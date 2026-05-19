import os
from google import genai
from flask import current_app

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
