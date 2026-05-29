import os

from google import genai
from google.genai import types


class GeminiService:
    def __init__(self):
        # A chave de API deve estar no .env como GEMINI_API_KEY
        self.api_key = os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY não encontrada nas variáveis de ambiente.")
        self.client = genai.Client(api_key=self.api_key)
        
    def generate_response(self, prompt, model="gemini-2.5-pro"):
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

    def generate_response_with_file(
        self,
        prompt=None,
        model="gemini-2.5-pro",
        file_uri=None,
        mime_type=None,
    ):
        try:
            contents = prompt

            if file_uri:
                parts = []
                if prompt:
                    parts.append(prompt)
                parts.append(types.Part.from_uri(file_uri=file_uri, mime_type=mime_type))
                contents = parts

            response = self.client.models.generate_content(
                model=model,
                contents=contents,
            )
            return response.text
        except Exception as e:
            print(f"Erro ao chamar a API do Gemini: {e}")
            return None
