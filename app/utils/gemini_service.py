import os
import time
import json
import math
import re
import concurrent.futures
import unicodedata
from typing import List, Optional

from google import genai
from google.genai import types
from google.cloud import storage
from pydantic import BaseModel, Field

from app.utils.rag_service import IncrementalRAG

MAX_KNOWLEDGE_BASE_FILES = 16
RAG_TOP_K_PER_DOCUMENT = 4

# Padrão de código CID-10: uma letra + dois dígitos + subcategoria decimal opcional (ex.: F31.6)
CID10_PATTERN = re.compile(r"\b([A-Z]\d{2}(?:\.\d{1,2})?)\b")

# Siglas administrativas próximas às quais um "falso positivo" de CID costuma aparecer em OCR
# (CRM, CEP, CNPJ, etc. às vezes geram sequências letra+dígitos parecidas com um CID)
_NON_CID_CONTEXT_KEYWORDS = ("CEP", "CNPJ", "CPF", "CNS", "CNES", "RQE", "CRM", "SEI", "TEL")


def _normalize_marker_text(text):
    normalized = unicodedata.normalize("NFKD", text)
    without_accents = "".join(char for char in normalized if not unicodedata.combining(char))
    return without_accents.upper()


def clean_minuta_text(text):
    if not text:
        return ""

    cleaned_lines = []
    skipping_confidence_section = False

    for raw_line in str(text).splitlines():
        line = raw_line.strip()
        marker = _normalize_marker_text(line)

        if not line or re.fullmatch(r"[-*_]{3,}", line):
            if cleaned_lines and cleaned_lines[-1] != "":
                cleaned_lines.append("")
            continue

        if line.startswith("```"):
            continue

        if re.match(r"^ASSUNTO\s*:", marker) or re.match(r"^CONFIDENCE_SCORE\s*:", marker):
            continue

        confidence_heading = re.sub(r"^[#>\s*\-_.]*\d*\.?\s*", "", marker)
        if re.search(r"\b(CALCULO DE CONFIANCA|CONFIDENCE SCORE|NOTA DE CONFIANCA)\b", confidence_heading):
            skipping_confidence_section = True
            continue

        if skipping_confidence_section:
            is_new_numbered_section = bool(re.match(r"^\s*(?:#{1,6}\s*)?\d+\.\s+", line))
            if not is_new_numbered_section:
                continue
            skipping_confidence_section = False

        line = re.sub(r"^\s*#{1,6}\s*", "", line)
        line = re.sub(r"^\s*>\s?", "", line)
        line = re.sub(r"^\s*[*+-]\s+", "", line)
        line = line.replace("**", "").replace("__", "").replace("`", "").replace("*", "")
        cleaned_lines.append(line.rstrip())

    cleaned = "\n".join(cleaned_lines).strip()
    return re.sub(r"\n{3,}", "\n\n", cleaned)


def ensure_sei_reference(text, numero_sei):
    numero_sei = str(numero_sei or "").strip()
    if not numero_sei:
        return text

    reference_line = f"Ref. ao SEI n\u00ba {numero_sei}"
    if not text:
        return reference_line

    ref_pattern = re.compile(
        "(?im)^\\s*Ref\\.\\s*ao\\s+SEI\\s*n(?:[\\u00ba\\u00b0o]|\\.)?\\s*.*$"
    )
    if ref_pattern.search(text):
        return ref_pattern.sub(reference_line, text, count=1)

    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.strip():
            lines.insert(index + 1, reference_line)
            return "\n".join(lines)

    return reference_line


def extract_cid_candidates(text):
    """
    Rede de segurança determinística: varre um texto bruto em busca de todos os
    códigos CID-10 mencionados. Valida a extração feita pelo modelo
    """
    if not text:
        return set()
    source = str(text)
    candidates = set()
    for match in CID10_PATTERN.finditer(source):
        start = match.start()
        window = source[max(0, start - 15):start].upper()
        if any(keyword in window for keyword in _NON_CID_CONTEXT_KEYWORDS):
            continue
        candidates.add(match.group(1).upper())
    return candidates


