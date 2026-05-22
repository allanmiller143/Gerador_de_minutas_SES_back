from flask import Blueprint, jsonify

from app.utils.mock_data_service import (
    JURISPRUDENCIAS,
    SEIS,
    gerar_minuta,
    get_jurisprudencias_for_sei,
    get_resumo_tecnico_for_sei,
    get_sei,
    read_mock_pdf_bytes,
    with_pdf_metadata,
)

mock_data_bp = Blueprint("mock_data", __name__, url_prefix="/api")


@mock_data_bp.route("/seis", methods=["GET"])
def list_seis():
    return jsonify({"seis": [with_pdf_metadata(sei) for sei in SEIS]}), 200


@mock_data_bp.route("/seis/<sei_id>", methods=["GET"])
def detail_sei(sei_id: str):
    sei = get_sei(sei_id)
    if not sei:
        return jsonify({"error": "SEI não encontrado."}), 404

    return (
        jsonify(
            {
                "sei": with_pdf_metadata(sei),
                "jurisprudencias": get_jurisprudencias_for_sei(sei),
                "resumoTecnico": get_resumo_tecnico_for_sei(sei),
                "minuta": gerar_minuta(sei["numero"], sei["assunto"]),
            }
        ),
        200,
    )


@mock_data_bp.route("/seis/<sei_id>/pdf", methods=["GET"])
def get_sei_pdf(sei_id: str):
    sei = get_sei(sei_id)
    if not sei:
        return jsonify({"error": "SEI não encontrado."}), 404

    try:
        pdf_content = read_mock_pdf_bytes()
    except FileNotFoundError:
        return jsonify({"error": "PDF mockado não encontrado."}), 404

    return (
        jsonify(
            {
                "filename": sei["documentoPdf"]["filename"] if "documentoPdf" in sei else "exemplo-processo-2.pdf",
                "mime_type": "application/pdf",
                "size": len(pdf_content),
                "pdf_bytes": list(pdf_content),
            }
        ),
        200,
    )


@mock_data_bp.route("/jurisprudencias", methods=["GET"])
def list_jurisprudencias():
    return jsonify({"jurisprudencias": JURISPRUDENCIAS}), 200
