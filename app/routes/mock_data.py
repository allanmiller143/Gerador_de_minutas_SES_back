from datetime import datetime, time, timezone
from threading import Thread
import json 
from flask import Blueprint, current_app, jsonify, request
from app.models import PromptConfig, db, utcnow
from app.utils.resumo_service import ResumoService
from app.models import ( ResumoBatchRun, ResumoBatchSchedule, ResumoReexecutionRequest, ResumoTecnicoVersion, db, utcnow,)
from app.utils.pdf_extraction_service import PdfExtractionError, PdfExtractionService
from app.utils.resumo_service import DEFAULT_MODEL, ResumoService
from app.utils.support_document_service import SupportDocumentService
from app.utils.mock_data_service import ( JURISPRUDENCIAS, SEIS, get_jurisprudencias_for_sei, get_sei, read_mock_pdf_bytes, with_pdf_metadata,)

mock_data_bp = Blueprint("mock_data", __name__, url_prefix="/api")
ACTIVE_BATCH_STATUSES = {"running", "cancel_requested"}
DEFAULT_ACTIVE_RUN_STALE_AFTER_SECONDS = 10 * 60
# Prompt padrão hardcoded (fallback se o banco estiver vazio)
DEFAULT_PROMPT_TEXT = ResumoService().build_prompt("...", "...", True) 


def _parse_schedule_time(value: str | None) -> time | None:
    if not isinstance(value, str) or len(value) != 5 or value[2] != ":":
        return None
    hour, minute = value[:2], value[3:]
    if not hour.isdigit() or not minute.isdigit():
        return None
    hour_int = int(hour)
    minute_int = int(minute)
    if hour_int > 23 or minute_int > 59:
        return None
    return time(hour_int, minute_int)


def _get_sei_or_processo(sei_id: str) -> dict | None:
    from app.models import ProcessoSEI
    try:
        pid = int(sei_id)
        processo = db.session.get(ProcessoSEI, pid)
    except ValueError:
        processo = None

    if processo:
        sei_dict = processo.to_dict()
        if processo.arquivoPdf:
            import os
            sei_dict["documentoPdf"] = {
                "filename": os.path.basename(processo.arquivoPdf),
                "mime_type": "application/pdf",
                "url": f"/api/seis/{processo.id}/pdf"
            }
        return sei_dict

    return get_sei(sei_id)


def _active_run_stale_after_seconds() -> int:
    try:
        return max(1, int(request.args.get("stale_after_seconds", DEFAULT_ACTIVE_RUN_STALE_AFTER_SECONDS)))
    except RuntimeError:
        return DEFAULT_ACTIVE_RUN_STALE_AFTER_SECONDS
    except (TypeError, ValueError):
        return DEFAULT_ACTIVE_RUN_STALE_AFTER_SECONDS


def _parse_log_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _last_batch_log_at(run: ResumoBatchRun) -> datetime | None:
    for entry in reversed(run.logs):
        parsed = _parse_log_timestamp(entry.get("timestamp"))
        if parsed:
            return parsed
    return None


def _seconds_since_last_batch_progress(run: ResumoBatchRun) -> int | None:
    last_log_at = _last_batch_log_at(run)
    if not last_log_at:
        return None
    now = utcnow()
    return max(0, int((now - last_log_at).total_seconds()))


def _mark_stale_active_runs_as_interrupted(stale_after_seconds: int | None = None) -> None:
    stale_after_seconds = stale_after_seconds or _active_run_stale_after_seconds()
    active_runs = ResumoBatchRun.query.filter(ResumoBatchRun.status.in_(ACTIVE_BATCH_STATUSES)).all()
    changed = False
    for run in active_runs:
        if not run.logs:
            run.finish("interrupted", "Execução interrompida sem registro de conclusão.")
            run.append_log(
                "warning",
                "Execução marcada como interrompida porque estava em andamento, mas não tinha logs de progresso. Inicie uma nova execução se necessário.",
            )
            changed = True
            continue

        stale_for_seconds = _seconds_since_last_batch_progress(run)
        if stale_for_seconds is not None and stale_for_seconds > stale_after_seconds:
            run.finish("interrupted", "Execução interrompida por ausência de progresso recente.")
            run.append_log(
                "warning",
                f"Execução marcada como interrompida porque ficou sem progresso há mais de {stale_after_seconds} segundo(s). Inicie uma nova execução se necessário.",
            )
            changed = True
    if changed:
        db.session.commit()


