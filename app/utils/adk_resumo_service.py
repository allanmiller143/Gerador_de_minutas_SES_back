from __future__ import annotations

import json
import logging
import os
import re
import time
import unicodedata
from typing import List, Optional

from dotenv import load_dotenv
from google import genai
from google.adk import Agent, Runner
from google.adk.models.google_llm import Gemini
from google.adk.sessions import InMemorySessionService
from google.cloud import storage
from google.genai import types
from pydantic import BaseModel, Field

from app.models import PromptConfig
from app.utils.rag_service import IncrementalRAG
from app.utils.support_document_service import SupportDocumentService

load_dotenv()
logger = logging.getLogger(__name__)

DEFAULT_ADK_MODEL = (os.getenv("ADK_MODEL") or "").strip() or "gemini-2.5-flash"


def _normalize_text(s: str) -> str:
    if not s:
        return ""
    normalized = unicodedata.normalize("NFKD", s)
    return "".join(c for c in normalized if not unicodedata.combining(c)).lower()


# =====================================================================
# Higienização Clínica Pré-ADK: Detecção determinística de CIDs
# =====================================================================
CID10_PATTERN = re.compile(r"\b([A-Z]\d{2}(?:\.\d{1,2})?)\b")
_NON_CID_CONTEXT_KEYWORDS = (
    "CEP", "CNPJ", "CPF", "CNS", "CNES", "RQE", "CRM", "SEI", "TEL", "FONE", "RAMAL", "PROCESSO", "PORTARIA", "LEI"
)


def extract_cid_candidates(text: str) -> set[str]:
    """Varre deterministamente o texto em busca de códigos CID-10 genuínos,
    eliminando falsos positivos originados por CRM, CEP, CNPJ, CNS, etc."""
    if not text:
        return set()
    source = str(text)
    candidates = set()
    for match in CID10_PATTERN.finditer(source):
        start = match.start()
        window = source[max(0, start - 20):start].upper()
        if any(keyword in window for keyword in _NON_CID_CONTEXT_KEYWORDS):
            continue
        candidates.add(match.group(1).upper())
    return candidates


# =====================================================================
# Pydantic Schemas para Structured Output do ADK
# =====================================================================
class ItemMedicamentoDetalhadoModel(BaseModel):
    medicamento: str = Field(
        description="Nome ou princípio ativo do medicamento solicitado (ex.: 'Adalimumabe', 'Omeprazol').",
    )
    apresentacao: str = Field(
        default="",
        description="Concentração e forma farmacêutica exata do produto (ex.: 'comprimido 20mg', 'solução injetável 40mg/0,8mL'). Não misturar posologia.",
    )
    posologia: str = Field(
        default="",
        description="Instrução posológica de administração prescrita (ex.: '1 comp VO 12/12h', '1 seringa SC a cada 14 dias').",
    )
    origem_documental: str = Field(
        default="",
        description="Documento comprobatório da prescrição ativa nos autos (ex.: 'Receituário fl. 3', 'Laudo LME fl. 5').",
    )
    apresentacao_padronizada_sus: bool = Field(
        default=False,
        description="True se a apresentação/concentração exata solicitada estiver padronizada na RENAME/REESME/PCDT para dispensação.",
    )
    status_dispensacao: str = Field(
        default="Não Dispensado",
        description="'Componente Básico', 'Componente Especializado - Aprovado', 'Componente Especializado - Negado' ou 'Não Dispensado'.",
    )
    justificativa_item: str = Field(
        default="",
        description="Justificativa técnica focada na apresentação. Se não padronizada, 'A apresentação e dosagem solicitadas não estão padronizadas para fornecimento.'",
    )


class ResumoProcessoModel(BaseModel):
    tipo_demanda: str = Field(
        default="não informado",
        description="Tipo de solicitação administrativa (ex.: Medicamento, Insumo, Nutrição, Procedimento).",
    )
    medicamento_solicitado: str = Field(
        default="não informado",
        description="Nome do(s) medicamento(s) solicitado(s) com dosagem e posologia se disponível.",
    )
    medicamentos_detalhados: List[ItemMedicamentoDetalhadoModel] = Field(
        default_factory=list,
        description="Lista discriminada de cada medicamento com separação rigorosa de apresentação (concentração/forma) e posologia.",
    )
    cid_informado: str = Field(
        default="não informado",
        description="Código(s) CID-10 informado(s) no laudo/prescrição (ex.: K50.0).",
    )
    diagnostico_informado: str = Field(
        default="não informado",
        description="Diagnóstico clínico textual descrito pelo médico assistente.",
    )
    objetivo_da_solicitacao: str = Field(
        default="não informado",
        description="Objetivo clínico informado para o tratamento (ex.: indução de remissão, controle de dor).",
    )


class ConfrontoDocumentacaoSuporteModel(BaseModel):
    cid_validado: bool = Field(
        default=False,
        description="True se o CID-10 foi formalmente identificado e validado nas normas/diretrizes.",
    )
    medicamento_contemplado_para_o_cid: str = Field(
        default="indeterminado",
        description="Indica se o medicamento é contemplado no SUS para a doença: 'sim', 'não', 'com ressalvas' ou 'indeterminado'.",
    )
    apresentacao_padronizada: bool = Field(
        default=False,
        description="True se a concentração e forma farmacêutica solicitadas constam literalmente entre as apresentações padronizadas no SUS.",
    )
    outros_cids_contemplados_para_o_medicamento: List[str] = Field(
        default_factory=list,
        description="Lista de outros códigos CID-10 e patologias para os quais o medicamento pleiteado é fornecido no SUS caso não seja contemplado para o CID atual.",
    )
    itens_avaliados: List[ItemMedicamentoDetalhadoModel] = Field(
        default_factory=list,
        description="Detalhamento da avaliação técnica de cada apresentação/dosagem individualmente.",
    )
    observacoes: List[str] = Field(
        default_factory=list,
        description="Observações técnicas sobre os critérios do PCDT, linha de cuidado ou diretriz estadual.",
    )