def reconcile_cids(llm_cids, raw_text):
    """
    Combina os CIDs extraídos pelo modelo com os encontrados pela varredura do texto original
    """
    merged = []
    existing_codes = set()

    for item in (llm_cids or []):
        codigo = str((item or {}).get("codigo", "")).strip().upper()
        if not codigo or codigo in existing_codes:
            continue
        existing_codes.add(codigo)
        merged.append({"codigo": codigo, "origem": (item or {}).get("origem", "")})

    specific_prefixes = {c.split(".")[0] for c in existing_codes if "." in c}

    for codigo in sorted(extract_cid_candidates(raw_text)):
        if codigo in existing_codes or codigo in specific_prefixes:
            continue
        merged.append({"codigo": codigo, "origem": "Verificação automática no texto do processo"})
        existing_codes.add(codigo)

    return merged


def filtrar_medicamentos_sem_apresentacao(dados_clinicos):
    """
    Remove itens sem apresentação/posologia concreta.
    Menções de histórico ("faz uso ocasional de..."), siglas de classe terapêutica e lixo de OCR
    não têm receituário/laudo por trás e por isso não chegam com uma apresentação preenchida —
    esse filtro os descarta mesmo que escapem das regras de prompt das Fases 1/1B.
    """
    medicamentos = (dados_clinicos or {}).get("medicamentos_solicitados") or []
    dados_clinicos["medicamentos_solicitados"] = [
        m for m in medicamentos if str((m or {}).get("apresentacao", "")).strip()
    ]
    return dados_clinicos


STATUS_ORDEM_MINUTA = [
    "Componente Básico",
    "Componente Especializado - Aprovado",
    "Componente Especializado - Negado",
    "Não Dispensado",
]


def agrupar_decisoes_por_status(decisoes):
    """
    Agrupa as decisões por status, na ordem em que devem aparecer na minuta. 
    Evita que a LLM misture, itens de status diferentes
    (ex.: "Componente Básico" caindo dentro do parágrafo de "Não Dispensado").
    """
    grupos = {status: [] for status in STATUS_ORDEM_MINUTA}
    for item in (decisoes or {}).get("decisoes", []):
        status = (item or {}).get("status") or "Não Dispensado"
        grupos.setdefault(status, []).append(item)
    return {status: itens for status, itens in grupos.items() if itens}

# --- Esquemas estruturados (forçam o formato de resposta no Gemini) ---
class MedicamentoSolicitado(BaseModel):
    nome: str
    apresentacao: str = ""
    origem: Optional[str] = None


class CidEncontrado(BaseModel):
    codigo: str
    origem: str = ""


class DadosClinicos(BaseModel):
    paciente: Optional[str] = None
    municipio: Optional[str] = None
    medicamentos_solicitados: List[MedicamentoSolicitado] = Field(default_factory=list)
    cids_encontrados: List[CidEncontrado] = Field(default_factory=list)


class DecisaoMedicamento(BaseModel):
    medicamento: str
    apresentacao: Optional[str] = None
    status: str
    cid_relacionado: Optional[str] = None
    justificativa_para_minuta: str


class DecisoesResponse(BaseModel):
    decisoes: List[DecisaoMedicamento] = Field(default_factory=list)


