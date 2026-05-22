from flask import Blueprint, jsonify, request

from app.utils.pdf_extraction_service import (
    PdfExtractionError,
    PdfExtractionService,
    PdfValidationError,
)
from app.utils.resumo_service import DEFAULT_MODEL, ResumoService
from app.utils.support_document_service import SupportDocumentService

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
    except PdfValidationError as exc:
        return jsonify({"error": str(exc)}), 400

    try:
        pdf_content = PdfExtractionService.from_json_bytes(pdf_bytes)
    except PdfValidationError as exc:
        return jsonify({"error": str(exc)}), 400
    except PdfExtractionError as exc:
        return jsonify({"error": str(exc)}), 422

    try:
        extraction = PdfExtractionService.extract_text(pdf_content)
    except PdfExtractionError as exc:
        return jsonify({"error": str(exc)}), 422

    support_context = ""
    if include_support_docs:
        support_context = SupportDocumentService().build_context(
            max_trechos_suporte=max_trechos_suporte
        )

    try:
        resumo_payload = ResumoService().generate_resumo(
            process_text=extraction.text,
            support_context=support_context,
            model=model,
            include_minuta=include_minuta,
        )
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
                },
            }
        ),
        200,
    )
