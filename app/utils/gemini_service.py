import os
import time
import math
import re
import concurrent.futures
import unicodedata

from google import genai
from google.genai import types
from google.cloud import storage

from app.utils.rag_service import IncrementalRAG

MAX_KNOWLEDGE_BASE_FILES = 10


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


_SES_PE_RESPOSTA_ADMINISTRATIVA_INSTRUCTION = (
    "Voce atua no Nucleo de Respostas da Diretoria Geral de Assistencia Farmaceutica "
    "da Secretaria de Saude de Pernambuco. Sua tarefa e redigir uma minuta de resposta "
    "administrativa ao requerente sobre solicitacao de medicamento em processo SEI.\n\n"
    "PADRAO INSTITUCIONAL DE REDACAO:\n"
    "A minuta deve ser objetiva e organizada por grupos de medicamentos com a mesma conclusao administrativa. "
    "O foco principal e informar se o medicamento e dispensado, qual Componente da Assistencia Farmaceutica e responsavel, "
    "quais CIDs, concentracoes e apresentacoes sao contemplados e se os diagnosticos do paciente correspondem exatamente "
    "aos criterios documentados. Nao produza parecer clinico, juridico ou revisao extensa dos protocolos.\n\n"
    "FORMATO ESPERADO DA RESPOSTA:\n"
    "Ao Sr./A Sra. [NOME DO REQUERENTE OU PACIENTE]\n"
    "Ref. ao SEI no [NUMERO DO SEI]\n\n"
    "[Prezado Senhor, ou Prezada Senhora, conforme o destinatario identificado]\n\n"
    "Cumprimentando-o(a) cordialmente, e em resposta ao requerimento recebido por meio do processo SEI no [NUMERO], "
    "com solicitacao do(s) medicamento(s) [LISTA] para o(a) paciente [NOME], esclarecemos, inicialmente, "
    "que o elenco de medicamentos disponibilizado pelo SUS e estruturado com base na RENAME, elaborada "
    "e atualizada pelo Ministerio da Saude com apoio tecnico da CONITEC. A selecao dos medicamentos "
    "essenciais segue criterios tecnico-cientificos de eficacia, seguranca, qualidade e custo-efetividade, "
    "considerando as necessidades prioritarias de saude da populacao brasileira.\n\n"
    "Sobre os medicamentos solicitados neste processo, informamos que:\n\n"
    "1) [MEDICAMENTOS COM A MESMA CONCLUSAO ADMINISTRATIVA]\n"
    "2) [MEDICAMENTOS DE OUTRO COMPONENTE OU COM OUTRA CONCLUSAO]\n"
    "3) [MEDICAMENTO DISPONIVEL APENAS PARA CIDS OU APRESENTACOES ESPECIFICAS]\n\n"
    "Sem mais para o momento, colocamo-nos a disposicao para quaisquer esclarecimentos.\n\n"
    "Atenciosamente,\n\n"
    "Nucleo de Respostas - GADM\n"
    "Diretoria Geral de Assistencia Farmaceutica\n"
    "Secretaria de Saude de Pernambuco.\n\n"
    "DIRETRIZES DE CONTEUDO:\n"
    "1. Antes de redigir, identifique internamente todos os medicamentos formalmente solicitados no oficio, requerimento ou despacho. "
    "Diferencie-os dos medicamentos que aparecem apenas em receitas, laudos ou anexos. Analise como objeto principal somente os medicamentos formalmente solicitados.\n"
    "2. Agrupe medicamentos com a mesma conclusao administrativa e liste expressamente todos os medicamentos de cada grupo.\n"
    "3. Apresente preferencialmente os grupos nesta ordem: (a) medicamentos nao dispensados pela Farmacia de Pernambuco; "
    "(b) medicamentos do Componente Basico; (c) medicamentos do Componente Especializado disponiveis para CIDs especificos; "
    "(d) medicamentos do Componente Estrategico; e (e) demais situacoes particulares.\n"
    "4. Para medicamentos nao dispensados, utilize preferencialmente a estrutura: 'nao sao dispensados na Farmacia de Pernambuco, "
    "uma vez que nao fazem parte de nenhum Programa ou Componente da Assistencia Farmaceutica (Basico, Estrategico e Especializado)'. "
    "Somente use essa conclusao quando houver base documental suficiente.\n"
    "5. Para medicamentos do Componente Basico, informe que o fornecimento e de responsabilidade dos municipios e oriente o paciente "
    "a procurar a Unidade Basica de Saude mais proxima de sua residencia ou o setor de assistencia farmaceutica municipal.\n"
    "6. Para medicamentos do Componente Especializado, informe as patologias, os CIDs, as concentracoes e as formas farmaceuticas "
    "expressamente contempladas nos documentos recuperados. Em seguida, compare esses dados com os CIDs apresentados no processo.\n"
    "7. Considere um CID contemplado somente quando o codigo exato estiver expressamente listado. Nao considere automaticamente que um "
    "codigo generico, como E11 ou F31, corresponde aos subcodigos especificos contemplados pelo programa.\n"
    "8. Quando os CIDs do paciente nao estiverem expressamente contemplados, utilize conclusao objetiva semelhante a: "
    "'Entretanto, para os diagnosticos informados em laudo medico do paciente em tela, o referido medicamento nao esta contemplado para dispensacao no Programa'.\n"
    "9. Para medicamentos do Componente Estrategico, informe o programa correspondente, a concentracao e a apresentacao disponivel, "
    "bem como eventuais restricoes. Nao apresente o medicamento como disponivel fora da indicacao ou apresentacao expressamente prevista.\n"
    "10. Preserve exatamente os nomes, concentracoes e formas informadas no requerimento. Nao substitua nome comercial por principio ativo, "
    "ou principio ativo por marca, salvo quando a correspondencia estiver expressamente demonstrada no processo ou nos documentos consultados.\n"
    "11. Medicamentos relacionados no mesmo PCDT, guia ou norma tecnica nao devem ser apresentados como alternativas terapeuticas ou substitutos "
    "entre si, salvo quando o documento afirmar expressamente essa relacao.\n"
    "12. Preserve exatamente a natureza dos documentos consultados. Nao transforme Norma Tecnica em Nota Tecnica, Portaria em PCDT ou guia de orientacao em protocolo clinico.\n"
    "13. Nao conclua que o paciente possui direito automatico ao medicamento apenas porque um CID aparece no PCDT ou no HORUS. "
    "Quando o CID exato estiver contemplado, informe que o acesso depende do atendimento aos criterios clinicos, laboratoriais e administrativos e da avaliacao tecnica.\n"
    "14. Diferencie rigorosamente os fatos encontrados nos sistemas. Se a tela informar que o paciente 'nao possui solicitacoes no HORUS especializado', "
    "nao converta isso em 'nao possui cadastro', 'cadastro inativo' ou expressao equivalente.\n"
    "15. Informe procedimento de abertura de processo, documentos, exames e prazo de avaliacao apenas quando essas orientacoes forem aplicaveis ao caso "
    "e estiverem expressamente previstas em guia, norma ou PCDT recuperado.\n"
    "16. Nao desenvolva discussoes extensas sobre ANVISA, RENAME, REMUME, custo anual, Tema 1.234 ou alternativas terapeuticas. "
    "Quando houver quesito expresso sobre esses temas, responda de forma breve e administrativa, somente com base documental suficiente. "
    "Se nao houver fonte suficiente, informe que nao foi possivel confirmar o ponto com seguranca.\n"
    "17. Para custo anual ou Tema 1.234, nao estime valores sem preco oficial e quantidade anual claramente definidos nos documentos analisados.\n"
    "18. Nao use linguagem judicial, nao escreva parecer tecnico interno e nao use termos como deferido, indeferido, improcedente ou tutela.\n"
    "19. Nao invente dados. Se o processo nao trouxer nome, SEI, CID, medicamento, diagnostico ou classificacao com clareza, registre a limitacao de forma cautelosa.\n"
    "20. Gere texto puro. Nao use Markdown, cabecalhos com #, negrito com **, linhas ---, tabelas Markdown, blocos de codigo ou listas com asterisco.\n"
    "21. Nao inclua calculo de confianca, nota de confianca, avaliacao critica da propria resposta ou confidence score no corpo da minuta.\n"
    "22. Antes de finalizar, confira internamente se todos os medicamentos formalmente solicitados foram analisados, se medicamentos com a mesma conclusao "
    "foram agrupados e se nenhum CID, componente, apresentacao, documento ou alternativa terapeutica foi inferido sem base documental. Nao mostre esse checklist interno."
)