class GeminiService:
    def __init__(self):
        # A chave de API deve estar no .env como GEMINI_API_KEY
        self.api_key = os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY não encontrada nas variáveis de ambiente.")
        self.client = genai.Client(api_key=self.api_key, vertexai=True)
        self.rag = IncrementalRAG(
            genai_client=self.client,
            bucket_name=os.getenv("GCS_BUCKET_NAME"),
            knowledge_prefix=os.getenv("GCS_BUCKET_KNOWLEDGE_BASE"),
            index_prefix="rag-index/",
        )

    def generate_response(self, prompt, model="gemini-3.5-pro"):
        try:
            response = self.client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(temperature=0.1),
            )
            return response.text
        except Exception as e:
            print(f"Erro ao chamar a API do Gemini: {e}")
            return None

    def print_model_info(self, model="gemini-3.5-pro"):
        try:
            model_info = self.client.models.get(model=model)
            print(f"Nome de exibição: {model_info.display_name}")
            print(f"Limite de Tokens de Entrada: {model_info.input_token_limit}")
            print(f"Temperatura Máxima: {model_info.max_temperature}")
            print(f"Métodos Suportados: {model_info.supported_generation_methods}")
        except Exception as e:
            print(f"Erro ao chamar a API do Gemini: {e}")
            return None

    def list_blobs(self, project_id, bucket_name, knowledge_base_dir):
        storage_client = storage.Client(project=project_id)
        blobs = storage_client.list_blobs(bucket_name, prefix=knowledge_base_dir)
        file_list = []
        for blob in blobs:
            if blob.name.endswith("/") or not blob.name:
                continue
            gcs_uri = f"gs://{bucket_name}/{blob.name}"
            file_list.append((gcs_uri, blob.content_type))
        return file_list

    def filter_files_from_knowledge_base(self, model="gemini-3.5-flash", file_uri=None, mime_type=None, process_text=None, prompt_focado=None):
        project_id = os.getenv("GCS_PROJECT_ID")
        bucket_name = os.getenv("GCS_BUCKET_NAME")
        knowledge_base_dir = os.getenv("GCS_BUCKET_KNOWLEDGE_BASE")

        contents = []
        available_files = []

        filter_instruction = f"""
        Você atua como analista técnico da Secretaria de Saúde do Estado de Pernambuco. Analise o PROCESSO ADMINISTRATIVO anexado e selecione, entre os caminhos de arquivos fornecidos, somente os documentos necessários para responder administrativamente sobre todos os medicamentos FORMALMENTE SOLICITADOS, considerando também os CIDs informados no processo.
        Use até {MAX_KNOWLEDGE_BASE_FILES} documentos complementares e focados no pedido.
        Retorne somente os caminhos exatos dos arquivos existentes na lista recebida, um por linha. Não use numeração ou comentários.
        """
        contents.append("BASE DE CONHECIMENTO (Leis, protocolos clínicos, CID e normas técnicas):")
        if project_id and bucket_name and knowledge_base_dir:
            client = storage.Client(project=project_id)
            bucket = client.bucket(bucket_name)
            blobs = bucket.list_blobs(prefix=knowledge_base_dir)
            for blob in blobs:
                if blob.name.endswith("/") or not blob.name:
                    continue
                available_files.append(blob.name)
                contents.append(f"{blob.name}")

            if file_uri:
                contents.append("PROCESSO ADMINISTRATIVO (Pedido do medicamento):")
                contents.append(types.Part.from_uri(file_uri=file_uri, mime_type=mime_type))

            if prompt_focado:
                contents.append(f"MEDICAMENTOS SOLICITADOS IDENTIFICADOS:\n{prompt_focado}")
            elif process_text:
                contents.append("TEXTO EXTRAÍDO DO PROCESSO VIA OCR:")
                contents.append(process_text)

        current_time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        print(f"GeminiService - Iniciando chamada à API de filtro em: {current_time_str}")
        start_time = time.time()

        def _chamar_api_filtro():
            return self.client.models.generate_content(
                model=model,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=filter_instruction,
                    temperature=0.1,
                    top_p=0.45,
                    top_k=10
                )
            )

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_chamar_api_filtro)
                response = future.result(timeout=60)
        except concurrent.futures.TimeoutError:
            print("error: A IA travou e não respondeu o filtro em 60s.")
            return []
        except Exception as e:
            print(f"error: {e}")
            return []

        end_time = time.time()
        print(f"Tempo de resposta da API (Filtro): {end_time - start_time:.2f} segundos")

        if not response or not response.text:
            return []

        available_files_set = set(available_files)
        files = []
        seen = set()

        for line in response.text.strip().splitlines():
            clean_name = re.sub(r"^\s*\d+[.)-]?\s*", "", line).strip(" -*`\"'")
            if clean_name in available_files_set and clean_name not in seen:
                seen.add(clean_name)
                files.append(clean_name)

        return files[:MAX_KNOWLEDGE_BASE_FILES]

    def _fase_1_extrair_dados(self, model, file_uri, mime_type, process_text):
        system_instruction = """
        Sua única tarefa é extrair fatos literais do processo, sem julgar cobertura do SUS e sem inventar dados.
        Responda OBRIGATORIAMENTE em JSON estruturado:
        {
          "paciente": "Nome completo",
          "municipio": "Município exato (retorne null se não houver menção explícita)",
          "medicamentos_solicitados": [{"nome": "Nome do medicamento", "apresentacao": "Concentração e forma farmacêutica do PRODUTO (ex.: comprimido 2mg, solução oral gotas)", "origem": "Documento onde essa apresentação foi lida"}],
          "cids_encontrados": [{"codigo": "Código CID exato, incluindo o ponto decimal quando houver", "origem": "Documento onde foi lido"}]
        }

        REGRAS ABSOLUTAS:
        - Extraia TODOS os medicamentos expressamente solicitados no requerimento formal, incluindo os que constam em receituários, laudos e guias anexados que fundamentem o pedido.
        - Só extraia um medicamento se houver receituário, laudo ou guia com a apresentação (dose/forma) concreta. Ignore QUALQUER medicamento citado apenas no histórico/anamnese narrativa (ex.: "faz uso ocasional de...", "já utilizou..."), sem documento que fundamente o pedido formal.
        - NUNCA registre como medicamento uma sigla de classe terapêutica (ex.: TARV, ATB) nem um fragmento de texto ilegível/corrompido de OCR que não seja um nome de medicamento real e reconhecível.
        - "apresentacao" é a concentração + forma farmacêutica do PRODUTO (ex.: "comprimido 2mg", "solução oral gotas 2,5mg/mL"). NUNCA é a posologia/instrução de uso (quantas gotas tomar, quantos frascos, frequência como "3/3h" ou "à noite"). Quando o documento só trouxer posologia sem concentração explícita do produto (ex.: "10 gts 3/3h", "5 gotas à noite"), registre a apresentação apenas como a forma farmacêutica reconhecível (ex.: "solução oral gotas"), sem incluir a quantidade/frequência no campo.
        - Se o MESMO princípio ativo aparecer com apresentações (concentração/forma) DIFERENTES em documentos diferentes, registre CADA apresentação como um item separado da lista, com sua própria "origem". Nunca escolha arbitrariamente apenas uma delas nem misture concentrações/formas distintas em um único item.
        - Se o MESMO princípio ativo aparecer sob nomes comerciais diferentes (genérico e marca, ou grafias/erros de digitação do mesmo nome) para a MESMA apresentação, ou a MESMA forma farmacêutica for citada mais de uma vez só com posologia variando (gotas, frascos, frequência), registre um ÚNICO item com o nome do princípio ativo, citando as variações de nome/posologia na "origem".
        - Percorra CADA documento do processo (formulário, guias de encaminhamento, laudos, receituários) individualmente e extraia TODOS os códigos CID-10 mencionados, incluindo os que aparecem em campos de "Observação" ou embutidos no meio de parágrafos. Nenhum código pode ser omitido, mesmo que pareça secundário ou repetido em outro documento.
        - Não deduza ou invente CIDs, doses ou apresentações. Copie EXATAMENTE os valores escritos nos documentos, incluindo o ponto decimal do CID (ex.: registre "F31.6", nunca arredonde ou trunque para "F31").
        """
        contents = []
        if file_uri:
            contents.append(types.Part.from_uri(file_uri=file_uri, mime_type=mime_type))
        if process_text:
            contents.append(f"TEXTO DO OCR:\n{process_text}")

        try:
            response = self.client.models.generate_content(
                model=model,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=0.0,
                    response_mime_type="application/json",
                    response_schema=DadosClinicos,
                )
            )
            dados = json.loads(response.text)
        except Exception as e:
            print(f"Erro na extração JSON: {e}")
            dados = {}

        try:
            dados["cids_encontrados"] = reconcile_cids(dados.get("cids_encontrados", []), process_text)
        except Exception as e:
            print(f"Aviso: falha na reconciliação determinística de CIDs: {e}")

        return dados

    def _fase_1b_validar_extracao(self, model, dados_clinicos, process_text):
        """
        Devido ao alto numero de erros na extração foi incorporado uma etapa
        de validação dos dados extraidos
        """
        if not process_text:
            return dados_clinicos

        system_instruction = """
        Você é um auditor de qualidade de dados. Vai receber o TEXTO ORIGINAL de um processo administrativo
        e um JSON com dados já extraídos dele. Sua tarefa é CONFERIR o JSON contra o texto, não recriá-lo do zero.

        "apresentacao" é a concentração + forma farmacêutica do PRODUTO (ex.: "comprimido 2mg", "solução oral
        gotas"). NUNCA é posologia/instrução de uso (quantidade de gotas, frascos, frequência como "3/3h" ou
        "à noite") — isso não define um item novo nem deve compor o campo "apresentacao".

        Faça, em ordem:
        1. Para cada medicamento citado em receituários, laudos ou guias do TEXTO ORIGINAL, confirme se ele
           aparece no JSON com a apresentação (concentração/forma) EXATAMENTE como escrita na fonte, sem
           misturar posologia/quantidade no campo.
        2. Se o mesmo princípio ativo aparecer com concentrações ou formas farmacêuticas diferentes em
           documentos diferentes, garanta que CADA uma conste como um item separado no JSON.
        3. Para cada código CID-10 citado no TEXTO ORIGINAL — inclusive em campos "Observação" ou embutido
           dentro de parágrafos — confirme se está presente em "cids_encontrados". Não invente códigos que
           não constem literalmente no texto.
        4. Corrija qualquer valor do JSON que não bata exatamente com o texto original (ex.: dose incorreta).
        5. REMOVA do JSON qualquer item de "medicamentos_solicitados" que se enquadre em algum destes casos:
           (a) só é citado no histórico/anamnese narrativo, sem receituário/laudo/guia que o fundamente;
           (b) é uma sigla de classe terapêutica (ex.: TARV, ATB), não um nome de medicamento;
           (c) é texto de OCR ilegível/corrompido, sem corresponder a um nome de medicamento real;
           (d) é duplicata de outro item já presente — mesmo princípio ativo E mesma apresentação (concentração
               e forma farmacêutica), ainda que sob nome comercial/grafia diferente ou com posologia/quantidade
               (gotas, frascos, frequência) descrita de forma diferente entre documentos — nesse caso mantenha
               só um item.
           Fora esses casos, não remova itens corretos: apenas adicione o que faltar e corrija o que estiver errado.

        Devolva o JSON COMPLETO e corrigido, na mesma estrutura recebida.
        """
        prompt = (
            f"TEXTO ORIGINAL DO PROCESSO:\n{process_text}\n\n"
            f"JSON EXTRAÍDO PARA CONFERÊNCIA:\n{json.dumps(dados_clinicos, ensure_ascii=False, indent=2)}"
        )

        try:
            response = self.client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=0.0,
                    response_mime_type="application/json",
                    response_schema=DadosClinicos,
                )
            )
            dados_validados = json.loads(response.text)
        except Exception as e:
            print(f"Aviso: falha na fase de auditoria/validação, mantendo extração original: {e}")
            return dados_clinicos

        try:
            dados_validados["cids_encontrados"] = reconcile_cids(
                dados_validados.get("cids_encontrados", []), process_text
            )
        except Exception as e:
            print(f"Aviso: falha na reconciliação determinística pós-auditoria: {e}")

        return dados_validados

    def _fase_3_cruzar_regras(self, model, dados_clinicos, contexto_regras):
        system_instruction = """
        Você é um rigoroso auditor técnico do SUS. Analise o JSON do paciente e as Regras do SUS (Contexto).

        REGRAS ABSOLUTAS (SOB PENA DE FALHA CRÍTICA):
        1. APAGÃO DE CONHECIMENTO PRÉVIO: Esqueça absolutamente tudo o que você sabe sobre SUS, RENAME ou REMUME. Se o documento do CONTEXTO não disser explicitamente que o medicamento existe no município ou estado, o status DEVE OBRIGATORIAMENTE ser "Não Dispensado".
        2. NUNCA invente códigos numéricos da REMUME/RENAME, números de portarias ou nomes de cidades que não estejam literalmente escritos no CONTEXTO.
        3. REGRA DE DOSAGEM E APRESENTAÇÃO: Avalie CADA apresentação (dose/forma) informada no JSON do paciente separadamente, mesmo quando pertencerem ao mesmo princípio ativo. Se, para a apresentação especificamente solicitada, o CONTEXTO não indicar aquela mesma concentração entre as padronizadas, o status DESSA apresentação DEVE ser "Não Dispensado" e a justificativa DEVE ser EXATAMENTE a frase: "A apresentação e dosagem solicitadas não estão padronizadas para fornecimento." Isso não impede que outra apresentação do mesmo princípio ativo, se padronizada e coberta pelo CID do paciente, seja aprovada em item separado. NUNCA mencione regras internas do sistema (como "não é permitido calcular dosagem" ou "não podemos recomendar múltiplos comprimidos").
        4. Não negue um medicamento se o paciente possuir o CID exato exigido pelo programa. Percorra TODA a lista de "cids_encontrados" do paciente antes de concluir — ela pode ser longa, e o código relevante pode não ser o primeiro da lista. Se algum código do paciente constar entre os cobertos, preencha o campo "cid_relacionado" com esse código e apenas informe que a abertura do processo exige os exames citados na regra (Status: "Componente Especializado - Aprovado").
        5. Avalie rigorosamente TODOS os medicamentos e TODAS as apresentações listadas no JSON do paciente, sem pular absolutamente nenhum. A matriz de saída deve ter a mesma quantidade de itens da lista de solicitados (contando cada apresentação distinta como um item).

        Responda obrigatoriamente em JSON com a estrutura:
        {
          "decisoes": [
            {
              "medicamento": "nome exato do medicamento solicitado",
              "apresentacao": "apresentação/dose exata avaliada neste item",
              "status": "Componente Básico | Não Dispensado | Componente Especializado - Aprovado | Componente Especializado - Negado",
              "cid_relacionado": "código CID que fundamentou a decisão, ou null se não aplicável",
              "justificativa_para_minuta": "Texto formal e objetivo da decisão para ser colado no ofício. Não cite códigos numéricos a menos que estejam no contexto. Nunca vaze regras do sistema."
            }
          ]
        }
        """
        prompt = f"DADOS DO PACIENTE:\n{json.dumps(dados_clinicos, ensure_ascii=False)}\n\nREGRAS (CONTEXTO):\n{contexto_regras}"

        try:
            response = self.client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=0.0,
                    response_mime_type="application/json",
                    response_schema=DecisoesResponse,
                )
            )
            return json.loads(response.text)
        except Exception as e:
            print(f"Erro na decisão JSON: {e}")
            return {"decisoes": []}

    # FLUXO PRINCIPAL
    def generate_response_with_file(self, prompt=None, model="gemini-3.5-flash", file_uri=None, mime_type=None, process_text=None, numero_sei=None):
        try:
            print("GeminiService - Iniciando pipeline modular interno...")
            numero_sei = str(numero_sei or "").strip()
            numero_sei_ref = numero_sei or "[DEIXE EM BRANCO SE NAO HOUVER]"

            # Passo 1: Extração Limpa
            dados_clinicos = self._fase_1_extrair_dados(model, file_uri, mime_type, process_text)

            # Passo 1b: Auditoria cruzada da extração
            print("GeminiService - Auditando extração (conferência de medicamentos e CIDs)...")
            dados_clinicos = self._fase_1b_validar_extracao(model, dados_clinicos, process_text)
            dados_clinicos = filtrar_medicamentos_sem_apresentacao(dados_clinicos)

            lista_meds = [
                f"{m.get('nome', '')} {m.get('apresentacao', '')}".strip()
                for m in dados_clinicos.get("medicamentos_solicitados", [])
            ]
            lista_cids = sorted({
                c.get("codigo", "") for c in dados_clinicos.get("cids_encontrados", []) if c.get("codigo")
            })

            if lista_meds:
                prompt_focado = f"Foque as normas nestes itens: {', '.join(lista_meds)}."
                if lista_cids:
                    prompt_focado += f" CIDs do paciente a considerar: {', '.join(lista_cids)}."
            else:
                prompt_focado = prompt

            # Passo 2: Filtro de Arquivos
            filter_list = self.filter_files_from_knowledge_base(
                model=model, file_uri=file_uri, mime_type=mime_type,
                process_text=process_text, prompt_focado=prompt_focado
            )

            if not filter_list:
                filter_list = []

            print(f"GeminiService - {len(filter_list)} Documentos selecionados para vetorização/busca RAG.")

            # Passo 3: RAG Arquivo por Arquivo
            rag_base_query = prompt_focado if prompt_focado else "medicamentos solicitados, critérios de inclusão e CIDs"
            rag_contexts = []

            for file_name in filter_list:
                document_query = (
                    f"{rag_base_query}\n"
                    f"DOCUMENTO ALVO: {file_name}\n"
                    f"Recupere regras de dispensação, restrições, apresentações padronizadas e CIDs contemplados."
                )
                try:
                    document_result = self.rag.rag(
                        query=document_query,
                        selected_files=[file_name],
                        top_k=RAG_TOP_K_PER_DOCUMENT,
                    )
                    document_context = (document_result or {}).get("context")
                    if document_context:
                        rag_contexts.append(f"DOCUMENTO: {file_name}\n{document_context}")
                except Exception as rag_error:
                    print(f"GeminiService - Erro ao recuperar trechos de {file_name}: {rag_error}")

            rag_context_final = "\n\n".join(rag_contexts)

            # Passo 4: Decisão Técnica Limpa
            decisoes = self._fase_3_cruzar_regras(model, dados_clinicos, rag_context_final)

            # Passo 5: Geração da Minuta
            system_instruction_redator = f"""
            Você é um redator administrativo da Secretaria de Saúde de Pernambuco (DGAF).
            Redija o ofício de resposta usando EXCLUSIVAMENTE os dados do JSON fornecido.
            NÃO INVENTE INFORMAÇÕES. NÃO ADICIONE CABEÇALHOS FORA DO MODELO.

            O JSON de decisões já vem AGRUPADO por status (chave = status, valor = lista de itens
            daquele status), na ordem exata em que os grupos devem aparecer na minuta. Gere UM item
            numerado por grupo presente no JSON (pule grupos ausentes) e NUNCA misture, no mesmo item
            numerado, medicamentos de grupos (status) diferentes.

            Dentro de um mesmo grupo, se o MESMO medicamento tiver mais de uma apresentação (dose),
            trate e explique cada apresentação separadamente dentro do item — nunca junte doses
            diferentes numa única frase genérica que possa confundir o requerente sobre qual
            apresentação foi de fato aprovada ou negada.

            É OBRIGATÓRIO USAR ESTA ESTRUTURA:

            Ao Sr./À Sra. [NOME DO PACIENTE]
            Ref. ao SEI n\u00ba {numero_sei_ref}

            Prezado(a) Senhor(a),

            Cumprimentando-o(a) cordialmente, e em resposta ao requerimento com solicitação dos medicamentos [LISTAR OS REMÉDIOS AQUI] para o(a) paciente [NOME], esclarecemos, inicialmente, que o elenco de medicamentos disponibilizado pelo SUS é estruturado com base na RENAME, elaborada e atualizada pelo Ministério da Saúde com apoio técnico da CONITEC. A seleção dos medicamentos essenciais segue critérios técnico-científicos de eficácia, segurança, qualidade e custo-efetividade, considerando as necessidades prioritárias de saúde da população brasileira.

            Sobre os medicamentos solicitados neste processo, informamos que:

            [TRANSFORME CADA GRUPO DO JSON EM UM ITEM NUMERADO (1, 2, 3...), NESTA MESMA ORDEM]

            Sem mais para o momento, colocamo-nos à disposição para quaisquer esclarecimentos.

            Atenciosamente,

            Núcleo de Respostas - GADM
            Diretoria Geral de Assistência Farmacêutica
            Secretaria de Saúde de Pernambuco.

            IMPORTANTE: Fora da minuta, nas duas últimas linhas, inclua OBRIGATORIAMENTE:
            ASSUNTO: [Assunto curto]
            CONFIDENCE_SCORE: [Número entre 0.80 e 0.99]
            """

            decisoes_agrupadas = agrupar_decisoes_por_status(decisoes)
            prompt_redator = f"PROCESSO SEI:\n{numero_sei_ref}\n\nDECISÕES AGRUPADAS POR STATUS:\n{json.dumps(decisoes_agrupadas, ensure_ascii=False, indent=2)}\n\nPACIENTE:\n{json.dumps(dados_clinicos, ensure_ascii=False)}"

            print("GeminiService - Gerando Minuta Final...")
            start_time = time.time()

            def _chamar_api_analise():
                return self.client.models.generate_content(
                    model=model,
                    contents=prompt_redator,
                    config=types.GenerateContentConfig(
                        system_instruction=system_instruction_redator,
                        temperature=0.2
                    )
                )

            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(_chamar_api_analise)
                    response = future.result(timeout=180)
            except concurrent.futures.TimeoutError:
                print("error: A IA travou na geração final em 180s.")
                return None

            end_time = time.time()
            print(f"Tempo de resposta da API (Análise): {end_time - start_time:.2f} segundos")

            if not response or not response.text:
                return None

            raw_text = response.text

            confidence = 0.90
            confidence_match = re.search(r"CONFIDENCE_SCORE:\s*([\d\.]+)", raw_text, re.IGNORECASE)
            if confidence_match:
                try:
                    confidence = float(confidence_match.group(1))
                    confidence = max(0.0, min(1.0, confidence))
                except ValueError:
                    pass

            assunto = "Assunto não identificado"
            assunto_match = re.search(r"^\s*ASSUNTO:\s*(.*)$", raw_text, re.IGNORECASE | re.MULTILINE)
            if assunto_match:
                assunto = assunto_match.group(1).strip()

            clean_text = re.sub(r"^\s*CONFIDENCE_SCORE:\s*[\d\.]+\s*$", "", raw_text, flags=re.IGNORECASE | re.MULTILINE)
            clean_text = re.sub(r"^\s*ASSUNTO:\s*.*$", "", clean_text, flags=re.IGNORECASE | re.MULTILINE)
            clean_text = clean_minuta_text(clean_text)
            clean_text = ensure_sei_reference(clean_text, numero_sei)

            return {
                "text": clean_text,
                "confidence": confidence,
                "assunto": assunto,
                "avg_logprobs": None,
                "files": filter_list
            }
        except Exception as e:
            print(f"⛔ Erro inesperado no GeminiService: {e}")
            return None

    def generate_minuta_only(self, resumo_tecnico_json, model="gemini-3.5-flash", numero_sei=None):
        try:
            numero_sei = str(numero_sei or "").strip()
            numero_sei_ref = numero_sei or "[DEIXE EM BRANCO SE NAO HOUVER]"

            system_instruction = f"""
            Você é um redator administrativo da Secretaria de Saúde de Pernambuco (DGAF).
            Redija o ofício de resposta usando EXCLUSIVAMENTE os dados do JSON fornecido.
            NÃO INVENTE INFORMAÇÕES. NÃO ADICIONE CABEÇALHOS FORA DO MODELO.

            Se o JSON trouxer mais de uma apresentação (dose) para o mesmo medicamento com status
            diferentes entre si, trate cada apresentação separadamente, sem misturá-las numa única frase.

            É OBRIGATÓRIO USAR ESTA ESTRUTURA EXATA:

            Ao Sr./À Sra. [NOME DO PACIENTE]
            Ref. ao SEI n\u00ba {numero_sei_ref}

            Prezado(a) Senhor(a),

            Cumprimentando-o(a) cordialmente, e em resposta ao requerimento com solicitação dos medicamentos [LISTAR OS REMÉDIOS AQUI] para o(a) paciente [NOME], esclarecemos, inicialmente, que o elenco de medicamentos disponibilizado pelo SUS é estruturado com base na RENAME, elaborada e atualizada pelo Ministério da Saúde com apoio técnico da CONITEC. A seleção dos medicamentos essenciais segue critérios técnico-científicos de eficácia, segurança, qualidade e custo-efetividade, considerando as necessidades prioritárias de saúde da população brasileira.

            Sobre os medicamentos solicitados neste processo, informamos que:

            [AGRUPAR AS JUSTIFICATIVAS DO JSON EM ITENS NUMERADOS (1, 2, 3...).
            DICA: Agrupe os "Não Dispensados" em um único item, os de "Componente Básico" em outro, e detalhe os "Especializados" em itens próprios]

            Sem mais para o momento, colocamo-nos à disposição para quaisquer esclarecimentos.

            Atenciosamente,

            Núcleo de Respostas - GADM
            Diretoria Geral de Assistência Farmacêutica
            Secretaria de Saúde de Pernambuco.
            """

            prompt_content = f"PROCESSO SEI:\n{numero_sei_ref}\n\nRESUMO TÉCNICO (USE ESTES DADOS PARA PREENCHER O OFÍCIO):\n{resumo_tecnico_json}"

            current_time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
            print(f"GeminiService - Iniciando chamada rápida de Minuta em: {current_time_str}")

            response = self.client.models.generate_content(
                model=model,
                contents=prompt_content,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=0.1,
                )
            )

            if not response or not response.text:
                return None

            return ensure_sei_reference(clean_minuta_text(response.text), numero_sei)

        except Exception as e:
            print(f"⛔ Erro ao gerar apenas a minuta no GeminiService: {e}")
            return None
