import os
import time

from google import genai
from google.genai import types

from google.cloud import storage


class GeminiService:
    def __init__(self):
        # A chave de API deve estar no .env como GEMINI_API_KEY
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
            # Log do erro ou tratamento adequado
            print(f"Erro ao chamar a API do Gemini: {e}")
            return None

    def list_blobs(self, project_id, bucket_name, knowledge_base_dir):
        storage_client = storage.Client(project=project_id)
        blobs = storage_client.list_blobs(bucket_name, prefix=knowledge_base_dir)

        file_list = []

        for blob in blobs:
            if blob.name.endswith("/") or not blob.name:
                continue

            # Construir URI correta do GCS
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
            "IMPORTANTE: NÃO selecione catálogos gerais e extensos como 'CID-10-LISTA-PDF.pdf', 'rename-2024.pdf' ou 'REESME-2025.pdf' para evitar exceder o limite de tokens. "
            "Concentre-se em selecionar estritamente o Protocolo Clínico (PCDT) específico, a Norma Técnica e a Orientação ao Usuário correspondentes à doença do paciente.\n"
            "Retorne apenas uma lista com os caminhos dos arquivos selecionados, nenhum outro texto a mais."
        )

        contents.append("BASE DE CONHECIMENTO (Leis, protocolos clínicos, CID e normas técnicas):")
        if project_id and bucket_name and knowledge_base_dir:
            client = storage.Client(project=project_id)
            bucket = client.bucket(bucket_name)
            blobs = bucket.list_blobs(prefix=knowledge_base_dir)

            for blob in blobs:
                if blob.name.endswith("/") or not blob.name:
                    continue
                # Adicionar apenas o nome do arquivo
                contents.append(f"{blob.name}")

            # Incluir o arquivo alvo a ser analisado
            if file_uri:
                contents.append("PROCESSO ADMINISTRATIVO (Pedido do medicamento):")
                contents.append(types.Part.from_uri(file_uri=file_uri, mime_type=mime_type))

            # print("GeminiService - Conteúdo a ser enviado para o modelo:")
            #print(contents)

            # 3. Chamar a API separando as instruções de sistema do conteúdo e configurando os parâmetros de geração
            current_time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
            print(f"GeminiService - Iniciando chamada à API em: {current_time_str}")
            start_time = time.time()
            response = self.client.models.generate_content(
                model=model,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=filter_instruction,
                    temperature=0.1,  # Temperatura baixa para maior precisão em análises técnicas
                    top_p=0.95,
                    top_k=40
                )
            )
            end_time = time.time()
            print(f"GeminiService - Tempo de resposta da API: {end_time - start_time:.2f} segundos")

            if not response.text:
                return []

            # Parse the text to extract file names into a list
            files = []
            for line in response.text.strip().split('\n'):
                clean_name = line.strip(" -*`")
                if clean_name:
                    files.append(clean_name)

            #print(f"GeminiService - Arquivos selecionados: {files}")
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

            # 1. Definir o System Prompt (Instruções de sistema, persona e comportamento)
            system_instruction = (
                "Você atua como um analista técnico na Secretaria de Saúde do Estado de Pernambuco, Brasil. "
                "Seu trabalho é, a partir da legislação vigente, analisar o pedido de medicamentos realizado através de "
                "um processo administrativo, em que médicos especialistas solicitam medicamentos a partir da situação "
                "clínica de um paciente.\n\n"
                "DIRETRIZES PARA A ANÁLISE:\n"
                "1. Analise se o paciente tem direito ao medicamento e se o Estado de Pernambuco pode fornecê-lo a "
                "partir das leis, protocolos clínicos, tabela CID e normas técnicas que serão fornecidas na base de conhecimento.\n"
                "2. Não poderão ser fornecidos medicamentos não previstos nos protocolos clínicos. Caso o medicamento solicitado não possa ser fornecido, inclua obrigatoriamente os possíveis medicamentos "
                "alternativos (fornecidos pelo SUS) para a doença do paciente.\n"
                "3. Inclua na sua resposta todos os trechos exatos dos documentos utilizados para produzir a análise.\n"
                "4. Inclua o nome do paciente, o nome do médico solicitante e o nome do medicamento.\n"
                "5. Crie em sua resposta uma seção específica em que o resultado da análise seja apresentada com termos amigáveis para uma pessoa não técnica."
            )

            # 2. Construir o Conteúdo (Base de conhecimento e processo do paciente)
            contents = []

            filter_list = self.filter_files_from_knowledge_base(file_uri=file_uri, mime_type=mime_type)
            if not filter_list:
                filter_list = []

            # Buscar arquivos da base de conhecimento (prefixo corrigido)
            if project_id and bucket_name and knowledge_base_dir:
                contents.append("BASE DE CONHECIMENTO (Leis, protocolos clínicos, CID e normas técnicas):")
                for file_name in filter_list:
                    # Construir URI correta do GCS
                    gcs_uri = f"gs://{bucket_name}/{file_name}"
                    contents.append(
                        types.Part.from_uri(file_uri=gcs_uri, mime_type="application/pdf")
                    )

            # Incluir o arquivo alvo a ser analisado
            if file_uri:
                contents.append("PROCESSO ADMINISTRATIVO (Pedido do medicamento):")
                contents.append(types.Part.from_uri(file_uri=file_uri, mime_type=mime_type))

            # print("GeminiService - Conteúdo a ser enviado para o modelo:")
            #print(contents)

            # 3. Chamar a API separando as instruções de sistema do conteúdo e configurando os parâmetros de geração
            current_time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
            print(f"GeminiService - Iniciando chamada à API em: {current_time_str}")
            start_time = time.time()
            response = self.client.models.generate_content(
                model=model,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=0.1,  # Temperatura baixa para maior precisão em análises técnicas
                    top_p=0.95,
                    top_k=40
                )
            )
            end_time = time.time()
            print(f"GeminiService - Tempo de resposta da API: {end_time - start_time:.2f} segundos")
            return response.text
        except Exception as e:
            print(f"Erro ao chamar a API do Gemini: {e}")
            return None