def _find_active_resumo_batch_run() -> ResumoBatchRun | None:
    _mark_stale_active_runs_as_interrupted()
    return ResumoBatchRun.query.filter(ResumoBatchRun.status.in_(ACTIVE_BATCH_STATUSES)).order_by(ResumoBatchRun.started_at.desc()).first()


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
        pdf_filename = None
        pdf_content = None
        
        # Check if the process has a GCS file path
        arquivo_pdf = sei.get("arquivoPdf")
        if arquivo_pdf:
            from google.cloud import storage
            import os
            bucket_name = os.getenv("GCS_BUCKET_NAME")
            project_id = os.getenv("GCS_PROJECT_ID")
            if bucket_name:
                client = storage.Client(project=project_id)
                bucket = client.bucket(bucket_name)
                blob_path = arquivo_pdf
                if blob_path.startswith("gs://"):
                    blob_path = blob_path.split(f"{bucket_name}/")[-1]
                blob = bucket.blob(blob_path)
                if blob.exists():
                    pdf_content = blob.download_as_bytes()
        
        if pdf_content is None:
            sei_with_pdf = with_pdf_metadata(sei)
            pdf_filename = sei_with_pdf.get("documentoPdf", {}).get("filename")
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
    except Exception as exc:
        return _empty_resumo_tecnico(f"Falha ao gerar resumo técnico a partir do Gemini: {exc}")

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


def _create_resumo_batch_run(triggered_by: str, trigger_type: str = "manual") -> ResumoBatchRun:
    run = ResumoBatchRun(triggered_by=triggered_by or "sistema", trigger_type=trigger_type, status="running")
    run.append_log("info", f"Execução {trigger_type} iniciada por {run.triggered_by}.")
    db.session.add(run)
    db.session.commit()
    return run


def _sei_log_label(sei: dict) -> str:
    numero = sei.get("numero") or sei["id"]
    assunto = sei.get("assunto")
    return f"{numero} — {assunto}" if assunto else numero


def _append_batch_log(run: ResumoBatchRun, level: str, message: str) -> None:
    run.append_log(level, message)
    db.session.commit()


def _finish_canceled_run(run: ResumoBatchRun, generated_count: int, total_count: int) -> ResumoBatchRun:
    run.finish("canceled", "Execução suspensa por solicitação do usuário.")
    run.append_log(
        "warning",
        f"Execução suspensa antes do próximo processo: {generated_count} de {total_count} resumo(s) gerado(s).",
    )
    db.session.commit()
    return run


def _execute_resumo_batch_run(run_id: int) -> ResumoBatchRun | None:
    run = db.session.get(ResumoBatchRun, run_id)
    if not run:
        return None

    generated_ids: list[str] = []
    failed_count = 0
    pending_reexecution_ids = _pending_reexecution_sei_ids()
    from app.models import ProcessoSEI
    db_processos = ProcessoSEI.query.all()
    if db_processos:
        targets = []
        for p in db_processos:
            sei_dict = p.to_dict()
            if p.arquivoPdf:
                import os
                sei_dict["documentoPdf"] = {
                    "filename": os.path.basename(p.arquivoPdf),
                    "mime_type": "application/pdf",
                    "url": f"/api/seis/{p.id}/pdf"
                }
            if _needs_batch_generation(sei_dict, pending_reexecution_ids):
                targets.append(sei_dict)
    else:
        targets = [sei for sei in SEIS if _needs_batch_generation(sei, pending_reexecution_ids)]
        
    run.total_seis = len(targets)
    _append_batch_log(run, "info", f"{len(targets)} processo(s) SEI pendente(s) para processamento.")

    for index, sei in enumerate(targets, start=1):
        if run.status == "cancel_requested":
            return _finish_canceled_run(run, len(generated_ids), len(targets))

        label = _sei_log_label(sei)
        _append_batch_log(run, "info", f"Iniciando processo SEI {index}/{len(targets)}: {label}.")
        try:
            _persist_generated_resumo(sei, run.triggered_by, "batch", batch_run_id=run.id)
            generated_ids.append(sei["id"])
            run.generated_count = len(generated_ids)
            run.sei_ids = generated_ids
            ResumoReexecutionRequest.query.filter_by(sei_id=sei["id"], status="pending").update(
                {"status": "fulfilled", "fulfilled_at": utcnow()}
            )
            _append_batch_log(run, "success", f"Resumo gerado para o processo SEI {sei.get('numero', sei['id'])}.")
        except Exception as exc:
            failed_count += 1
            run.failed_count = failed_count
            _append_batch_log(run, "error", f"Falha ao gerar resumo do processo SEI {sei.get('numero', sei['id'])}: {exc}")

    if run.status == "cancel_requested":
        return _finish_canceled_run(run, len(generated_ids), len(targets))

    run.generated_count = len(generated_ids)
    run.failed_count = failed_count
    run.sei_ids = generated_ids
    final_status = "failed" if failed_count else "success"
    run.finish(final_status)
    if failed_count:
        run.append_log("error", f"Execução finalizada com falhas: {len(generated_ids)} resumo(s) gerado(s), {failed_count} falha(s).")
    else:
        run.append_log("success", f"Execução finalizada com sucesso: {len(generated_ids)} resumo(s) gerado(s), {failed_count} falha(s).")
    db.session.commit()
    return run


