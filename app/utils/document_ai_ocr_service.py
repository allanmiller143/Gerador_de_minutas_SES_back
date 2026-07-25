from __future__ import annotations

import logging
import os

from app.utils.pdf_extraction_service import (
    PdfExtractionError,
    PdfExtractionResult,
    PdfExtractionService,
)


class DocumentAiOcrService:
    PROCESSOR_ID_ENV = "DOCUMENT_AI_OCR_PROCESSOR_ID"
    PROJECT_ID_ENV = "GCS_PROJECT_ID"
    LOCATION_ENV = "DOCUMENT_AI_OCR_LOCATION"
    CREDENTIALS_ENV = "GOOGLE_APPLICATION_CREDENTIALS"

    @classmethod
    def is_configured(cls) -> bool:
        return bool(os.getenv(cls.PROCESSOR_ID_ENV) and os.getenv(cls.PROJECT_ID_ENV))

    @classmethod
    def _client_and_processor_name(cls):
        try:
            from google.cloud import documentai_v1 as documentai
        except ImportError as exc:
            raise PdfExtractionError(
                "Dependencia google-cloud-documentai nao instalada."
            ) from exc

        processor_id = os.getenv(cls.PROCESSOR_ID_ENV)
        project_id = os.getenv(cls.PROJECT_ID_ENV)
        location = os.getenv(cls.LOCATION_ENV) or "us"

        if not processor_id or not project_id:
            raise PdfExtractionError(
                f"Document AI nao configurado. Defina {cls.PROCESSOR_ID_ENV} e {cls.PROJECT_ID_ENV}."
            )

        client_options = {"api_endpoint": f"{location}-documentai.googleapis.com"}
        client_kwargs = {"client_options": client_options}

        credentials_path = os.getenv(cls.CREDENTIALS_ENV)
        if credentials_path:
            try:
                from google.oauth2 import service_account

                client_kwargs["credentials"] = (
                    service_account.Credentials.from_service_account_file(credentials_path)
                )
            except Exception as exc:
                raise PdfExtractionError(
                    f"Falha ao carregar credenciais do Document AI em {cls.CREDENTIALS_ENV}: {exc}"
                ) from exc

        client = documentai.DocumentProcessorServiceClient(**client_kwargs)
        name = client.processor_path(project_id, location, processor_id)
        return documentai, client, name

    @classmethod
    def extract_text(cls, pdf_content: bytes, mime_type: str = "application/pdf") -> PdfExtractionResult:
        documentai, client, name = cls._client_and_processor_name()

        try:
            request = documentai.ProcessRequest(
                name=name,
                raw_document=documentai.RawDocument(
                    content=pdf_content,
                    mime_type=mime_type,
                ),
            )
            result = client.process_document(request=request)
            text = (result.document.text or "").strip()
        except Exception as exc:
            raise PdfExtractionError(f"Falha ao executar OCR no Document AI: {exc}") from exc

        if not text:
            raise PdfExtractionError("Document AI nao retornou texto extraido do PDF.")
        return PdfExtractionResult(text=text, text_chars=len(text))

    @classmethod
    def extract_text_from_gcs(
        cls,
        gcs_uri: str,
        mime_type: str = "application/pdf",
    ) -> PdfExtractionResult:
        documentai, client, name = cls._client_and_processor_name()

        try:
            request = documentai.ProcessRequest(
                name=name,
                gcs_document=documentai.GcsDocument(
                    gcs_uri=gcs_uri,
                    mime_type=mime_type,
                ),
            )
            result = client.process_document(request=request)
            text = (result.document.text or "").strip()
        except Exception as exc:
            raise PdfExtractionError(f"Falha ao executar OCR no Document AI: {exc}") from exc

        if not text:
            raise PdfExtractionError("Document AI nao retornou texto extraido do PDF.")
        return PdfExtractionResult(text=text, text_chars=len(text))

    @classmethod
    def extract_text_with_fallback(
        cls,
        pdf_content: bytes,
        mime_type: str = "application/pdf",
    ) -> PdfExtractionResult:
        if cls.is_configured():
            try:
                return cls.extract_text(pdf_content, mime_type=mime_type)
            except PdfExtractionError as exc:
                logging.warning("Document AI OCR falhou; usando extracao local: %s", exc)

        return PdfExtractionService.extract_text(pdf_content)
