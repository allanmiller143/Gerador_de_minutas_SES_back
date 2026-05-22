from flask import Blueprint, jsonify

from app.utils.pdf_extraction_service import PdfExtractionError, PdfExtractionService
from app.utils.resumo_service import DEFAULT_MODEL, ResumoService
from app.utils.support_document_service import SupportDocumentService
from app.utils.mock_data_service import (
    JURISPRUDENCIAS,
    SEIS,
    get_jurisprudencias_for_sei,
    get_sei,
    read_mock_pdf_bytes,
    with_pdf_metadata,
)

mock_data_bp = Blueprint("mock_data", __name__, url_prefix="/api")


def _empty_resumo_tecnico(error_message: str | None = None) -> dict:
    resumo = ResumoService._normalize_payload({})
    if error_message:
        resumo["confronto_documentacao_suporte"]["observacoes"] = [error_message]
        resumo["insumo_parecer"]["pendencias_documentais"] = ["Gerar novamente o resumo técnico após corrigir a falha de processamento."]
        resumo["fontes_consultadas"] = ["Falha ao gerar resumo técnico a partir do PDF"]
    return resumo


def _generate_resumo_tecnico_from_pdf(sei: dict) -> dict:
    """Gera o resumo técnico a partir do PDF e mantém o contrato consumido pelo frontend."""
    try:
        sei_with_pdf = with_pdf_metadata(sei)
        pdf_filename = sei_with_pdf["documentoPdf"]["filename"]
        pdf_content = read_mock_pdf_bytes(pdf_filename)
        extraction = PdfExtractionService.extract_text(pdf_content)
        support_context = SupportDocumentService().build_context(max_trechos_suporte=12)
        payload = ResumoService().generate_resumo(
            process_text=extraction.text,
            support_context=support_context,
            model=DEFAULT_MODEL,
            include_minuta=True,
        )
    except (FileNotFoundError, PdfExtractionError, ValueError) as exc:
        return _empty_resumo_tecnico(str(exc))
    except Exception:
        return _empty_resumo_tecnico("Falha ao gerar resumo técnico a partir do Gemini.")

    if not payload:
        return _empty_resumo_tecnico("Gemini não retornou resumo técnico válido.")
    return ResumoService._normalize_payload(payload)


def _generate_minuta_from_resumo(sei: dict, resumo_tecnico: dict) -> str:
    insumo = resumo_tecnico.get("insumo_parecer", {})
    resumo_processo = resumo_tecnico.get("resumo_processo", {})
    fundamentos = insumo.get("fundamentos") or []
    pendencias = insumo.get("pendencias_documentais") or []

    fundamentos_texto = "\n".join(f"- {item}" for item in fundamentos) or "- Fundamentos técnicos não informados pelo resumo gerado."
    pendencias_texto = "\n".join(f"- {item}" for item in pendencias) or "- Sem pendências documentais específicas retornadas."

    return f"""EXCELENTÍSSIMO(A) SENHOR(A) DOUTOR(A) DE DIREITO

Processo SEI: {sei["numero"]}
Assunto: {sei["assunto"]}
Tipo de demanda: {resumo_processo.get("tipo_demanda", "não informado")}
Medicamento/insumo solicitado: {resumo_processo.get("medicamento_solicitado", "não informado")}

1. RESUMO TÉCNICO PRELIMINAR
{resumo_processo.get("objetivo_da_solicitacao", sei.get("resumo", "não informado"))}

2. INSUMO PARA PARECER
{insumo.get("conclusao_tecnica_sugerida", "Conclusão técnica não informada.")}

3. FUNDAMENTOS TÉCNICOS
{fundamentos_texto}

4. PENDÊNCIAS DOCUMENTAIS
{pendencias_texto}

Observação: minuta preliminar gerada automaticamente a partir do resumo técnico e pendente de revisão humana.
"""


def _generate_initial_minuta(sei: dict) -> str:
    return f"""EXCELENTÍSSIMO(A) SENHOR(A) DOUTOR(A) DE DIREITO

Processo SEI: {sei["numero"]}
Assunto: {sei["assunto"]}

1. SÍNTESE INICIAL
{sei.get("resumo", "Síntese não informada.")}

2. OBSERVAÇÃO
Resumo técnico preliminar em geração sob demanda a partir do PDF informado pelo mock do processo.

Observação: minuta preliminar pendente de integração com o resumo técnico gerado e de revisão humana.
"""


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
                "minuta": _generate_initial_minuta(sei),
            }
        ),
        200,
    )


@mock_data_bp.route("/seis/<sei_id>/resumo-tecnico", methods=["GET"])
def get_sei_resumo_tecnico(sei_id: str):
    sei = get_sei(sei_id)
    if not sei:
        return jsonify({"error": "SEI não encontrado."}), 404

    resumo_tecnico = _generate_resumo_tecnico_from_pdf(sei)
    return (
        jsonify(
            {
                "sei": with_pdf_metadata(sei),
                "resumoTecnico": resumo_tecnico,
                "minuta": _generate_minuta_from_resumo(sei, resumo_tecnico),
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
        sei_with_pdf = with_pdf_metadata(sei)
        pdf_content = read_mock_pdf_bytes(sei_with_pdf["documentoPdf"]["filename"])
    except FileNotFoundError:
        return jsonify({"error": "PDF mockado não encontrado."}), 404

    return (
        jsonify(
            {
                "filename": sei_with_pdf["documentoPdf"]["filename"],
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