class GeminiService:
    def __init__(self):
        #A chave de API deve estar no .env como GEMINI_API_KEY
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
            #Log do erro ou tratamento adequado
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
            #Log do erro ou tratamento adequado
            print(f"Erro ao chamar a API do Gemini: {e}")
            return None

    def list_blobs(self, project_id, bucket_name, knowledge_base_dir):
        storage_client = storage.Client(project=project_id)
        blobs = storage_client.list_blobs(bucket_name, prefix=knowledge_base_dir)

        file_list = []

        for blob in blobs:
            if blob.name.endswith("/") or not blob.name:
                continue

            #Construir URI correta do GCS
            gcs_uri = f"gs://{bucket_name}/{blob.name}"
            content_type = blob.content_type
            file_list.append((gcs_uri, content_type))
        return file_list

    def filter_files_from_knowledge_base(self,
        model="gemini-3.5-flash",
        file_uri=None,
        mime_type=None,):

        project_id = os.getenv("GCS_PROJECT_ID")
        bucket_name = os.getenv("GCS_BUCKET_NAME")
        knowledge_base_dir = os.getenv("GCS_BUCKET_KNOWLEDGE_BASE")

        contents = []

        filter_instruction = (
            "Você atua como um analista técnico na Secretaria de Saúde do Estado de Pernambuco, Brasil. "
            "Analise o processo administrativo em anexo e selecione, entre os nomes de arquivos apresentados, "
            "os documentos mais relevantes para responder administrativamente sobre os medicamentos solicitados.\n"
            f"IMPORTANTE: Selecione no MÁXIMO os {MAX_KNOWLEDGE_BASE_FILES} documentos mais relevantes para cobrir todos os medicamentos formalmente solicitados. "
            "Nao priorize apenas o medicamento principal.\n"
            "Para cada medicamento, selecione prioritariamente documentos que permitam confirmar: componente da Assistencia Farmaceutica, "
            "disponibilidade na Farmacia de Pernambuco, concentracao, forma farmaceutica, programa, patologias e CIDs expressamente contemplados.\n"
            "Priorize normas tecnicas, PCDTs e guias especificos. Quando necessario, selecione tambem catalogos gerais como "
            "'CID-10-LISTA-PDF.pdf', 'rename-2024.pdf' ou 'REESME-2025.pdf'.\n"
            "Quando houver quesito expresso sobre ANVISA, RENAME/REMUME, custo anual ou Tema 1.234, selecione documento adicional somente se ele for necessario "
            "e estiver identificado na lista de arquivos.\n"
            "Nao priorize documentos juridicos ou gerais quando uma norma, guia, PCDT ou relacao estadual responder diretamente sobre o medicamento.\n"
            "Retorne apenas os caminhos exatos dos arquivos selecionados, um por linha, sem numeracao, explicacao ou qualquer outro texto.\n"
            "Retorne apenas documentos existentes na lista recebida."
        )

        contents.append("BASE DE CONHECIMENTO (Leis, protocolos clínicos, CID e normas técnicas):")
        if project_id and bucket_name and knowledge_base_dir:
            client = storage.Client(project=project_id)
            bucket = client.bucket(bucket_name)
            blobs = bucket.list_blobs(prefix=knowledge_base_dir)

            for blob in blobs:
                if blob.name.endswith("/") or not blob.name:
                    continue
                #Adicionar apenas o nome do arquivo
                contents.append(f"{blob.name}")
                # print(blob.name)

            #Arquivo alvo a ser analisado
            if file_uri:
                contents.append("PROCESSO ADMINISTRATIVO (Pedido do medicamento):")
                contents.append(types.Part.from_uri(file_uri=file_uri, mime_type=mime_type))

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

        #Realiza o timeout.
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_chamar_api_filtro)
                #O código trava por no máximo 60s.
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

        # Parse the text to extract file names into a list
        files = []
        for line in response.text.strip().split('\n'):
            clean_name = line.strip(" -*`")
            if clean_name:
                files.append(clean_name)

        return files

    def generate_response_with_file(
        self,
        prompt=None,
        model="gemini-3.5-flash",
        file_uri=None,
        mime_type=None,
        process_text=None,
    ):
        try:
            project_id = os.getenv("GCS_PROJECT_ID")
            bucket_name = os.getenv("GCS_BUCKET_NAME")
            knowledge_base_dir = os.getenv("GCS_BUCKET_KNOWLEDGE_BASE")

            system_instruction = (
                _SES_PE_RESPOSTA_ADMINISTRATIVA_INSTRUCTION
                + "\n\nAo final, fora do conteudo da minuta e apenas para processamento interno, "
                "inclua obrigatoriamente estas duas linhas:\n"
                "ASSUNTO: [resumo curto com medicamento principal, CID quando houver e paciente]\n"
                "CONFIDENCE_SCORE: [numero de 0.0 a 1.0]"
            )

            contents = []

            filter_list = self.filter_files_from_knowledge_base(file_uri=file_uri, mime_type=mime_type)
            if not filter_list:
                filter_list = []
            
            #Limitar documentos para evitar estouro de limite de tokens no modelo
            if len(filter_list) > MAX_KNOWLEDGE_BASE_FILES:
                print(f"GeminiService - Limitando arquivos selecionados de {len(filter_list)} para {MAX_KNOWLEDGE_BASE_FILES} para evitar estouro de tokens.")
                filter_list = filter_list[:MAX_KNOWLEDGE_BASE_FILES]

            # Recuperar apenas os trechos relevantes dos arquivos selecionados
            rag_result = self.rag.rag(
                query=process_text or prompt or "medicamentos, CIDs e quesitos do processo",
                selected_files=filter_list,
                top_k=5,
            )

            #Buscar arquivos da base de conhecimento
            # if project_id and bucket_name and knowledge_base_dir:
            #     contents.append("BASE DE CONHECIMENTO (Leis, protocolos clínicos, CID e normas técnicas):")
            #     for file_name in filter_list:
            #         gcs_uri = f"gs://{bucket_name}/{file_name}"
            #         contents.append(
            #             types.Part.from_uri(file_uri=gcs_uri, mime_type="application/pdf")
            #         )

            if rag_result["context"]:
                contents.append("TRECHOS RELEVANTES DA BASE DE CONHECIMENTO:")
                contents.append(rag_result["context"])

            #Incluir o arquivo alvo a ser analisado
            if file_uri:
                contents.append("PROCESSO ADMINISTRATIVO (Pedido do medicamento):")
                contents.append(types.Part.from_uri(file_uri=file_uri, mime_type=mime_type))

            if process_text:
                contents.append("TEXTO EXTRAIDO DO PROCESSO VIA OCR:")
                contents.append(process_text)

            current_time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
            print(f"GeminiService - Iniciando chamada à API de análise em: {current_time_str}")
            start_time = time.time()
            
            config_args = {
                "system_instruction": system_instruction,
                "temperature": 0.1,
                "top_p": 0.45,
                "top_k": 10,
            }

            #Função interna isolada para podermos aplicar o timeout
            def _chamar_api_analise():
                return self.client.models.generate_content(
                    model=model,
                    contents=contents,
                    config=types.GenerateContentConfig(**config_args)
                )

            #Aplica o timeout.
            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(_chamar_api_analise)
                    response = future.result(timeout=180) #Trava por no máximo 180s.
            except concurrent.futures.TimeoutError:
                print("error: A IA travou e não respondeu a análise em 180s.")
                return None

            end_time = time.time()
            print(f"Tempo de resposta da API (Análise): {end_time - start_time:.2f} segundos")
            
            if not response or not response.text:
                return None
                
            raw_text = response.text
            
            # Extrair a confiança a partir da tag CONFIDENCE_SCORE: [nota]
            confidence = 0.90  # fallback
            confidence_match = re.search(r"CONFIDENCE_SCORE:\s*([\d\.]+)", raw_text, re.IGNORECASE)
            if confidence_match:
                try:
                    confidence = float(confidence_match.group(1))
                    confidence = max(0.0, min(1.0, confidence))  # Garante entre 0.0 e 1.0
                    print(f"GeminiService - Confiança extraída do texto: {confidence:.4f}")
                except ValueError:
                    pass

            # Extrair o Assunto a partir da tag ASSUNTO: [texto]
            assunto = "Assunto não identificado"
            assunto_match = re.search(r"^\s*ASSUNTO:\s*(.*)$", raw_text, re.IGNORECASE | re.MULTILINE)
            if assunto_match:
                assunto = assunto_match.group(1).strip()
            
            # Remover a tag CONFIDENCE_SCORE, a tag ASSUNTO e as linhas associadas do texto limpo retornado
            clean_text = re.sub(r"^\s*CONFIDENCE_SCORE:\s*[\d\.]+\s*$", "", raw_text, flags=re.IGNORECASE | re.MULTILINE)
            clean_text = re.sub(r"^\s*ASSUNTO:\s*.*$", "", clean_text, flags=re.IGNORECASE | re.MULTILINE)
            clean_text = clean_minuta_text(clean_text)
            
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

    # Método para gerar apenas a Minuta baseado em um Resumo Técnico
    def generate_minuta_only(self, resumo_tecnico_json, model="gemini-3.5-flash"):
        """
        Recebe o JSON do Resumo Técnico salvo no banco e gera apenas o texto da Minuta.
        Não faz requisições pesadas aos PDFs originais.
        """
        try:
            system_instruction = _SES_PE_RESPOSTA_ADMINISTRATIVA_INSTRUCTION

            prompt_content = f"RESUMO TÉCNICO:\n{resumo_tecnico_json}"

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

            return clean_minuta_text(response.text)
            
        except Exception as e:
            print(f"⛔ Erro ao gerar apenas a minuta no GeminiService: {e}")
            return None