from datetime import datetime

from flask import Blueprint, jsonify, request

from app.models import (
    ResumoBatchRun,
    ResumoBatchSchedule,
    ResumoReexecutionRequest,
    ResumoTecnicoVersion,
    db,
    utcnow,
)
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


def _actor_from_request(default="sistema") -> str:
    data = request.get_json(silent=True) or {}
    return data.get("triggered_by") or data.get("updated_by") or data.get("requested_by") or default


def _persist_generated_resumo(sei: dict, generated_by: str, source: str, batch_run_id: int | None = None) -> ResumoTecnicoVersion:
    resumo_tecnico = _generate_resumo_tecnico_from_pdf(sei)
    minuta = _generate_minuta_from_resumo(sei, resumo_tecnico)
    version = ResumoTecnicoVersion.create_new(
        sei_id=sei["id"],
        payload=resumo_tecnico,
        minuta=minuta,
        generated_by=generated_by,
        source=source,
        batch_run_id=batch_run_id,
    )
    return version


def _pending_reexecution_sei_ids() -> set[str]:
    return {
        item.sei_id
        for item in ResumoReexecutionRequest.query.filter_by(status="pending").all()
    }


def _needs_batch_generation(sei: dict, pending_reexecution_ids: set[str]) -> bool:
    if sei["id"] in pending_reexecution_ids:
        return True
    return ResumoTecnicoVersion.query.filter_by(sei_id=sei["id"]).first() is None


def _run_resumo_batch(triggered_by: str, trigger_type: str = "manual") -> ResumoBatchRun:
    run = ResumoBatchRun(triggered_by=triggered_by or "sistema", trigger_type=trigger_type)
    db.session.add(run)
    db.session.flush()

    generated_ids: list[str] = []
    failed_count = 0
    pending_reexecution_ids = _pending_reexecution_sei_ids()
    targets = [sei for sei in SEIS if _needs_batch_generation(sei, pending_reexecution_ids)]
    run.total_seis = len(targets)

    for sei in targets:
        try:
            _persist_generated_resumo(sei, triggered_by, "batch", batch_run_id=run.id)
            generated_ids.append(sei["id"])
            ResumoReexecutionRequest.query.filter_by(sei_id=sei["id"], status="pending").update(
                {"status": "fulfilled", "fulfilled_at": utcnow()}
            )
        except Exception:
            failed_count += 1

    run.generated_count = len(generated_ids)
    run.failed_count = failed_count
    run.sei_ids = generated_ids
    run.finish("failed" if failed_count else "success")
    db.session.commit()
    return run


def execute_due_resumo_batch(now: datetime | None = None):
    """Executa o batch online quando a agenda recorrente estiver vencida.

    Chamado no ciclo da aplicação; em produção também pode ser disparado por cron/worker.
    """
    schedule = ResumoBatchSchedule.query.first()
    if not schedule or not schedule.enabled:
        return None
    now = now or datetime.now()
    today = now.date().isoformat()
    if schedule.last_run_date == today or now.strftime("%H:%M") < schedule.time:
        return None
    run = _run_resumo_batch("agenda automática", "scheduled")
    schedule.last_run_date = today
    db.session.commit()
    return run


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

    active_version = ResumoTecnicoVersion.query.filter_by(sei_id=sei_id, is_active=True).first()
    if active_version:
        return jsonify({"sei": with_pdf_metadata(sei), **active_version.to_dict()}), 200

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


@mock_data_bp.route("/seis/<sei_id>/resumos", methods=["GET"])
def list_sei_resumos(sei_id: str):
    if not get_sei(sei_id):
        return jsonify({"error": "SEI não encontrado."}), 404
    versions = ResumoTecnicoVersion.query.filter_by(sei_id=sei_id).order_by(ResumoTecnicoVersion.version.desc()).all()
    return jsonify({"resumos": [item.to_dict() for item in versions]}), 200


@mock_data_bp.route("/seis/<sei_id>/resumos/generate", methods=["POST"])
def generate_sei_resumo(sei_id: str):
    sei = get_sei(sei_id)
    if not sei:
        return jsonify({"error": "SEI não encontrado."}), 404
    version = _persist_generated_resumo(sei, _actor_from_request(), "manual")
    ResumoReexecutionRequest.query.filter_by(sei_id=sei_id, status="pending").update(
        {"status": "fulfilled", "fulfilled_at": utcnow()}
    )
    db.session.commit()
    return jsonify(version.to_dict()), 201


@mock_data_bp.route("/seis/<sei_id>/resumos/requeue", methods=["POST"])
def requeue_sei_resumo(sei_id: str):
    if not get_sei(sei_id):
        return jsonify({"error": "SEI não encontrado."}), 404
    request_item = ResumoReexecutionRequest(sei_id=sei_id, requested_by=_actor_from_request())
    db.session.add(request_item)
    db.session.commit()
    return jsonify(request_item.to_dict()), 201


@mock_data_bp.route("/seis/<sei_id>/resumos/<int:resumo_id>/restore", methods=["POST"])
def restore_sei_resumo(sei_id: str, resumo_id: int):
    version = ResumoTecnicoVersion.query.filter_by(id=resumo_id, sei_id=sei_id).first()
    if not version:
        return jsonify({"error": "Versão de resumo não encontrada."}), 404
    ResumoTecnicoVersion.query.filter_by(sei_id=sei_id, is_active=True).update({"is_active": False})
    version.is_active = True
    db.session.commit()
    return jsonify(version.to_dict()), 200


@mock_data_bp.route("/resumo-batch/config", methods=["GET", "PUT"])
def resumo_batch_config():
    schedule = ResumoBatchSchedule.singleton()
    if request.method == "PUT":
        data = request.get_json(silent=True) or {}
        if "enabled" in data:
            schedule.enabled = bool(data["enabled"])
        if data.get("time"):
            schedule.time = data["time"]
        schedule.updated_by = _actor_from_request()
        schedule.updated_at = utcnow()
        db.session.commit()
    return jsonify(schedule.to_dict()), 200


@mock_data_bp.route("/resumo-batch/run", methods=["POST"])
def run_resumo_batch():
    run = _run_resumo_batch(_actor_from_request(), "manual")
    return jsonify(run.to_dict()), 201


@mock_data_bp.route("/resumo-batch/runs", methods=["GET"])
def list_resumo_batch_runs():
    runs = ResumoBatchRun.query.order_by(ResumoBatchRun.started_at.desc()).limit(50).all()
    return jsonify({"runs": [run.to_dict() for run in runs]}), 200


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
