from __future__ import annotations

from dataclasses import dataclass

import fitz


@dataclass
class PdfExtractionResult:
    text: str
    text_chars: int


class PdfValidationError(ValueError):
    pass


class PdfExtractionError(RuntimeError):
    pass


class PdfExtractionService:
    @staticmethod
    def from_json_bytes(pdf_bytes: list[int]) -> bytes:
        if not isinstance(pdf_bytes, list) or not pdf_bytes:
            raise PdfValidationError(
                "O campo 'pdf_bytes' é obrigatório e deve ser uma lista de inteiros entre 0 e 255."
            )
        if not all(isinstance(item, int) and 0 <= item <= 255 for item in pdf_bytes):
            raise PdfValidationError(
                "O campo 'pdf_bytes' é obrigatório e deve ser uma lista de inteiros entre 0 e 255."
            )
        payload = bytes(pdf_bytes)
        if not payload.startswith(b"%PDF"):
            raise PdfExtractionError("Não foi possível extrair texto do PDF informado.")
        return payload

    @staticmethod
    def extract_text(pdf_content: bytes) -> PdfExtractionResult:
        try:
            with fitz.open(stream=pdf_content, filetype="pdf") as doc:
                chunks = []
                for page in doc:
                    chunks.append(page.get_text("text"))
            text = "\n".join(chunks).strip()
            if not text:
                raise PdfExtractionError("Não foi possível extrair texto do PDF informado.")
            return PdfExtractionResult(text=text, text_chars=len(text))
        except PdfExtractionError:
            raise
        except Exception as exc:
            raise PdfExtractionError("Não foi possível extrair texto do PDF informado.") from exc
