import os
import time
import math
import re
import concurrent.futures

from google import genai
from google.genai import types
from google.cloud import storage

class GeminiService:
    def __init__(self):
        #A chave de API deve estar no .env como GEMINI_API_KEY
        self.api_key = os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY não encontrada nas variáveis de ambiente.")
        self.client = genai.Client(api_key=self.api_key, vertexai=True)

    def generate_response(self, prompt, model="gemini-3.5-pro"):
        try:
            response = self.client.models.generate_content(
                model=model,
                contents=prompt,
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
            "Analise o processo administrativo em anexo e selecione na base de conhecimento em anexo "
            "quais documentos devem ser considerados para análise da possibilidade de concessão de medicamento ao paciente.\n"
            "IMPORTANTE: Selecione no MÁXIMO os 4 documentos mais relevantes e diretamente relacionados ao medicamento principal solicitado. "
            "Evite selecionar documentos para comorbidades secundárias não relacionadas diretamente ao pedido principal.\n"
            "NÃO selecione catálogos gerais e extensos como 'CID-10-LISTA-PDF.pdf', 'rename-2024.pdf' ou 'REESME-2025.pdf' para evitar exceder o limite de tokens.\n"
            "Retorne apenas uma lista com os caminhos dos arquivos selecionados, nenhum outro texto a mais."
            "Retorne apenas documentos da lista que você receber como entrada."
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
                print(blob.name)

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
                    top_p=0.95,
                    top_k=40
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
    ):
        try:
            project_id = os.getenv("GCS_PROJECT_ID")
            bucket_name = os.getenv("GCS_BUCKET_NAME")
            knowledge_base_dir = os.getenv("GCS_BUCKET_KNOWLEDGE_BASE")

            system_instruction = (
                "Você atua como um analista técnico na Secretaria de Saúde do Estado de Pernambuco, Brasil. "
                "Seu trabalho é, a partir da legislação vigente, analisar o pedido de medicamentos realizado através de "
                "um processo administrativo, em que médicos especialistas solicitam medicamentos a partir da situação "
                "clínica de um paciente.\n\n"
                "DIRETRIZES PARA A ANÁLISE:\n"
                "1. Analise se o paciente tem direito ao medicamento e se o Estado de Pernambuco pode fornecê-lo a "
                "partir das leis, protocolos clínicos, tabela CID e normas técnicas que serão fornecidas na base de conhecimento.\n"
                "2. Não poderão ser fornecidos medicamentos não previstos nos protocolos clínicos. Caso o medicamento solicitado não possa ser fornecido, inclua obrigatoriamente os possíveis medicamentos "
                "alternativos (fornecidos pelo SUS) para o CID do paciente.\n"
                "3. Inclua na sua resposta todos os trechos exatos dos documentos utilizados para produzir a análise.\n"
                "4. Inclua o nome do paciente, o nome do médico solicitante e o nome do medicamento.\n"
                "5. Crie em sua resposta uma seção específica em que o resultado da análise seja apresentada com termos amigáveis para uma pessoa não técnica.\n"
                "6. CÁLCULO DE CONFIANÇA: Avalie criticamente a qualidade e a clareza das informações fornecidas no processo. "
                "NÃO retorne a nota 1.0 automaticamente. Siga rigorosamente esta métrica de 0.0 a 1.0:\n"
                "   - 0.9 a 1.0: Todos os documentos estão legíveis, completos, e o CID/medicamento batem perfeitamente com os protocolos anexados.\n"
                "   - 0.7 a 0.8: As informações são boas, mas faltam dados menores ou o enquadramento no protocolo exige uma interpretação indireta.\n"
                "   - 0.4 a 0.6: Faltam documentos cruciais (ex: laudo médico incompleto) ou existem contradições significativas no pedido.\n"
                "   - Abaixo de 0.4: É impossível realizar uma análise técnica conclusiva com os dados fragmentados que foram enviados.\n"
                "7. RESUMO DO ASSUNTO: Extraia um resumo curto, preciso e informativo em uma única linha sobre o processo, "
                "mencionando o medicamento principal solicitado, o CID da doença e o nome do paciente. "
                "Exemplo: 'Solicitação de Adalimumabe 40mg para Artrite Reumatoide (CID M05.8) - Paciente: João da Silva'.\n\n"
                "Ao final do seu texto, inclua obrigatoriamente estes dois blocos estruturados no formato exato:\n"
                "ASSUNTO: [Seu resumo de uma linha aqui]\n"
                "CONFIDENCE_SCORE: [número]"
            )

            contents = []

            filter_list = self.filter_files_from_knowledge_base(file_uri=file_uri, mime_type=mime_type)
            if not filter_list:
                filter_list = []
            
            #Limitar a no máximo 4 documentos para evitar estouro de limite de tokens no modelo
            if len(filter_list) > 4:
                print(f"GeminiService - Limitando arquivos selecionados de {len(filter_list)} para 4 para evitar estouro de tokens.")
                filter_list = filter_list[:4]

            #Buscar arquivos da base de conhecimento
            if project_id and bucket_name and knowledge_base_dir:
                contents.append("BASE DE CONHECIMENTO (Leis, protocolos clínicos, CID e normas técnicas):")
                for file_name in filter_list:
                    gcs_uri = f"gs://{bucket_name}/{file_name}"
                    contents.append(
                        types.Part.from_uri(file_uri=gcs_uri, mime_type="application/pdf")
                    )

            #Incluir o arquivo alvo a ser analisado
            if file_uri:
                contents.append("PROCESSO ADMINISTRATIVO (Pedido do medicamento):")
                contents.append(types.Part.from_uri(file_uri=file_uri, mime_type=mime_type))

            current_time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
            print(f"GeminiService - Iniciando chamada à API de análise em: {current_time_str}")
            start_time = time.time()
            
            config_args = {
                "systemInstruction": system_instruction,
                "temperature": 0.1,  
                "topP": 0.95,
                "topK": 40,
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
            assunto_match = re.search(r"ASSUNTO:\s*(.*?)(?=\n|$)", raw_text, re.IGNORECASE)
            if assunto_match:
                assunto = assunto_match.group(1).strip()
            
            # Remover a tag CONFIDENCE_SCORE, a tag ASSUNTO e as linhas associadas do texto limpo retornado
            clean_text = re.sub(r"CONFIDENCE_SCORE:\s*[\d\.]+", "", raw_text, flags=re.IGNORECASE)
            clean_text = re.sub(r"ASSUNTO:\s*.*?(?=\n|$)", "", clean_text, flags=re.IGNORECASE).strip()
            
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