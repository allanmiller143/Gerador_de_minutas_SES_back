from __future__ import annotations

import logging
import os

import fitz

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
    SYNC_PAGE_LIMIT = 15
    SYNC_CHUNK_PAGE_LIMIT = 14

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
    def _process_raw_document(
        cls,
        documentai,
        client,
        name: str,
        content: bytes,
        mime_type: str,
    ) -> PdfExtractionResult:
        try:
            request = documentai.ProcessRequest(
                name=name,
                raw_document=documentai.RawDocument(
                    content=content,
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
    def _is_page_limit_error(cls, exc: Exception) -> bool:
        message = str(exc)
        return "PAGE_LIMIT_EXCEEDED" in message or "page limit" in message.lower()

    @classmethod
    def _extract_pdf_in_chunks(
        cls,
        documentai,
        client,
        name: str,
        pdf_content: bytes,
        mime_type: str,
    ) -> PdfExtractionResult:
        texts = []
        try:
            with fitz.open(stream=pdf_content, filetype="pdf") as source:
                page_count = source.page_count
                chunk_count = (page_count + cls.SYNC_CHUNK_PAGE_LIMIT - 1) // cls.SYNC_CHUNK_PAGE_LIMIT
                logging.info(
                    "PDF com %s paginas excede o limite online do Document AI; processando em %s parte(s) de ate %s paginas.",
                    page_count,
                    chunk_count,
                    cls.SYNC_CHUNK_PAGE_LIMIT,
                )

                for index, start_page in enumerate(
                    range(0, page_count, cls.SYNC_CHUNK_PAGE_LIMIT),
                    start=1,
                ):
                    end_page = min(start_page + cls.SYNC_CHUNK_PAGE_LIMIT, page_count)
                    chunk = fitz.open()
                    try:
                        chunk.insert_pdf(source, from_page=start_page, to_page=end_page - 1)
                        chunk_bytes = chunk.write()
                    finally:
                        chunk.close()

                    extraction = cls._process_raw_document(
                        documentai,
                        client,
                        name,
                        chunk_bytes,
                        mime_type,
                    )
                    texts.append(extraction.text)
                    logging.info(
                        "Document AI OCR parte %s/%s concluida para paginas %s-%s com %s caracteres.",
                        index,
                        chunk_count,
                        start_page + 1,
                        end_page,
                        extraction.text_chars,
                    )
        except PdfExtractionError:
            raise
        except Exception as exc:
            raise PdfExtractionError(
                f"Falha ao preparar PDF para OCR em partes: {exc}"
            ) from exc

        text = "\n\n".join(texts).strip()
        if not text:
            raise PdfExtractionError("Document AI nao retornou texto extraido do PDF.")

        result = PdfExtractionResult(text=text, text_chars=len(text))
        return result

    @classmethod
    def extract_text(cls, pdf_content: bytes, mime_type: str = "application/pdf") -> PdfExtractionResult:
        documentai, client, name = cls._client_and_processor_name()

        if mime_type == "application/pdf":
            page_count = None
            try:
                with fitz.open(stream=pdf_content, filetype="pdf") as source:
                    page_count = source.page_count
            except Exception as exc:
                logging.warning("Nao foi possivel contar paginas do PDF antes do OCR: %s", exc)
            if page_count and page_count > cls.SYNC_PAGE_LIMIT:
                return cls._extract_pdf_in_chunks(
                    documentai,
                    client,
                    name,
                    pdf_content,
                    mime_type,
                )

        try:
            return cls._process_raw_document(documentai, client, name, pdf_content, mime_type)
        except PdfExtractionError as exc:
            if mime_type == "application/pdf" and cls._is_page_limit_error(exc):
                logging.warning(
                    "Document AI recusou o PDF por limite de paginas; tentando novamente em partes de ate %s paginas.",
                    cls.SYNC_CHUNK_PAGE_LIMIT,
                )
                return cls._extract_pdf_in_chunks(
                    documentai,
                    client,
                    name,
                    pdf_content,
                    mime_type,
                )
            raise

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
