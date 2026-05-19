import os
from dotenv import load_dotenv
from google import genai

# carrega o .env
load_dotenv()

# pega a chave do .env
api_key = os.getenv("GEMINI_API_KEY")

client = genai.Client(api_key=api_key)

response = client.models.generate_content(
    model="gemini-2.5-pro",
    contents="Qual a capital da França?",
)

print(response.text)