class InsumoParecerModel(BaseModel):
    conclusao_tecnica_sugerida: str = Field(
        default="Conclusão técnica não informada.",
        description="Conclusão preliminar sugerida pelo avaliador farmacêutico.",
    )
    fundamentos: List[str] = Field(
        default_factory=list,
        description="Fundamentos técnicos e legais baseados nas normas do SUS, PCDT, RENAME ou REESME.",
    )
    alternativas_orientaveis: List[str] = Field(
        default_factory=list,
        description="Alternativas terapêuticas disponíveis na RENAME/REESME/SUS para o caso.",
    )
    pendencias_documentais: List[str] = Field(
        default_factory=list,
        description="Documentos clínicos ou exames faltantes exigidos pelo PCDT correspondente.",
    )
    necessita_revisao_humana: bool = Field(
        default=True,
        description="Deve ser sempre True para garantir revisão obrigatória do farmacêutico.",
    )
    nivel_confianca: str = Field(
        default="médio",
        description="Nível de confiança da análise ('alto', 'médio' ou 'baixo').",
    )


class AdkAnaliseOutput(BaseModel):
    resumo_processo: ResumoProcessoModel
    evidencias_clinicas_do_processo: List[str] = Field(
        default_factory=list,
        description="Fatos clínicos comprovados no processo (ex.: refratariedade prévia, tempo de doença, exames).",
    )
    confronto_documentacao_suporte: ConfrontoDocumentacaoSuporteModel
    insumo_parecer: InsumoParecerModel
    fontes_consultadas: List[str] = Field(
        default_factory=list,
        description="Nomes dos arquivos, portarias ou protocolos consultados.",
    )
    complexidade: Optional[str] = Field(
        default="Média",
        description="Classificação da complexidade do caso ('Baixa', 'Média' ou 'Alta').",
    )
    complexidade_justificativa: Optional[str] = Field(
        default="",
        description="Justificativa concisa da classificação de complexidade.",
    )
    minuta_parecer: str = Field(
        default="",
        description="Minuta formal completa do parecer técnico da SES-PE (com cabeçalho, relatório, fundamentação e conclusão preliminar).",
    )


# =====================================================================
# Manifesto Clínico Dinâmico - Fallback para Ambiente de Testes / Offline
# Em produção, a fonte de verdade é sincronizada via IncrementalRAG.get_or_build_manifest()
# =====================================================================

# =====================================================================
# Mapeamento de CIDs e Indicações do SUS por Princípio Ativo
# =====================================================================
_FALLBACK_MANIFEST: dict[str, Any] = {
    "medication_to_indications": {
        "adalimumabe": [
            {"cid": "K50", "patologia": "Doença de Crohn"},
            {"cid": "K51", "patologia": "Retocolite Ulcerativa"},
            {"cid": "M05/M06", "patologia": "Artrite Reumatoide"},
            {"cid": "M08", "patologia": "Artrite Idiopática Juvenil"},
            {"cid": "M45", "patologia": "Espondilite Ancilosante"},
            {"cid": "L40", "patologia": "Psoríase e Artrite Psoriásica"},
            {"cid": "L73.2", "patologia": "Hidradenite Supurativa"},
            {"cid": "H20/H30", "patologia": "Uveítes não Infecciosas"},
        ],
        "infliximabe": [
            {"cid": "K50", "patologia": "Doença de Crohn"},
            {"cid": "K51", "patologia": "Retocolite Ulcerativa"},
            {"cid": "M05/M06", "patologia": "Artrite Reumatoide"},
            {"cid": "M45", "patologia": "Espondilite Ancilosante"},
            {"cid": "L40", "patologia": "Psoríase e Artrite Psoriásica"},
        ],
        "etanercepte": [
            {"cid": "M05/M06", "patologia": "Artrite Reumatoide"},
            {"cid": "M08", "patologia": "Artrite Idiopática Juvenil"},
            {"cid": "M45", "patologia": "Espondilite Ancilosante"},
            {"cid": "L40", "patologia": "Psoríase e Artrite Psoriásica"},
        ],
        "vedolizumabe": [
            {"cid": "K50", "patologia": "Doença de Crohn"},
            {"cid": "K51", "patologia": "Retocolite Ulcerativa"},
        ],
        "dupilumabe": [
            {"cid": "L20", "patologia": "Dermatite Atópica"},
            {"cid": "J45", "patologia": "Asma grave"},
        ],
        "azatioprina": [
            {"cid": "K50", "patologia": "Doença de Crohn"},
            {"cid": "K51", "patologia": "Retocolite Ulcerativa"},
            {"cid": "M05/M06", "patologia": "Artrite Reumatoide"},
        ],
        "mesalazina": [
            {"cid": "K50", "patologia": "Doença de Crohn"},
            {"cid": "K51", "patologia": "Retocolite Ulcerativa"},
        ],
    },
    "cid_to_pathology": {
        "K50": "Doença de Crohn",
        "K51": "Retocolite Ulcerativa",
        "M05": "Artrite Reumatoide",
        "M06": "Artrite Reumatoide",
        "M08": "Artrite Idiopática Juvenil",
        "M45": "Espondilite Ancilosante",
        "L40": "Psoríase",
        "L20": "Dermatite Atópica",
        "G35": "Esclerose Múltipla",
        "J45": "Asma",
    },
    "cid_to_files": {
        "K50": ["base_conhecimento/protocolos_clinicos/Doença de Crohn.pdf"],
        "K51": ["base_conhecimento/protocolos_clinicos/Retocolite Ulcerativa.pdf"],
        "M05": ["base_conhecimento/protocolos_clinicos/Artrite Reumatoide.pdf"],
        "M06": ["base_conhecimento/protocolos_clinicos/Artrite Reumatoide.pdf"],
        "M08": ["base_conhecimento/protocolos_clinicos/Artrite Idiopática Juvenil (AIJ).pdf"],
        "M45": ["base_conhecimento/protocolos_clinicos/Espondilite Ancilosante.pdf"],
        "L40": ["base_conhecimento/protocolos_clinicos/Psoríase.pdf"],
        "L20": ["base_conhecimento/protocolos_clinicos/Dermatite Atópica.pdf"],
        "G35": ["base_conhecimento/protocolos_clinicos/Esclerose Múltipla.pdf"],
    },
    "medication_to_files": {
        "adalimumabe": [
            "base_conhecimento/protocolos_clinicos/Doença de Crohn.pdf",
            "base_conhecimento/protocolos_clinicos/Artrite Reumatoide.pdf",
            "base_conhecimento/protocolos_clinicos/Espondilite Ancilosante.pdf",
            "base_conhecimento/protocolos_clinicos/Psoríase.pdf",
        ],
        "dupilumabe": [
            "base_conhecimento/protocolos_clinicos/Dermatite Atópica.pdf",
        ],
    },
}

