import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from google.oauth2 import service_account
from google import genai

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.utils.gemini_service import GeminiService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Execute a prompt using Google Gemini and print the response."
    )
    parser.add_argument(
        "prompt",
        nargs="?",
        default=None,
        help="Prompt to send to Gemini. Defaults to GEMINI_TEST_PROMPT or a fallback prompt.",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("GEMINI_TEST_MODEL", "gemini-3.5-flash"),
        help="Gemini model to use. Defaults to GEMINI_TEST_MODEL or gemini-3.5-flash.",
    )
    parser.add_argument(
        "--file-uri",
        default=os.getenv("GCS_FILE_URI"),
        help="Optional GCS file URI to include in the Gemini request. Example: gs://bucket/path/file.pdf",
    )
    parser.add_argument(
        "--mime-type",
        default=None,
        help="Optional MIME type for the GCS file URI. If omitted, Gemini will infer it from the URI.",
    )
    return parser


def main() -> int:
    load_dotenv()

    parser = build_parser()
    args = parser.parse_args()

    prompt = args.prompt
    if prompt is None:
        prompt = os.getenv(
            "GEMINI_TEST_PROMPT",
            "Analise este arquivo e descreva o conteúdo." if args.file_uri else "Qual a capital da França?",
        )

    service = GeminiService()
    # response = service.filter_files_from_knowledge_base(
    #     model=args.model,
    #     file_uri=args.file_uri,
    #     mime_type=args.mime_type,
    # )

    # response = service.generate_response_with_file(
    #     prompt,
    #     model=args.model,
    #     file_uri=args.file_uri,
    #     mime_type=args.mime_type,
    # )

    caminho_json = "/home/emanoel/Git/ses_farmacia/Gerador_de_minutas_SES_back/br-ssep-cld-01-7fb62b612e2c.json"

    # 2. Carrega as credenciais a partir do arquivo
    credenciais = service_account.Credentials.from_service_account_file(caminho_json)

    # 3. Inicializa o cliente do Gemini usando as credenciais e ativando o Vertex AI
    # IMPORTANTE: Contas de serviço exigem 'vertexai=True' e o ID do projeto correto
    client = genai.Client(
        vertexai=True,
        project=credenciais.project_id, # Puxa automaticamente o ID do projeto de dentro do JSON
        location="us-central1",          # Defina a região que preferir (ex: us-central1)
        credentials=credenciais
    )

    # 4. Teste a chamada ao modelo
    response = client.models.generate_content(
        model='gemini-3.5-flash',
        contents='Olá! Autenticação via JSON funcionou?',
    )

    if response is None:
        print("Failed to get a response from Gemini.", flush=True)
        return 1

    print(response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