def _run_resumo_batch(triggered_by: str, trigger_type: str = "manual") -> ResumoBatchRun:
    run = _create_resumo_batch_run(triggered_by, trigger_type)
    completed_run = _execute_resumo_batch_run(run.id)
    return completed_run or run


def _start_resumo_batch_thread(app, run_id: int) -> None:
    def target():
        with app.app_context():
            _execute_resumo_batch_run(run_id)

    Thread(target=target, daemon=True).start()


def execute_due_resumo_batch(now: datetime | None = None):
    """Executa o batch online quando a agenda recorrente estiver vencida.

    Chamado no ciclo da aplicação; em produção também pode ser disparado por cron/worker.
    """
    schedule = ResumoBatchSchedule.query.first()
    if not schedule or not schedule.enabled:
        return None
    if _find_active_resumo_batch_run():
        return None
    now = now or datetime.now()
    scheduled_time = _parse_schedule_time(schedule.time)
    if not scheduled_time:
        return None
    today = now.date().isoformat()
    if schedule.last_run_date == today or now.time().replace(second=0, microsecond=0) < scheduled_time:
        return None
    run = _run_resumo_batch("agenda automática", "scheduled")
    schedule.last_run_date = today
    db.session.commit()
    return run


@mock_data_bp.route("/seis", methods=["GET"])
def list_seis():
    from app.models import ProcessoSEI
    processos = ProcessoSEI.query.order_by(ProcessoSEI.dataRecebimento.desc()).all()
    if not processos:
        return jsonify({"seis": [with_pdf_metadata(sei) for sei in SEIS]}), 200

    seis_list = []
    for p in processos:
        d = p.to_dict()
        if p.arquivoPdf:
            import os
            d["documentoPdf"] = {
                "filename": os.path.basename(p.arquivoPdf),
                "mime_type": "application/pdf",
                "url": f"/api/seis/{p.id}/pdf"
            }
        seis_list.append(d)
    return jsonify({"seis": seis_list}), 200


@mock_data_bp.route("/seis/<sei_id>", methods=["GET"])
def detail_sei(sei_id: str):
    from app.models import ProcessoSEI
    try:
        pid = int(sei_id)
        processo = db.session.get(ProcessoSEI, pid)
    except ValueError:
        processo = None
        
    if not processo:
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
        
    sei_dict = processo.to_dict()
    if processo.arquivoPdf:
        import os
        sei_dict["documentoPdf"] = {
            "filename": os.path.basename(processo.arquivoPdf),
            "mime_type": "application/pdf",
            "url": f"/api/seis/{processo.id}/pdf"
        }
    
    from app.utils.mock_data_service import JURISPRUDENCIAS
    juris_list = [j for j in JURISPRUDENCIAS if j["id"] in (processo.jurisprudenciasSugeridas or [])]
    
    return jsonify({
        "sei": sei_dict,
        "jurisprudencias": juris_list,
        "minuta": processo.minuta or processo.iaSugestao or _generate_initial_minuta(sei_dict)
    }), 200