# =====================================================================
# Serviço Principal baseado no Google ADK
# =====================================================================
class AdkResumoService:
    def __init__(self, client: genai.Client | None = None):
        api_key = os.getenv("GEMINI_API_KEY")
        project = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCS_PROJECT_ID")
        location = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
        use_vertex = os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "true").lower() == "true"

        if client:
            self.client = client
        else:
            self.client = genai.Client(
                api_key=api_key,
                vertexai=use_vertex,
                project=project,
                location=location,
            )

        self.bucket_name = os.getenv("GCS_BUCKET_NAME", "ses-farmacia")
        self.knowledge_prefix = os.getenv("GCS_BUCKET_KNOWLEDGE_BASE", "base_conhecimento")

        # RAG incremental com cache de índices
        self.rag: IncrementalRAG | None = None
        try:
            if self.bucket_name:
                self.rag = IncrementalRAG(
                    genai_client=self.client,
                    bucket_name=self.bucket_name,
                    knowledge_prefix=self.knowledge_prefix,
                    index_prefix="rag-index/",
                )
        except Exception as e:
            logger.warning(f"Não foi possível inicializar IncrementalRAG: {e}")

    _cached_knowledge_blobs: List[str] | None = None
    _cache_timestamp: float = 0.0

    def _get_knowledge_blobs(self) -> List[str]:
        """Obtém os arquivos da base de conhecimento com cache em memória (TTL 10 min)."""
        now = time.time()
        if (
            AdkResumoService._cached_knowledge_blobs is not None
            and (now - AdkResumoService._cache_timestamp) < 600
        ):
            return AdkResumoService._cached_knowledge_blobs

        try:
            storage_client = storage.Client()
            bucket = storage_client.bucket(self.bucket_name)
            blobs = [
                b.name
                for b in bucket.list_blobs(prefix=self.knowledge_prefix)
                if not b.name.endswith("/")
            ]
            AdkResumoService._cached_knowledge_blobs = blobs
            AdkResumoService._cache_timestamp = now
            return blobs
        except Exception as exc:
            logger.warning(f"Erro ao listar arquivos da base de conhecimento: {exc}")
            return AdkResumoService._cached_knowledge_blobs or []

    @classmethod
    def _get_global_manifest(cls) -> dict:
        """Obtém o manifesto clínico dinâmico do GCS (com fallback seguro em offline/testes)."""
        bucket_name = os.getenv("GCS_BUCKET_NAME", "ses-farmacia")
        knowledge_prefix = os.getenv("GCS_BUCKET_KNOWLEDGE_BASE", "base_conhecimento")
        try:
            rag = IncrementalRAG(
                genai_client=None,
                bucket_name=bucket_name,
                knowledge_prefix=knowledge_prefix,
            )
            manifest = rag.get_or_build_manifest()
            if manifest and manifest.get("medication_to_indications"):
                return manifest
        except Exception:
            pass
        return _FALLBACK_MANIFEST

    def _get_manifest(self) -> dict:
        """Obtém o manifesto clínico dinâmico a partir da instância do RAG ou fallback global."""
        if self.rag:
            try:
                manifest = self.rag.get_or_build_manifest()
                if manifest and manifest.get("medication_to_indications"):
                    return manifest
            except Exception as e:
                logger.warning(f"Erro ao obter manifesto clínico do RAG: {e}")
        return self._get_global_manifest()

    def _find_matching_knowledge_files(
        self, medicamento: str = "", cid: str = "", diagnostico: str = ""
    ) -> List[str]:
        """Localiza com precisão os protocolos clínicos (PCDTs) e normas oficiais relevantes
        combinando o manifesto clínico dinâmico e pontuação contextual."""
        blobs = self._get_knowledge_blobs()
        if not blobs:
            return []

        manifest = self._get_manifest()
        cid_to_files = manifest.get("cid_to_files", {})
        cid_to_pathology = manifest.get("cid_to_pathology", {})
        med_to_files = manifest.get("medication_to_files", {})
        med_to_ind = manifest.get("medication_to_indications", {})

        search_phrases: set[str] = set()

        # 1. Expansão taxonômica dinâmica a partir do CID-10
        cid_norm = _normalize_text(cid).replace(".", "").strip()
        if cid_norm:
            prefix = cid_norm[:3].upper()
            if prefix in cid_to_pathology:
                search_phrases.add(_normalize_text(cid_to_pathology[prefix]))
            search_phrases.add(cid_norm)
            search_phrases.add(prefix.lower())

        # 2. Expansão taxonômica dinâmica a partir do medicamento
        med_norm = _normalize_text(medicamento)
        if med_norm:
            for drug_key, ind_list in med_to_ind.items():
                if drug_key in med_norm or med_norm in drug_key:
                    for ind in ind_list:
                        search_phrases.add(_normalize_text(ind["patologia"]))
            for part in med_norm.split():
                if len(part) >= 4 and part not in ("para", "como", "mais", "dose", "comprimido", "ampola", "frasco"):
                    search_phrases.add(part)

        # 3. Termos clínicos do diagnóstico informado
        diag_norm = _normalize_text(diagnostico)
        if diag_norm:
            search_phrases.add(diag_norm)
            for part in diag_norm.split():
                if len(part) >= 4 and part not in ("doenca", "sindrome", "tipo", "grau", "fase", "cronica", "aguda"):
                    search_phrases.add(part)

        scored: list[tuple[int, str]] = []
        reference_files: list[str] = []

        # Adiciona arquivos mapeados diretamente no manifesto com pontuação alta
        direct_files = set()
        if cid_norm:
            prefix = cid_norm[:3].upper()
            for f in cid_to_files.get(prefix, []):
                direct_files.add(f)
        if med_norm:
            for drug_key, f_list in med_to_files.items():
                if drug_key in med_norm or med_norm in drug_key:
                    for f in f_list:
                        direct_files.add(f)

        for blob_name in blobs:
            norm_name = _normalize_text(blob_name)

            # RENAME e REESME são normas de referência fundamentais de dispensação
            if "rename" in norm_name or "reesme" in norm_name:
                reference_files.append(blob_name)
                continue

            score = 0
            # Se mapeado diretamente pelo manifesto clínico dinâmico
            if blob_name in direct_files or any(blob_name.endswith(df.split("/")[-1]) for df in direct_files):
                score += 15

            for phrase in search_phrases:
                if phrase and phrase in norm_name:
                    score += len(phrase.split()) * 3

            if score > 0:
                if "protocolos_clinicos" in norm_name:
                    score += 2
                elif "normas_tecnicas" in norm_name:
                    score += 1
                scored.append((score, blob_name))

        scored.sort(key=lambda x: x[0], reverse=True)
        top_specific = [item[1] for item in scored[:2]]

        reference_files.sort(reverse=True)
        top_references = reference_files[:2]

        selected = top_specific + [rf for rf in top_references if rf not in top_specific]
        return selected

    @classmethod
    def _get_cids_sus_info(cls, medicamento: str, cid: str = "", manifest: dict | None = None) -> str:
        """Retorna texto estruturado com todos os CIDs contemplados no SUS para o fármaco."""
        if not medicamento:
            return ""
        if manifest is None:
            manifest = cls._get_global_manifest()
        norm_med = _normalize_text(medicamento)
        med_map = manifest.get("medication_to_indications", {})
        for med_key, cids_list in med_map.items():
            if med_key in norm_med or norm_med in med_key:
                linhas = [f"  - CID {entry['cid']}: {entry['patologia']}" for entry in cids_list]
                return (
                    f"\n\n=== INDICAÇÕES E CIDs PADRONIZADOS NO SUS PARA {medicamento.upper()} ===\n"
                    f"O SUS contempla formalmente este princípio ativo para as seguintes patologias/CIDs:\n"
                    + "\n".join(linhas) + "\n"
                    f"DIRETRIZ OBRIGATÓRIA DE PARECER: Se o CID informado ({cid or 'N/I'}) não for contemplado no SUS para este insumo, "
                    f"você DEVE informar no parecer e no resumo TODOS os outros CIDs acima para os quais o insumo pode ser fornecido no SUS, "
                    f"além de relacionar todos os insumos e alternativas que PODEM ser fornecidos para o CID do paciente."
                )
        return ""

    def _consultar_protocolo_sus(
        self, medicamento: str, cid: str, diagnostico: str = ""
    ) -> str:
        """Ferramenta Tool para o agente ADK: consulta normas, PCDT e RENAME sob demanda via busca vetorial."""
        cids_sus_info = self._get_cids_sus_info(medicamento=medicamento, cid=cid)

        if not self.rag:
            # Fallback para documento local se não houver bucket configurado
            local_ctx = SupportDocumentService().build_context(max_trechos_suporte=6)
            return (local_ctx + cids_sus_info) if local_ctx else cids_sus_info

        try:
            files = self._find_matching_knowledge_files(
                medicamento=medicamento, cid=cid, diagnostico=diagnostico
            )
            if not files:
                # Tenta fallback local
                fallback = SupportDocumentService().build_context(max_trechos_suporte=6)
                base = fallback or "Nenhum documento específico encontrado para os termos informados."
                return base + cids_sus_info

            # Query semântica focada nos critérios de decisão técnica do SUS
            rag_query = (
                f"Critérios de diagnóstico, critérios de inclusão e exclusão no SUS, "
                f"esquema posológico e alternativas terapêuticas da RENAME para {medicamento} "
                f"no tratamento de {diagnostico or 'patologia'} (CID {cid}) no PCDT e normas técnicas."
            ).strip()

            rag_result = self.rag.rag(query=rag_query, selected_files=files, top_k=6)
            context = rag_result.get("context", "")

            if not context:
                fallback = SupportDocumentService().build_context(max_trechos_suporte=6)
                base = fallback or "Protocolos e normas localizados, mas nenhum trecho específico correspondeu à busca."
                return base + cids_sus_info

            fontes_consultadas = "\n".join(f"- {f.split('/')[-1]}" for f in files)
            return (
                f"=== DIRETRIZES TÉCNICAS E NORMAS OFICIAIS DO SUS (PCDT / RENAME / REESME) ===\n"
                f"Protocolos e normas consultados:\n{fontes_consultadas}\n\n"
                f"Trechos normativos recuperados via busca vetorial:\n{context}"
                f"{cids_sus_info}"
            )
        except Exception as e:
            logger.warning(f"Erro ao executar tool consultar_protocolo_sus: {e}")
            fallback = SupportDocumentService().build_context(max_trechos_suporte=6)
            base = fallback or f"Erro ao consultar normas oficiais do SUS: {e}"
            return base + cids_sus_info

    def generate_resumo(
        self,
        process_text: str,
        support_context: str = "",
        model: str = DEFAULT_ADK_MODEL,
        include_minuta: bool = True,
        prompt_key: str = "resumo_default",
        numero_sei: str | None = None,
    ) -> dict:
        """Executa a análise do processo via Google ADK com Tool Calling e Structured Output."""
        # Tool vinculada ao agente
        def consultar_normas_sus(
            medicamento: str, cid: str, diagnostico: str = ""
        ) -> str:
            """Consulta as diretrizes oficiais do SUS (PCDT, RENAME, REESME) para um medicamento e diagnóstico/CID via busca vetorial.
            Obrigatória para verificar conformidade técnica, critérios de inclusão/exclusão, posologia e alternativas.
            Args:
                medicamento: Nome do medicamento solicitado
                cid: Código CID-10 informado
                diagnostico: Diagnóstico clínico ou patologia
            """
            return self._consultar_protocolo_sus(
                medicamento=medicamento, cid=cid, diagnostico=diagnostico
            )

        # Higienização clínica pré-ADK: detecção determinística de CIDs genuínos
        cids_detectados = sorted(extract_cid_candidates(process_text))
        if cids_detectados:
            cids_info = f"CIDs-10 identificados e validados no texto do processo via OCR: {', '.join(cids_detectados)}"
        else:
            cids_info = "Nenhum código com formato estrito de CID-10 detectado diretamente no OCR."

        instruction = (
            "Você é um farmacêutico avaliador da Secretaria de Saúde do Estado de Pernambuco (SES-PE).\n"
            "Sua análise é estritamente técnica, preliminar e imparcial.\n\n"
            "DIRETRIZES FUNDAMENTAIS:\n"
            "1. HIGIENIZAÇÃO CLÍNICA E VALIDAÇÃO DE PRESCRIÇÃO:\n"
            "   - Considere APENAS medicamentos com prescrição médica ativa, laudo circunstanciado ou receituário comprovado nos autos. Descarte menções soltas em anamnese/histórico clínico narrativo ('relata uso prévio de...', 'já fez tratamento com...') que não constituam o objeto da solicitação atual.\n"
            "   - Descarte siglas de classes terapêuticas genéricas (ex.: TARV, ATB) e termos corrompidos ou ilegíveis de OCR.\n"
            "   - Utilize os códigos CID-10 comprovados na documentação clínica. NUNCA registre nem confunda números de CRM, CEP, CPF, CNS, CNES ou protocolo SEI como códigos de CID.\n"
            "2. OBRIGATORIEDADE DE CONSULTA ÀS NORMAS DO SUS:\n"
            "   - Você DEVE invocar a ferramenta `consultar_normas_sus` informando o medicamento pleiteado, código CID-10 e diagnóstico identificados.\n"
            "   - Se o processo contiver MÚLTIPLOS MEDICAMENTOS pleiteados (ex.: terapia combinada ou vários itens no receituário/LME), você DEVE invocar a ferramenta `consultar_normas_sus` individualmente para CADA medicamento/princípio ativo.\n"
            "   - Baseie sua fundamentação técnica EXCLUSIVAMENTE nos protocolos e normas retornados pela ferramenta (PCDT, RENAME e REESME), confrontando dosagem, linha de cuidado e alternativas para cada item.\n"
            "3. VALIDAÇÃO E DISCRIMINAÇÃO DE APRESENTAÇÃO/DOSAGEM FARMACÊUTICA:\n"
            "   - TODAS AS APRESENTAÇÕES E DOSAGENS SOLICITADAS: Ao mencionar as apresentações e dosagens dos insumos solicitados (em `medicamento_solicitado`, `medicamentos_detalhados`, relatório ou fundamentação), sempre INCLUIR TODAS as apresentações e dosagens solicitadas na prescrição/processo (forma farmacêutica e concentração completas de cada insumo, sem omissões).\n"
            "   - TODAS AS APRESENTAÇÕES E DOSAGENS DISPONÍVEIS NO SUS: Ao mencionar as apresentações e dosagens dos insumos dispensados, dispensáveis ou padronizados no SUS (seja para o próprio fármaco pleiteado ou para alternativas terapêuticas da RENAME/REESME/PCDT), INFORMAR TODAS as apresentações e dosagens oficialmente disponíveis e padronizadas no SUS (listar expressamente todas as formas e concentrações padronizadas).\n"
            "   - SEPARAÇÃO RIGOROSA: Separe a APRESENTAÇÃO (concentração e forma farmacêutica do produto, ex.: 'comprimido 20mg', 'solução injetável 40mg/0,8mL') da POSOLOGIA (instrução de uso/frequência, ex.: '1 comprimido a cada 12 horas'). NUNCA misture posologia no campo de apresentação.\n"
            "   - AVALIAÇÃO INDIVIDUAL POR ITEM/MEDICAMENTO: Avalie CADA medicamento e CADA apresentação/dosagem separadamente em `medicamentos_detalhados` e `itens_avaliados`. Se o pedido contiver múltiplos medicamentos, cada um deve constituir um item independente com seu próprio status de dispensação e sua própria justificativa técnica.\n"
            "   - REGRA DE NÃO PADRONIZAÇÃO DE DOSAGEM/FORMA: Se o princípio ativo constar nas normas do SUS, mas a CONCENTRAÇÃO ou FORMA FARMACÊUTICA solicitada não estiver entre as padronizadas da RENAME/REESME/PCDT:\n"
            "     * O campo `apresentacao_padronizada_sus` do item DEVE ser False.\n"
            "     * O status dessa apresentação DEVE ser 'Não Dispensado'.\n"
            "     * A justificativa técnica DEVE registrar formalmente: 'A apresentação e dosagem solicitadas não estão padronizadas para fornecimento.'\n"
            "     * Relacione expressamente no campo de alternativas e na fundamentação TODAS as apresentações e concentrações oficiais que constam padronizadas no SUS para aquele fármaco.\n"
            "4. MEDICAMENTO CONTEMPLADO NO SUS PARA OUTROS CIDs (MAS NÃO PARA O CID INFORMADO):\n"
            "   - Em caso de negativa/impossibilidade de fornecimento de um insumo pleiteado para o CID informado, OU caso o medicamento seja contemplado no SUS para outras patologias mas NÃO para o CID informado pelo paciente:\n"
            "     * Você DEVE INFORMAR EXPRESSAMENTE no resumo (`outros_cids_contemplados_para_o_medicamento`, `observacoes`) e na fundamentação/conclusão da minuta TODOS OS OUTROS CIDs e patologias para os quais aquele medicamento pode ser fornecido no SUS.\n"
            "     * CONCOMITANTEMENTE, relacione todos os insumos e alternativas terapêuticas padronizados no SUS que PODEM ser dispensados para o CID informado pelo paciente.\n"
            "5. RELATÓRIO SINTÉTICO: Identifique e documente paciente, medicamentos pleiteados com TODAS as apresentações e dosagens solicitadas, CID/diagnóstico e prescrições.\n"
            "6. ANÁLISE TÉCNICA E FUNDAMENTAÇÃO: Realize o confronto das evidências clínicas com o PCDT/RENAME/REESME e critérios do SUS item a item.\n"
            "7. Nunca emita deferimento/indeferimento definitivo institucional.\n"
            "8. O campo `necessita_revisao_humana` deve ser SEMPRE True.\n"
        )

        if include_minuta:
            instruction += (
                "\n9. No campo `minuta_parecer`, elabore o PARECER TÉCNICO institucional com a ESTRUTURA OBRIGATÓRIA:\n"
                "   1. CABEÇALHO INSTITUCIONAL: Secretaria de Saúde de Pernambuco (SES-PE) / Assistência Farmacêutica.\n"
                f"   2. IDENTIFICAÇÃO DO PROCESSO: Processo SEI nº {numero_sei or 'N/I'}.\n"
                "   3. RELATÓRIO SINTÉTICO: Paciente, medicamento(s) pleiteado(s) incluindo TODAS as apresentações e dosagens solicitadas, CID/diagnóstico e prescrições discriminadas.\n"
                "   4. ANÁLISE TÉCNICA E FUNDAMENTAÇÃO: Confronto das evidências com o PCDT/RENAME/REESME e critérios do SUS:\n"
                "      - Discriminar e analisar CADA medicamento e cada apresentação/dosagem solicitada em tópicos ou itens individuais.\n"
                "      - Para cada insumo dispensado ou dispensável, informar expressamente TODAS as apresentações e dosagens disponíveis na RENAME/REESME.\n"
                "      - Se a concentração/forma solicitada não estiver padronizada no SUS, registrar expressamente: 'A apresentação e dosagem solicitadas não estão padronizadas para fornecimento.', informando todas as apresentações padronizadas disponíveis na RENAME/REESME.\n"
                "      - Caso o insumo pleiteado seja contemplado no SUS para outras patologias mas não para o CID informado (ou em caso de impossibilidade de fornecimento para o CID informado):\n"
                "        * Informar expressamente TODOS OS OUTROS CIDs e patologias para os quais aquele medicamento pleiteado pode ser fornecido pelo SUS.\n"
                "        * Relacionar todos os insumos e alternativas que PODEM ser dispensados pelo SUS para o CID informado pelo paciente.\n"
                "   5. CONCLUSÃO TÉCNICA SUGERIDA: Sugestão preliminar fundamentada para cada medicamento, orientações das alternativas e apresentações disponíveis no SUS, indicação dos outros CIDs atendidos pelo medicamento pleiteado, pendências documentais e necessidade expressa de validação humana final.\n"
                "   Não adicione introduções ou saudações fora do padrão formal do parecer.\n"
            )
        else:
            instruction += "\n8. O campo `minuta_parecer` pode ficar em branco ou conter uma síntese sucinta.\n"

        llm = Gemini(model=model, client=self.client)
        agent = Agent(
            name="AnalistaFarmaceuticoSES",
            model=llm,
            instruction=instruction,
            tools=[consultar_normas_sus],
            output_schema=AdkAnaliseOutput,
        )

        session_service = InMemorySessionService()
        runner = Runner(
            app_name="ses_farmacia_adk",
            agent=agent,
            session_service=session_service,
            auto_create_session=True,
        )

        prompt_input = (
            f"PROCESSO ADMINISTRATIVO SEI {numero_sei or ''}:\n\n"
            f"TRIAGEM CLÍNICA PRELIMINAR:\n"
            f"- {cids_info}\n\n"
            f"TEXTO DO PROCESSO:\n{process_text}"
        )
        if support_context:
            prompt_input += f"\n\nCONTEXTO DE SUPORTE PREEXISTENTE:\n{support_context[:3000]}"

        user_content = types.Content(
            role="user",
            parts=[types.Part.from_text(text=prompt_input)],
        )

        logger.info("[PIPELINE_MOTOR: GOOGLE_ADK] Agente ADK iniciando análise do processo SEI %s", numero_sei or "N/I")
        raw_output = ""
        for event in runner.run(
            user_id="ses_user",
            session_id=f"sei_{numero_sei or 'temp'}",
            new_message=user_content,
        ):
            if event.is_final_response():
                if event.message and event.message.parts:
                    for p in event.message.parts:
                        if p.text:
                            raw_output += p.text

        logger.info("[PIPELINE_MOTOR: GOOGLE_ADK] Agente ADK finalizou geração estruturada do processo SEI %s", numero_sei or "N/I")
        parsed_data = self._safe_parse_adk_output(raw_output, cids_detectados=cids_detectados)
        return parsed_data

    @staticmethod
    def _normalize_payload(payload: dict, cids_detectados: list[str] | None = None) -> dict:
        normalized = dict(payload) if isinstance(payload, dict) else {}
        normalized.setdefault("resumo_processo", {})
        normalized.setdefault("evidencias_clinicas_do_processo", [])
        normalized.setdefault("confronto_documentacao_suporte", {})
        normalized.setdefault("insumo_parecer", {})
        normalized.setdefault("fontes_consultadas", [])

        if not isinstance(normalized["resumo_processo"], dict):
            normalized["resumo_processo"] = {}
        if not isinstance(normalized["evidencias_clinicas_do_processo"], list):
            normalized["evidencias_clinicas_do_processo"] = []
        if not isinstance(normalized["confronto_documentacao_suporte"], dict):
            normalized["confronto_documentacao_suporte"] = {}
        if not isinstance(normalized["fontes_consultadas"], list):
            normalized["fontes_consultadas"] = []

        resumo_processo = normalized["resumo_processo"]
        resumo_processo.setdefault("tipo_demanda", "não informado")
        resumo_processo.setdefault("medicamento_solicitado", "não informado")
        resumo_processo.setdefault("cid_informado", "não informado")
        resumo_processo.setdefault("diagnostico_informado", "não informado")
        resumo_processo.setdefault("objetivo_da_solicitacao", "não informado")

        # Higienização e validação determinística de CIDs no payload
        cid_atual = str(resumo_processo.get("cid_informado", "")).strip()
        # Se contiver falsos positivos conhecidos (CRM, CEP, CPF, etc.), descarta
        if any(kw in cid_atual.upper() for kw in ("CRM", "CEP", "CNPJ", "CPF", "CNES", "SEI")):
            if cids_detectados:
                resumo_processo["cid_informado"] = ", ".join(cids_detectados)
            else:
                resumo_processo["cid_informado"] = "não informado"
        elif (not cid_atual or cid_atual.lower() in ("não informado", "nao informado", "n/i", "indeterminado")) and cids_detectados:
            resumo_processo["cid_informado"] = ", ".join(cids_detectados)

        # Normalização dos medicamentos detalhados (separação de apresentação e posologia)
        resumo_processo.setdefault("medicamentos_detalhados", [])
        if not isinstance(resumo_processo["medicamentos_detalhados"], list):
            resumo_processo["medicamentos_detalhados"] = []

        confronto = normalized["confronto_documentacao_suporte"]
        confronto.setdefault("cid_validado", bool(cids_detectados))
        confronto.setdefault("medicamento_contemplado_para_o_cid", "indeterminado")
        confronto.setdefault("apresentacao_padronizada", False)
        confronto.setdefault("itens_avaliados", [])
        confronto.setdefault("observacoes", [])
        if not isinstance(confronto["observacoes"], list):
            confronto["observacoes"] = []
        if not isinstance(confronto["itens_avaliados"], list):
            confronto["itens_avaliados"] = []

        # Sincronizar medicamentos_detalhados e itens_avaliados se um dos dois estiver vazio
        if not confronto["itens_avaliados"] and resumo_processo["medicamentos_detalhados"]:
            confronto["itens_avaliados"] = [dict(item) for item in resumo_processo["medicamentos_detalhados"] if isinstance(item, dict)]
        elif not resumo_processo["medicamentos_detalhados"] and confronto["itens_avaliados"]:
            resumo_processo["medicamentos_detalhados"] = [dict(item) for item in confronto["itens_avaliados"] if isinstance(item, dict)]

        # Normalizar cada item avaliado e garantir frase obrigatória quando não padronizado
        itens_normalizados = []
        for item in confronto["itens_avaliados"]:
            if not isinstance(item, dict):
                continue
            item_norm = dict(item)
            item_norm.setdefault("medicamento", "não informado")
            item_norm.setdefault("apresentacao", "")
            item_norm.setdefault("posologia", "")
            item_norm.setdefault("origem_documental", "")

            padronizado = bool(item_norm.get("apresentacao_padronizada_sus", False))
            item_norm["apresentacao_padronizada_sus"] = padronizado

            if not padronizado:
                justificativa = item_norm.get("justificativa_item", "")
                if not justificativa or "não estão padronizadas" not in justificativa:
                    item_norm["justificativa_item"] = "A apresentação e dosagem solicitadas não estão padronizadas para fornecimento."
                if item_norm.get("status_dispensacao") in ("Componente Especializado - Aprovado", "Componente Básico - Aprovado", "", None):
                    item_norm["status_dispensacao"] = "Não Dispensado"
            else:
                item_norm.setdefault("justificativa_item", "Apresentação e dosagem em conformidade com as diretrizes do SUS.")
                item_norm.setdefault("status_dispensacao", "Componente Especializado - Aprovado")

            itens_normalizados.append(item_norm)

        confronto["itens_avaliados"] = itens_normalizados
        resumo_processo["medicamentos_detalhados"] = [dict(it) for it in itens_normalizados]

        if itens_normalizados:
            confronto["apresentacao_padronizada"] = all(it["apresentacao_padronizada_sus"] for it in itens_normalizados)
            if not confronto["apresentacao_padronizada"]:
                padrao_frase = "A apresentação e dosagem solicitadas não estão padronizadas para fornecimento."
                if not any(padrao_frase.lower() in str(obs).lower() for obs in confronto["observacoes"]):
                    confronto["observacoes"].append(padrao_frase)

        # Normalização e garantia de outros CIDs contemplados no SUS para o medicamento
        confronto.setdefault("outros_cids_contemplados_para_o_medicamento", [])
        if not isinstance(confronto["outros_cids_contemplados_para_o_medicamento"], list):
            confronto["outros_cids_contemplados_para_o_medicamento"] = []

        med_texto = _normalize_text(resumo_processo.get("medicamento_solicitado", ""))
        cid_atual = _normalize_text(resumo_processo.get("cid_informado", ""))
        meds_para_checar = [med_texto]
        for it in confronto["itens_avaliados"]:
            if isinstance(it, dict) and it.get("medicamento"):
                meds_para_checar.append(_normalize_text(it["medicamento"]))

        manifest = AdkResumoService._get_global_manifest()
        med_indications = manifest.get("medication_to_indications", {})

        outros_cids_encontrados = list(confronto["outros_cids_contemplados_para_o_medicamento"])
        for med_item in meds_para_checar:
            if not med_item:
                continue
            for med_key, cids_list in med_indications.items():
                if med_key in med_item or med_item in med_key:
                    for entry in cids_list:
                        entry_cid_norm = _normalize_text(entry["cid"])
                        if entry_cid_norm not in cid_atual:
                            label = f"CID {entry['cid']} ({entry['patologia']})"
                            if label not in outros_cids_encontrados:
                                outros_cids_encontrados.append(label)

        confronto["outros_cids_contemplados_para_o_medicamento"] = outros_cids_encontrados

        nao_contemplado_cid = (
            confronto.get("medicamento_contemplado_para_o_cid") in ("não", "nao", "com ressalvas")
            or any(it.get("status_dispensacao") == "Não Dispensado" for it in confronto["itens_avaliados"])
        )
        if nao_contemplado_cid and outros_cids_encontrados:
            obs_texto = f"O insumo pleiteado é padronizado no SUS para outras patologias: {', '.join(outros_cids_encontrados)}."
            if not any("padronizado no sus para outras patologias" in str(o).lower() for o in confronto["observacoes"]):
                confronto["observacoes"].append(obs_texto)

        insumo = normalized["insumo_parecer"]
        if not isinstance(insumo, dict):
            insumo = {}
        insumo.setdefault("conclusao_tecnica_sugerida", "Conclusão técnica não informada.")
        insumo.setdefault("fundamentos", [])
        insumo.setdefault("alternativas_orientaveis", [])
        insumo.setdefault("pendencias_documentais", [])
        insumo.setdefault("necessita_revisao_humana", True)
        insumo.setdefault("nivel_confianca", "não informado")
        for key in ("fundamentos", "alternativas_orientaveis", "pendencias_documentais"):
            if not isinstance(insumo[key], list):
                insumo[key] = []
        normalized["insumo_parecer"] = insumo

        # Compatibilidade com chaves de minuta
        minuta = normalized.get("minuta_parecer") or normalized.get("minuta") or ""
        normalized["minuta"] = minuta
        normalized["minuta_parecer"] = minuta

        return normalized

    @staticmethod
    def _safe_parse_adk_output(raw_text: str, cids_detectados: list[str] | None = None) -> dict:
        """Converte com segurança o output do modelo para o schema esperado pelo frontend."""
        if not raw_text:
            return AdkResumoService._normalize_payload({}, cids_detectados=cids_detectados)

        cleaned = raw_text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if cleaned.startswith("json"):
                cleaned = cleaned[4:].strip()

        try:
            data = json.loads(cleaned)
            if isinstance(data, dict):
                return AdkResumoService._normalize_payload(data, cids_detectados=cids_detectados)
        except Exception:
            pass

        fallback = {
            "resumo_processo": {"tipo_demanda": "indefinido"},
            "evidencias_clinicas_do_processo": [cleaned[:500]],
            "confronto_documentacao_suporte": {
                "cid_validado": False,
                "medicamento_contemplado_para_o_cid": "indeterminado",
                "observacoes": ["Resposta gerada sem parsing JSON completo."],
            },
            "insumo_parecer": {
                "conclusao_tecnica_sugerida": "Revisão técnica necessária.",
                "fundamentos": [],
                "alternativas_orientaveis": [],
                "pendencias_documentais": [],
                "necessita_revisao_humana": True,
                "nivel_confianca": "baixo",
            },
            "fontes_consultadas": [],
        }
        return AdkResumoService._normalize_payload(fallback, cids_detectados=cids_detectados)

    def generate_minuta_only(
        self,
        resumo_tecnico_json: str,
        model: str = DEFAULT_ADK_MODEL,
        numero_sei: str | None = None,
    ) -> str:
        """Gera de forma ágil apenas a minuta de parecer institucional usando o resumo estruturado pré-existente."""
        instruction = (
            "Você é um farmacêutico avaliador da Secretaria de Saúde do Estado de Pernambuco (SES-PE).\n"
            "Elabore um PARECER TÉCNICO institucional com base estrita no resumo técnico fornecido.\n\n"
            "DIRETRIZES FUNDAMENTAIS DE ELABORAÇÃO:\n"
            "- TODAS AS APRESENTAÇÕES E DOSAGENS SOLICITADAS: Sempre incluir e discriminar TODAS as apresentações e dosagens solicitadas na prescrição/processo.\n"
            "- TODAS AS APRESENTAÇÕES E DOSAGENS DISPONÍVEIS: Sempre que mencionar insumos dispensados ou dispensáveis no SUS, informar TODAS as apresentações e dosagens oficialmente disponíveis na RENAME/REESME/PCDT.\n"
            "- NÃO PADRONIZAÇÃO DE DOSAGEM/FORMA: Se a concentração/forma solicitada não for padronizada no SUS, registrar expressamente: 'A apresentação e dosagem solicitadas não estão padronizadas para fornecimento.', orientando todas as apresentações padronizadas disponíveis.\n"
            "- MEDICAMENTO CONTEMPLADO NO SUS PARA OUTROS CIDs (MAS NÃO PARA O CID INFORMADO): Caso o medicamento pleiteado seja contemplado no SUS para outras patologias mas não para o CID informado (ou em caso de impossibilidade de fornecimento para o CID informado):\n"
            "  * Informar OBRIGATORIAMENTE TODOS OS OUTROS CIDs e patologias para os quais aquele medicamento pleiteado pode ser fornecido no SUS.\n"
            "  * CONCOMITANTEMENTE, relacionar todos os insumos e alternativas que PODEM ser dispensados pelo SUS para o CID informado pelo paciente.\n\n"
            "ESTRUTURA OBRIGATÓRIA:\n"
            "1. CABEÇALHO INSTITUCIONAL: Secretaria de Saúde de Pernambuco (SES-PE) / Assistência Farmacêutica.\n"
            f"2. IDENTIFICAÇÃO DO PROCESSO: Processo SEI nº {numero_sei or 'N/I'}.\n"
            "3. RELATÓRIO SINTÉTICO: Paciente, medicamento(s) pleiteado(s) incluindo TODAS as apresentações e dosagens solicitadas, CID/diagnóstico e prescrições discriminadas.\n"
            "4. ANÁLISE TÉCNICA E FUNDAMENTAÇÃO: Confronto das evidências com o PCDT/RENAME/REESME e critérios do SUS. Discriminar cada medicamento e apresentação em tópicos separados. Informar todas as apresentações padronizadas disponíveis no SUS. Se a concentração/forma não estiver padronizada, registrar a frase padrão obrigatória. Se o medicamento for fornecido no SUS para outros CIDs mas não para o CID atual, relacionar todos esses outros CIDs e listar todas as alternativas padronizadas para o CID do paciente.\n"
            "5. CONCLUSÃO TÉCNICA SUGERIDA: Sugestão preliminar fundamentada item a item, orientações das alternativas e apresentações no SUS, pendências documentais e necessidade expressa de validação humana final.\n"
            "Não adicione introduções ou saudações fora do padrão formal do parecer."
        )

        llm = Gemini(model=model, client=self.client)
        agent = Agent(
            name="RedatorMinutaSES",
            model=llm,
            instruction=instruction,
        )

        runner = Runner(
            app_name="ses_farmacia_minuta",
            agent=agent,
            session_service=InMemorySessionService(),
            auto_create_session=True,
        )

        user_content = types.Content(
            role="user",
            parts=[types.Part.from_text(text=f"RESUMO TÉCNICO DO CASO:\n{resumo_tecnico_json}")],
        )

        minuta_text = ""
        for event in runner.run(
            user_id="ses_user",
            session_id=f"minuta_{numero_sei or 'temp'}",
            new_message=user_content,
        ):
            if event.is_final_response():
                if event.message and event.message.parts:
                    for p in event.message.parts:
                        if p.text:
                            minuta_text += p.text

        return minuta_text.strip()
