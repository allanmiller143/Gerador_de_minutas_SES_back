import logging
from flask import Blueprint, jsonify, request

from app.utils.pdf_extraction_service import (
    PdfExtractionError,
    PdfExtractionService,
    PdfValidationError,
)
from app.utils.document_ai_ocr_service import DocumentAiOcrService
from app.utils.resumo_service import DEFAULT_MODEL, ResumoService
from app.utils.adk_resumo_service import DEFAULT_ADK_MODEL, AdkResumoService
from app.utils.support_document_service import SupportDocumentService

logger = logging.getLogger(__name__)

resumo_bp = Blueprint("resumo", __name__, url_prefix="/api")


def _parse_bool(value, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return bool(value)


def _parse_max_trechos(value, default: int = 12) -> int:
    if value is None:
        return default
    if not isinstance(value, int):
        raise PdfValidationError(
            "O campo 'options.max_trechos_suporte' deve ser um inteiro entre 1 e 30."
        )
    if value < 1 or value > 30:
        raise PdfValidationError(
            "O campo 'options.max_trechos_suporte' deve ser um inteiro entre 1 e 30."
        )
    return value


@resumo_bp.route("/resumo", methods=["POST"])
def resumo():
    data = request.get_json(silent=True) or {}
    pdf_bytes = data.get("pdf_bytes")
    filename = data.get("filename")
    model = data.get("model")
    options = data.get("options") or {}

    if not isinstance(options, dict):
        return jsonify({"error": "O campo 'options' deve ser um objeto JSON válido."}), 400

    filename = filename if isinstance(filename, str) and filename.strip() else "arquivo.pdf"
    model = model if isinstance(model, str) and model.strip() else DEFAULT_MODEL

    try:
        include_support_docs = _parse_bool(options.get("usar_documentacao_suporte"), True)
        max_trechos_suporte = _parse_max_trechos(options.get("max_trechos_suporte"), 12)
        include_minuta = _parse_bool(options.get("incluir_minuta_parecer"), True)
        use_adk = _parse_bool(options.get("use_adk"), True)
    except PdfValidationError as exc:
        return jsonify({"error": str(exc)}), 400

    try:
        pdf_content = PdfExtractionService.from_json_bytes(pdf_bytes)
    except PdfValidationError as exc:
        return jsonify({"error": str(exc)}), 400
    except PdfExtractionError as exc:
        return jsonify({"error": str(exc)}), 422

    try:
        extraction = DocumentAiOcrService.extract_text_with_fallback(pdf_content)
    except PdfExtractionError as exc:
        return jsonify({"error": str(exc)}), 422

    support_context = ""
    if include_support_docs:
        support_context = SupportDocumentService().build_context(
            max_trechos_suporte=max_trechos_suporte
        )

    resumo_payload = None
    engine_used = "legacy"

    # Tenta executar primeiro via Google ADK com Tool Calling para alto desempenho
    if use_adk:
        try:
            adk_model = model if model and model != DEFAULT_MODEL else DEFAULT_ADK_MODEL
            logger.info("[PIPELINE_MOTOR: GOOGLE_ADK] Rota /api/resumo executando via AdkResumoService")
            resumo_payload = AdkResumoService().generate_resumo(
                process_text=extraction.text,
                support_context=support_context,
                model=adk_model,
                include_minuta=include_minuta,
            )
            if resumo_payload:
                engine_used = "google-adk"
                logger.info("[PIPELINE_MOTOR: GOOGLE_ADK] Rota /api/resumo concluída com sucesso via Google ADK")
        except Exception as adk_err:
            logger.warning(f"[PIPELINE_FALLBACK: ADK -> LEGADO] Falha na execução do ADK na rota /api/resumo: {adk_err}. Aplicando fallback legado...")
            resumo_payload = None

    # Fallback seguro para o ResumoService tradicional se ADK não for usado ou falhar
    if not resumo_payload:
        try:
            logger.info("[PIPELINE_MOTOR: LEGADO] Rota /api/resumo executando via ResumoService (pipeline legado)")
            resumo_payload = ResumoService().generate_resumo(
                process_text=extraction.text,
                support_context=support_context,
                model=model,
                include_minuta=include_minuta,
            )
            engine_used = "legacy"
            logger.info("[PIPELINE_MOTOR: LEGADO] Rota /api/resumo concluída com sucesso via ResumoService (pipeline legado)")
        except Exception:
            return jsonify({"error": "Falha ao gerar resumo técnico."}), 500

    if not resumo_payload:
        return jsonify({"error": "Falha ao gerar resumo técnico."}), 500

    return (
        jsonify(
            {
                "resumo": resumo_payload,
                "metadata": {
                    "filename": filename,
                    "text_chars": extraction.text_chars,
                    "model": model,
                    "engine": engine_used,
                },
            }
        ),
        200,
    )