@mock_data_bp.route("/seis/<sei_id>/resumo-tecnico", methods=["GET"])
def get_sei_resumo_tecnico(sei_id: str):
    from app.models import ProcessoSEI

    try:
        pid = int(sei_id)
        processo = db.session.get(ProcessoSEI, pid)
    except ValueError:
        processo = None

    if not processo:
        sei = get_sei(sei_id)
        if not sei:
            return jsonify({"error": "SEI não encontrado."}), 404

        active_version = ResumoTecnicoVersion.query.filter_by(
            sei_id=sei_id,
            is_active=True
        ).first()

        if active_version:
            return jsonify({
                "sei": with_pdf_metadata(sei),
                **active_version.to_dict()
            }), 200

        resumo_tecnico = _generate_resumo_tecnico_from_pdf(sei)

        return jsonify({
            "sei": with_pdf_metadata(sei),
            "resumoTecnico": resumo_tecnico,
            "minuta": _generate_minuta_from_resumo(sei, resumo_tecnico),
        }), 200

    sei_dict = processo.to_dict()

    if processo.arquivoPdf:
        import os

        sei_dict["documentoPdf"] = {
            "filename": os.path.basename(processo.arquivoPdf),
            "mime_type": "application/pdf",
            "url": f"/api/seis/{processo.id}/pdf",
        }

    active_version = ResumoTecnicoVersion.query.filter_by(
        sei_id=str(processo.id),
        is_active=True
    ).first()

    if active_version:
        return jsonify({
            "sei": sei_dict,
            **active_version.to_dict()
        }), 200

    resumo_tecnico = {
        "resumo_processo": {
            "objetivo_da_solicitacao": processo.resumo or "Não informado",
            "medicamento_solicitado": processo.assunto,
        },
        "evidencias_clinicas_do_processo": [],
        "confronto_documentacao_suporte": {
            "cid_validado": True,
            "observacoes": ["Análise realizada sob demanda."],
        },
        "insumo_parecer": {
            "conclusao_tecnica_sugerida": processo.iaSugestao or "Minuta pendente de geração.",
            "necessita_revisao_humana": True,
            "level_confianca": f"{int(processo.iaConfidence * 100)}%" if processo.iaConfidence else "0%",
        },
        "fontes_consultadas": [],
    }

    return jsonify({
        "sei": sei_dict,
        "resumoTecnico": resumo_tecnico,
        "minuta": processo.minuta or processo.iaSugestao or _generate_minuta_from_resumo(sei_dict, resumo_tecnico),
    }), 200


@mock_data_bp.route("/seis/<sei_id>/resumos", methods=["GET"])
def list_sei_resumos(sei_id: str):
    if not _get_sei_or_processo(sei_id):
        return jsonify({"error": "SEI não encontrado."}), 404
    versions = ResumoTecnicoVersion.query.filter_by(sei_id=sei_id).order_by(ResumoTecnicoVersion.version.desc()).all()
    return jsonify({"resumos": [item.to_dict() for item in versions]}), 200


@mock_data_bp.route("/seis/<sei_id>/resumos/generate", methods=["POST"])
def generate_sei_resumo(sei_id: str):
    sei = _get_sei_or_processo(sei_id)
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
    if not _get_sei_or_processo(sei_id):
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
            schedule_time = _parse_schedule_time(data.get("time"))
            if not schedule_time:
                return jsonify({"error": "Horário da agenda deve estar no formato HH:MM."}), 400
            schedule.time = schedule_time.strftime("%H:%M")
        schedule.updated_by = _actor_from_request()
        schedule.updated_at = utcnow()
        db.session.commit()
    return jsonify(schedule.to_dict()), 200


@mock_data_bp.route("/resumo-batch/run", methods=["POST"])
def run_resumo_batch():
    active_run = _find_active_resumo_batch_run()
    if active_run:
        return (
            jsonify(
                {
                    "error": "Já existe uma execução de resumos em andamento. Conclua ou suspenda a execução atual antes de iniciar outra.",
                    "active_run": active_run.to_dict(),
                }
            ),
            409,
        )
    run = _create_resumo_batch_run(_actor_from_request(), "manual")
    _start_resumo_batch_thread(current_app._get_current_object(), run.id)
    return jsonify(run.to_dict()), 202


