from __future__ import annotations

import json
import logging
import os
import re
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
# Pydantic Schemas para Structured Output do ADK
# =====================================================================
class ResumoProcessoModel(BaseModel):
    tipo_demanda: str = Field(
        default="não informado",
        description="Tipo de solicitação administrativa (ex.: Medicamento, Insumo, Nutrição, Procedimento).",
    )
    medicamento_solicitado: str = Field(
        default="não informado",
        description="Nome do(s) medicamento(s) solicitado(s) com dosagem e posologia se disponível.",
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
        default="Médio",
        description="Classificação da complexidade do caso com base no SUS: 'Fácil' (todos no SUS), 'Médio' (misto/parcial) ou 'Difícil' (nenhum no SUS).",
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

    def _find_matching_knowledge_files(
        self, medicamento: str = "", cid: str = "", diagnostico: str = ""
    ) -> List[str]:
        """Localiza rapidamente arquivos relevantes na base de conhecimento sem chamadas lentas de IA."""
        try:
            storage_client = storage.Client()
            bucket = storage_client.bucket(self.bucket_name)
            blobs = [
                b.name
                for b in bucket.list_blobs(prefix=self.knowledge_prefix)
                if not b.name.endswith("/")
            ]
        except Exception as exc:
            logger.warning(f"Erro ao listar arquivos da base de conhecimento: {exc}")
            return []

        search_terms = set()
        for term_source in (diagnostico, medicamento, cid):
            if not term_source:
                continue
            normalized_terms = _normalize_text(term_source).split()
            for t in normalized_terms:
                if len(t) >= 4 and t not in (
                    "para",
                    "como",
                    "pelo",
                    "pela",
                    "mais",
                    "onde",
                    "qual",
                    "medicamento",
                    "solicitado",
                ):
                    search_terms.add(t)

        matched: List[tuple[int, str]] = []
        reference_files: List[str] = []

        for blob_name in blobs:
            filename = blob_name.split("/")[-1]
            norm_name = _normalize_text(filename)

            if "rename" in norm_name or "reesme" in norm_name:
                reference_files.append(blob_name)
                continue

            # Prioriza arquivo específico da patologia ou medicamento
            score = sum(1 for term in search_terms if term in norm_name)
            if score > 0:
                matched.append((score, blob_name))

        matched.sort(key=lambda x: x[0], reverse=True)
        if matched:
            # Prioriza os 2 melhores protocolos específicos da patologia/medicamento
            top_matched = [item[1] for item in matched[:2]]
        else:
            # Caso não haja protocolo específico, recorre a referências gerais
            top_matched = reference_files[:2]

        return top_matched

    def _consultar_protocolo_sus(
        self, medicamento: str, cid: str, diagnostico: str = ""
    ) -> str:
        """Ferramenta Tool para o agente ADK: consulta normas, PCDT e RENAME sob demanda."""
        if not self.rag:
            # Fallback para documento local se não houver bucket configurado
            return SupportDocumentService().build_context(max_trechos_suporte=6)

        try:
            files = self._find_matching_knowledge_files(
                medicamento=medicamento, cid=cid, diagnostico=diagnostico
            )
            if not files:
                # Tenta fallback local
                fallback = SupportDocumentService().build_context(max_trechos_suporte=6)
                return fallback or "Nenhum documento específico encontrado para os termos informados."

            query = f"{medicamento} {cid} {diagnostico}".strip()
            rag_result = self.rag.rag(query=query, selected_files=files, top_k=4)
            context = rag_result.get("context", "")

            if not context:
                return "Protocolos localizados, mas nenhum trecho específico correspondeu à busca."

            return f"DIRETRIZES TÉCNICAS E PCDT ENCONTRADOS:\n{context}"
        except Exception as e:
            logger.warning(f"Erro ao executar tool consultar_protocolo_sus: {e}")
            return SupportDocumentService().build_context(max_trechos_suporte=6)

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
            """Consulta as diretrizes oficiais do SUS (PCDT, RENAME, REESME) para um medicamento e diagnóstico/CID.
            Use esta ferramenta sempre que identificar o medicamento e o CID para verificar a conformidade técnica.
            Args:
                medicamento: Nome do medicamento solicitado
                cid: Código CID-10 informado
                diagnostico: Diagnóstico clínico ou patologia
            """
            return self._consultar_protocolo_sus(
                medicamento=medicamento, cid=cid, diagnostico=diagnostico
            )

        instruction = (
            "Você é um farmacêutico avaliador da Secretaria de Saúde do Estado de Pernambuco (SES-PE).\n"
            "Sua análise é estritamente técnica, preliminar e imparcial.\n\n"
            "DIRETRIZES FUNDAMENTAIS:\n"
            "1. Analise o pedido presente no TEXTO DO PROCESSO SEI.\n"
            "2. Utilize a ferramenta `consultar_normas_sus` com o medicamento e CID/diagnóstico identificados para obter os critérios oficiais de fornecimento.\n"
            "3. RELATÓRIO SINTÉTICO: Identifique e documente paciente, medicamento pleiteado, CID/diagnóstico e prescrição.\n"
            "4. ANÁLISE TÉCNICA E FUNDAMENTAÇÃO: Realize o confronto das evidências clínicas com o PCDT/RENAME/REESME e critérios do SUS. Caso a análise aponte para indeferimento pela impossibilidade de fornecer o insumo para o CID informado, relacione todos os insumos que podem ser dispensados pelo SUS para aquele CID.\n"
            "5. Nunca emita deferimento/indeferimento definitivo institucional.\n"
            "6. O campo `necessita_revisao_humana` deve ser SEMPRE True.\n"
        )

        if include_minuta:
            instruction += (
                "\n7. No campo `minuta_parecer`, elabore o PARECER TÉCNICO institucional com a ESTRUTURA OBRIGATÓRIA:\n"
                "   1. CABEÇALHO INSTITUCIONAL: Secretaria de Saúde de Pernambuco (SES-PE) / Assistência Farmacêutica.\n"
                f"   2. IDENTIFICAÇÃO DO PROCESSO: Processo SEI nº {numero_sei or 'N/I'}.\n"
                "   3. RELATÓRIO SINTÉTICO: Paciente, medicamento pleiteado, CID/diagnóstico e prescrição.\n"
                "   4. ANÁLISE TÉCNICA E FUNDAMENTAÇÃO: Confronto das evidências com o PCDT/RENAME/REESME e critérios do SUS. Caso a análise sugira o indeferimento devido à impossibilidade de fornecer o insumo para o CID informado, incluir todos os insumos que podem ser dispensados para aquele CID.\n"
                "   5. CONCLUSÃO TÉCNICA SUGERIDA: Sugestão preliminar, pendências documentais e necessidade expressa de validação humana final.\n"
                "   Não adicione introduções ou saudações fora do padrão formal do parecer.\n"
            )
        else:
            instruction += "\n7. O campo `minuta_parecer` pode ficar em branco ou conter uma síntese sucinta.\n"

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
            f"TEXTO DO PROCESSO:\n{process_text}"
        )
        if support_context:
            prompt_input += f"\n\nCONTEXTO DE SUPORTE PREEXISTENTE:\n{support_context[:3000]}"

        user_content = types.Content(
            role="user",
            parts=[types.Part.from_text(text=prompt_input)],
        )

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

        parsed_data = self._safe_parse_adk_output(raw_output)
        return parsed_data

    @staticmethod
    def _normalize_payload(payload: dict) -> dict:
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

        confronto = normalized["confronto_documentacao_suporte"]
        confronto.setdefault("cid_validado", False)
        confronto.setdefault("medicamento_contemplado_para_o_cid", "indeterminado")
        confronto.setdefault("observacoes", [])
        if not isinstance(confronto["observacoes"], list):
            confronto["observacoes"] = []

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
    def _safe_parse_adk_output(raw_text: str) -> dict:
        """Converte com segurança o output do modelo para o schema esperado pelo frontend."""
        if not raw_text:
            return AdkResumoService._normalize_payload({})

        cleaned = raw_text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if cleaned.startswith("json"):
                cleaned = cleaned[4:].strip()

        try:
            data = json.loads(cleaned)
            if isinstance(data, dict):
                return AdkResumoService._normalize_payload(data)
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
        return AdkResumoService._normalize_payload(fallback)

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
            "ESTRUTURA OBRIGATÓRIA:\n"
            "1. CABEÇALHO INSTITUCIONAL: Secretaria de Saúde de Pernambuco (SES-PE) / Assistência Farmacêutica.\n"
            f"2. IDENTIFICAÇÃO DO PROCESSO: Processo SEI nº {numero_sei or 'N/I'}.\n"
            "3. RELATÓRIO SINTÉTICO: Paciente, medicamento pleiteado, CID/diagnóstico e prescrição.\n"
            "4. ANÁLISE TÉCNICA E FUNDAMENTAÇÃO: Confronto das evidências com o PCDT/RENAME/REESME e critérios do SUS. Caso a análise sugira o indeferimento devido à impossibilidade de fornecer o insumo para o CID informado, incluir todos os insumos que podem ser dispensados para aquele CID.\n"
            "5. CONCLUSÃO TÉCNICA SUGERIDA: Sugestão preliminar, pendências documentais e necessidade expressa de validação humana final.\n"
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