@mock_data_bp.route("/resumo-batch/runs/<int:run_id>/cancel", methods=["POST"])
def cancel_resumo_batch_run(run_id: int):
    run = db.session.get(ResumoBatchRun, run_id)
    if not run:
        return jsonify({"error": "Execução não encontrada."}), 404
    if run.status not in {"running", "cancel_requested"}:
        return jsonify({"error": "Execução não está em andamento.", "run": run.to_dict()}), 409
    if run.status == "running":
        run.status = "cancel_requested"
        run.append_log(
            "warning",
            f"Cancelamento solicitado por {_actor_from_request()}. A execução será suspensa ao concluir o processo atual.",
        )
        db.session.commit()
    return jsonify(run.to_dict()), 200


def _mark_orphan_running_runs_as_interrupted(runs: list[ResumoBatchRun]) -> None:
    _mark_stale_active_runs_as_interrupted()


@mock_data_bp.route("/resumo-batch/runs", methods=["GET"])
def list_resumo_batch_runs():
    _mark_orphan_running_runs_as_interrupted([])
    runs = ResumoBatchRun.query.order_by(ResumoBatchRun.started_at.desc()).limit(50).all()
    return jsonify({"runs": [run.to_dict() for run in runs]}), 200


@mock_data_bp.route("/seis/<sei_id>/pdf", methods=["GET"])
def get_sei_pdf(sei_id: str):
    from app.models import ProcessoSEI
    try:
        pid = int(sei_id)
        processo = db.session.get(ProcessoSEI, pid)
    except ValueError:
        processo = None
        
    if processo and processo.arquivoPdf:
        import os
        from google.cloud import storage
        project_id = os.getenv("GCS_PROJECT_ID")
        bucket_name = os.getenv("GCS_BUCKET_NAME")
        if bucket_name:
            try:
                client = storage.Client(project=project_id)
                bucket = client.bucket(bucket_name)
                blob_path = processo.arquivoPdf
                if blob_path.startswith("gs://"):
                    blob_path = blob_path.split(f"{bucket_name}/")[-1]
                blob = bucket.blob(blob_path)
                if blob.exists():
                    pdf_content = blob.download_as_bytes()
                    return (
                        jsonify(
                            {
                                "filename": os.path.basename(processo.arquivoPdf),
                                "mime_type": "application/pdf",
                                "size": len(pdf_content),
                                "pdf_bytes": list(pdf_content),
                            }
                        ),
                        200,
                    )
            except Exception as e:
                # Log error and continue to fallback
                print(f"Error fetching PDF from GCS: {e}")
                
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


@mock_data_bp.route("/prompts/<key>", methods=["GET"])
def get_prompt(key: str):
    config = PromptConfig.get_or_create_default(ResumoService.get_default_editable_prompt(), key=key)
    return jsonify({
        "key": key,
        "editable_prompt": config.system_prompt,
        "fixed_schema": ResumoService.get_fixed_schema(),
        "updated_at": config.updated_at.isoformat() if config.updated_at else None,
        "updated_by": config.updated_by,
    }), 200


@mock_data_bp.route("/prompts/<key>", methods=["PUT"])
def update_prompt(key: str):
    data = request.get_json(silent=True) or {}
    new_prompt = data.get("editable_prompt")  
    
    if not new_prompt or not isinstance(new_prompt, str) or not new_prompt.strip():
        return jsonify({"error": "O campo 'editable_prompt' é obrigatório."}), 400

    config = PromptConfig.get_or_create_default(ResumoService.get_default_editable_prompt(), key=key)
    config.system_prompt = new_prompt.strip()
    config.updated_at = utcnow()
    config.updated_by = data.get("updated_by") or "sistema"
    db.session.commit()
    
    return jsonify({
        "key": key,
        "editable_prompt": config.system_prompt,
        "fixed_schema": ResumoService.get_fixed_schema(),
        "updated_at": config.updated_at.isoformat(),
        "updated_by": config.updated_by,
    }), 200