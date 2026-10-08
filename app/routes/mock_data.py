import os
import time as time_lib
import concurrent.futures
import logging
from datetime import datetime, time, timezone
from threading import Thread
import json 
from flask import Blueprint, current_app, jsonify, request
from app.models import (
    ProcessoSEI,
    PromptConfig,
    ResumoBatchRun,
    ResumoBatchSchedule,
    ResumoReexecutionRequest,
    ResumoTecnicoVersion,
    db,
    utcnow,
)
from app.utils.document_ai_ocr_service import DocumentAiOcrService
from app.utils.pdf_extraction_service import PdfExtractionError, PdfExtractionService
from app.utils.resumo_service import DEFAULT_MODEL, ResumoService
from app.utils.support_document_service import SupportDocumentService
from app.utils.mock_data_service import ( JURISPRUDENCIAS, SEIS, get_jurisprudencias_for_sei, get_sei, read_mock_pdf_bytes, with_pdf_metadata,)
try:
    from app.utils.adk_resumo_service import AdkResumoService
except ImportError:
    AdkResumoService = None

mock_data_bp = Blueprint("mock_data", __name__, url_prefix="/api")
ACTIVE_BATCH_STATUSES = {"running", "cancel_requested"}
DEFAULT_ACTIVE_RUN_STALE_AFTER_SECONDS = int(os.getenv("BATCH_STALE_TIMEOUT_SECONDS", 30 * 60))
DEFAULT_PROCESS_TIMEOUT_SECONDS = int(os.getenv("BATCH_PROCESS_TIMEOUT_SECONDS", 8 * 60))
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


def _finish_interrupted_runs_on_startup() -> None:
    """Marca execuções batch ativas que ficaram órfãs após queda/reinicialização do servidor."""
    active_runs = ResumoBatchRun.query.filter(ResumoBatchRun.status.in_(ACTIVE_BATCH_STATUSES)).all()
    for run in active_runs:
        run.finish("interrupted", "Execução interrompida pela reinicialização do sistema.")
        run.append_log(
            "warning",
            "Execução anterior marcada como interrompida devido à reinicialização do sistema. Inicie uma nova execução se necessário.",
        )
    if active_runs:
        db.session.commit()
        print(f"Batch startup: {len(active_runs)} execução(ões) batch ativa(s) marcada(s) como interrompida(s).")


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


def _generate_resumo_tecnico_from_pdf(
    sei: dict,
    ocr_text_out: dict | None = None,
    process_text: str | None = None,
) -> dict:
    """Gera o resumo técnico a partir do PDF e mantém o contrato consumido pelo frontend."""
    try:
        text_chars = len(process_text or "")
        if process_text is None:
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

            extraction = DocumentAiOcrService.extract_text_with_fallback(pdf_content)
            process_text = extraction.text
            text_chars = extraction.text_chars
        if ocr_text_out is not None:
            ocr_text_out["text"] = process_text
            ocr_text_out["text_chars"] = text_chars
        # Tenta gerar o resumo via Google ADK
        payload = None
        use_adk = not (current_app and current_app.config.get("TESTING") and not getattr(AdkResumoService, "_force_test_adk", False))
        if use_adk and AdkResumoService:
            try:
                logging.info("[PIPELINE_MOTOR: GOOGLE_ADK] Gerando resumo e minuta via AdkResumoService para processo %s", sei.get("numero"))
                payload = AdkResumoService().generate_resumo(
                    process_text=process_text,
                    include_minuta=True,
                    numero_sei=sei.get("numero"),
                )
                if payload:
                    logging.info("[PIPELINE_MOTOR: GOOGLE_ADK] Resumo e minuta gerados com sucesso via Google ADK para processo %s", sei.get("numero"))
            except Exception as adk_exc:
                logging.warning("[PIPELINE_FALLBACK: ADK -> LEGADO] Falha no AdkResumoService para processo %s: %s. Acionando pipeline legado...", sei.get("numero"), adk_exc)
                payload = None

        if not payload:
            logging.info("[PIPELINE_MOTOR: LEGADO] Gerando resumo via ResumoService (pipeline legado) para processo %s", sei.get("numero"))
            support_context = SupportDocumentService().build_context(max_trechos_suporte=12)
            payload = ResumoService().generate_resumo(
                process_text=process_text,
                support_context=support_context,
                model=DEFAULT_MODEL,
                include_minuta=True,
            )
            if payload:
                logging.info("[PIPELINE_MOTOR: LEGADO] Resumo gerado com sucesso via ResumoService (pipeline legado) para processo %s", sei.get("numero"))
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


def _persist_generated_resumo(
    sei: dict,
    generated_by: str,
    source: str,
    batch_run_id: int | None = None,
    ocr_text_out: dict | None = None,
    process_text: str | None = None,
) -> ResumoTecnicoVersion:
    # Gera o resumo técnico
    import inspect
    sig = inspect.signature(_generate_resumo_tecnico_from_pdf)
    gen_kwargs = {}
    if "ocr_text_out" in sig.parameters and ocr_text_out is not None:
        gen_kwargs["ocr_text_out"] = ocr_text_out
    if "process_text" in sig.parameters and process_text is not None:
        gen_kwargs["process_text"] = process_text
    resumo_tecnico = _generate_resumo_tecnico_from_pdf(sei, **gen_kwargs)
    #Busca a sugestão da IA no dicionário.
    minuta = sei.get("iaSugestao")
    #Se a IA não tiver gerado a minuta utiliza a genérica.
    if not minuta:
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
    try:
        db.session.expire(run, ["logs_json", "status"])
    except Exception:
        pass
    run.append_log(level, message)
    db.session.commit()


def _finish_canceled_run(run: ResumoBatchRun, generated_count: int, total_count: int) -> ResumoBatchRun:
    try:
        db.session.expire(run, ["logs_json", "status"])
    except Exception:
        pass
    run.finish("canceled", "Execução suspensa por solicitação do usuário.")
    run.append_log(
        "warning",
        f"Execução suspensa antes do próximo processo: {generated_count} de {total_count} resumo(s) gerado(s).",
    )
    db.session.commit()
    return run


def _run_single_batch_target(app, run_id: int, processo_id: int | None, sei: dict):
    """Executa a etapa pesada de obtenção de PDF e geração de resumo para um único processo."""
    with app.app_context():
        try:
            run = db.session.get(ResumoBatchRun, run_id)
            processo_obj = db.session.get(ProcessoSEI, processo_id) if processo_id else None

            # 1. Garante que o PDF está no GCS antes de gerar o resumo
            if processo_obj and not _ensure_pdf_in_gcs(processo_obj, run):
                return False, "Falha ao obter PDF do processo no SEI."

            # Atualiza o dicionário sei com o caminho do PDF no GCS se processo_obj existir
            if processo_obj:
                sei = processo_obj.to_dict()
                if processo_obj.arquivoPdf:
                    import os
                    sei["arquivoPdf"] = processo_obj.arquivoPdf
                    sei["documentoPdf"] = {
                        "filename": os.path.basename(processo_obj.arquivoPdf),
                        "mime_type": "application/pdf",
                        "url": f"/api/seis/{processo_obj.id}/pdf",
                    }

            # 2. Gera e persiste o resumo técnico
            version = _persist_generated_resumo(sei, run.triggered_by, "batch", batch_run_id=run.id)

            payload = version.payload or {}
            obs = payload.get("confronto_documentacao_suporte", {}).get("observacoes", [])
            has_error = any("falha" in str(o).lower() or "error" in str(o).lower() for o in obs)

            if has_error:
                err_msg = "; ".join(str(o) for o in obs)
                if processo_obj:
                    processo_obj.status_processamento = "Falhou"
                    processo_obj.status = "Falha na análise"
                    processo_obj.erro_processamento = err_msg
                    db.session.commit()
                return False, err_msg

            # 3. Atualiza o processo no banco com a minuta e dados gerados pela IA
            if processo_obj:
                import json
                minuta = version.minuta or sei.get("iaSugestao") or (payload.get("minuta") or payload.get("minuta_parecer"))
                processo_obj.iaSugestao = minuta
                processo_obj.minuta = minuta
                processo_obj.resumo = json.dumps(payload, ensure_ascii=False)
                processo_obj.status = "Pré-análise"
                processo_obj.status_processamento = "Concluído"
                processo_obj.erro_processamento = None

                insumo = payload.get("insumo_parecer", {})
                raw_conf = str(insumo.get("nivel_confianca", "0.75")).lower()
                conf_map = {"alto": 0.90, "alta": 0.90, "médio": 0.75, "medio": 0.75, "média": 0.75, "media": 0.75, "baixo": 0.50, "baixa": 0.50}
                if raw_conf in conf_map:
                    processo_obj.iaConfidence = conf_map[raw_conf]
                else:
                    try:
                        processo_obj.iaConfidence = float(raw_conf)
                    except ValueError:
                        processo_obj.iaConfidence = 0.75
                processo_obj.jurisprudenciasSugeridas = payload.get("fontes_consultadas", [])

                if payload.get("complexidade"):
                    processo_obj.complexidade = payload["complexidade"]
                if payload.get("complexidade_justificativa"):
                    processo_obj.complexidade_justificativa = payload["complexidade_justificativa"]
                if payload.get("alerta_ocr"):
                    processo_obj.alerta_ocr = payload["alerta_ocr"]

                db.session.commit()

            return True, None
        except Exception as exc:
            if processo_obj:
                processo_obj.status_processamento = "Falhou"
                processo_obj.status = "Falha na análise"
                processo_obj.erro_processamento = str(exc)
                try:
                    db.session.commit()
                except Exception:
                    db.session.rollback()
            return False, str(exc)


def _execute_resumo_batch_run(run_id: int) -> ResumoBatchRun | None:
    run = db.session.get(ResumoBatchRun, run_id)
    if not run:
        return None

    generated_ids: list[str] = []
    failed_count = 0
    pending_reexecution_ids = _pending_reexecution_sei_ids()
    from app.models import ProcessoSEI
    # Importa processos novos da caixa de Recebidos via RPA antes de processar
    _import_new_processes(run)

    db_processos = ProcessoSEI.query.all()
    if db_processos:
        targets = []
        for p in db_processos:
            sei_dict = p.to_dict()
            if p.arquivoPdf:
                sei_dict["documentoPdf"] = {
                    "filename": os.path.basename(p.arquivoPdf),
                    "mime_type": "application/pdf",
                    "url": f"/api/seis/{p.id}/pdf"
                }
            if _needs_batch_generation(sei_dict, pending_reexecution_ids):
                targets.append((p, sei_dict))
    else:
        targets = [(None, sei) for sei in SEIS if _needs_batch_generation(sei, pending_reexecution_ids)]

    run.total_seis = len(targets)
    _append_batch_log(run, "info", f"{len(targets)} processo(s) SEI pendente(s) para processamento.")

    app = current_app._get_current_object()
    process_timeout_seconds = int(os.getenv("BATCH_PROCESS_TIMEOUT_SECONDS", DEFAULT_PROCESS_TIMEOUT_SECONDS))

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)

    try:
        for index, (processo_obj, sei) in enumerate(targets, start=1):
            db.session.refresh(run)
            if run.status == "cancel_requested":
                return _finish_canceled_run(run, len(generated_ids), len(targets))

            label = _sei_log_label(sei)
            _append_batch_log(run, "info", f"Iniciando processo SEI {index}/{len(targets)}: {label}.")

            pid = processo_obj.id if processo_obj else None
            if processo_obj:
                processo_obj.status_processamento = "Processando"
                db.session.commit()

            future = executor.submit(_run_single_batch_target, app, run.id, pid, sei)
            start_ts = time_lib.time()
            last_heartbeat_ts = start_ts
            is_success = False
            error_detail = None
            is_timeout = False

            while True:
                time_spent = time_lib.time() - start_ts
                remaining_time = max(0.1, process_timeout_seconds - time_spent)

                try:
                    slice_timeout = min(5.0, remaining_time)
                    success_result, err = future.result(timeout=slice_timeout)
                    is_success = success_result
                    error_detail = err
                    break
                except concurrent.futures.TimeoutError:
                    current_elapsed = int(time_lib.time() - start_ts)
                    if current_elapsed >= process_timeout_seconds:
                        is_timeout = True
                        break
                    # Heartbeat a cada 60s
                    if time_lib.time() - last_heartbeat_ts >= 60:
                        last_heartbeat_ts = time_lib.time()
                        _append_batch_log(
                            run,
                            "info",
                            f"Processando {label} (etapa de IA/extração em andamento há {current_elapsed}s)...",
                        )

            if is_timeout:
                failed_count += 1
                run.failed_count = failed_count
                timeout_minutes = max(1, process_timeout_seconds // 60)
                _append_batch_log(
                    run,
                    "error",
                    f"Tempo limite de {timeout_minutes} minuto(s) excedido para o processo {label}. Pulando para o próximo.",
                )
                if pid:
                    p = db.session.get(ProcessoSEI, pid)
                    if p:
                        p.status_processamento = "Falhou"
                        p.status = "Falha na análise"
                        p.erro_processamento = (
                            f"Tempo limite individual de {timeout_minutes} minuto(s) excedido durante extração/análise."
                        )
                        db.session.commit()
            elif is_success:
                generated_ids.append(sei["id"])
                run.generated_count = len(generated_ids)
                run.sei_ids = generated_ids
                ResumoReexecutionRequest.query.filter_by(sei_id=sei["id"], status="pending").update(
                    {"status": "fulfilled", "fulfilled_at": utcnow()}
                )
                _append_batch_log(run, "success", f"Resumo gerado para o processo SEI {sei.get('numero', sei['id'])}.")
            else:
                failed_count += 1
                run.failed_count = failed_count
                _append_batch_log(run, "error", f"Falha ao processar {sei.get('numero', sei['id'])}: {error_detail}")
    finally:
        executor.shutdown(wait=False)

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

    # Resolve cada fonte/citação contra a Base de Conhecimento e documentos do processo
    try:
        from app.models import KnowledgeDocument
        all_docs = KnowledgeDocument.query.filter_by(is_active=True).all()
        fontes_detalhadas = []
        for ref in (processo.jurisprudenciasSugeridas or []):
            if not isinstance(ref, str) or not ref.strip():
                continue
            ref_lower = ref.lower()
            is_processo = any(k in ref_lower for k in ["processo", "laudo", "prescri", "sei", "receita"]) or (processo.numero in ref)
            matched_doc = None
            if not is_processo:
                if "rename" in ref_lower:
                    matched_doc = next((d for d in all_docs if "rename" in (d.filename or "").lower()), None)
                if not matched_doc:
                    for d in all_docs:
                        name = (d.titulo or "").strip().lower()
                        fn = (d.filename or "").replace(".pdf", "").strip().lower()
                        if (len(name) > 3 and name in ref_lower) or (len(fn) > 3 and fn in ref_lower):
                            matched_doc = d
                            break

            tipo = "processo" if is_processo else ("arquivo_base" if matched_doc else "norma_citada")
            tem_arquivo = (is_processo and bool(processo.arquivoPdf)) or (matched_doc is not None)
            fontes_detalhadas.append({
                "texto": ref,
                "tipo": tipo,
                "tem_arquivo": tem_arquivo,
                "arquivo_nome": matched_doc.filename if matched_doc else (os.path.basename(processo.arquivoPdf) if is_processo and processo.arquivoPdf else None),
                "file_path": matched_doc.file_path if matched_doc else None,
            })
        sei_dict["fontes_consultadas_detalhadas"] = fontes_detalhadas
    except Exception as exc:
        print(f"Aviso ao detalhar fontes consultadas para SEI {processo.id}: {exc}")
        sei_dict["fontes_consultadas_detalhadas"] = []
    
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

    #O processo não existe no banco (mockado).
    if not processo:
        sei = get_sei(sei_id)
        if not sei:
            return jsonify({"error": "SEI não encontrado."}), 404

        active_version = ResumoTecnicoVersion.query.filter_by(
            sei_id=sei_id,
            is_active=True
        ).first()

        if active_version:
            #Puxa a versão mockada.
            version_data = active_version.to_dict()
            
            #Se no arquivo mock já houver uma sugestão de IA, ela tem prioridade
            if sei.get("iaSugestao"):
                version_data["minuta"] = sei["iaSugestao"]

            return jsonify({
                "sei": with_pdf_metadata(sei),
                **version_data
            }), 200

        resumo_tecnico = _generate_resumo_tecnico_from_pdf(sei)

        return jsonify({
            "sei": with_pdf_metadata(sei),
            "resumoTecnico": resumo_tecnico,
            "minuta": _generate_minuta_from_resumo(sei, resumo_tecnico),
        }), 200


    #O processo é real e vem do banco de dados
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
        #Puxa a versão ativa do banco
        version_data = active_version.to_dict()
        
        #Sobrescreve se tiver uma minuta antiga com problema.
        minuta_real = processo.minuta or processo.iaSugestao or version_data.get("minuta")
        if minuta_real:
            version_data["minuta"] = minuta_real

        return jsonify({
            "sei": sei_dict,
            **version_data
        }), 200

    resumo_tecnico = {
        "resumo_processo": {
            "objetivo_da_solicitacao": processo.resumo or "Não informado",
            "medicamento_solicitado": processo.assunto,
            "remetente": processo.remetente or "Não informado",
            "prazo_legal_dias": processo.prazo_legal_dias,
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


def _import_new_processes(run: ResumoBatchRun) -> None:
    """Busca processos novos na caixa do SEI e importa para o banco."""
    if current_app and current_app.config.get("TESTING"):
        return

    from app.models import ProcessoSEI
    from app.utils import rpasei

    _append_batch_log(run, "info", "Buscando processos novos na caixa de Recebidos do SEI...")
    try:
        numeros = rpasei.buscar_todos_processos_recebidos()
    except Exception as e:
        _append_batch_log(run, "warning", f"Não foi possível acessar o SEI: {e}. Seguindo com os processos já existentes no banco.")
        return

    novos = 0
    for numero in numeros:
        if ProcessoSEI.query.filter_by(numero=numero).first():
            continue
        processo = ProcessoSEI(
            numero=numero,
            assunto="Pendente de análise",
            status="Pré-análise",
            prioridade="Média",
            status_processamento="Pendente",
        )
        db.session.add(processo)
        novos += 1

    if novos:
        db.session.commit()
        _append_batch_log(run, "info", f"{novos} processo(s) novo(s) importado(s) do SEI.")
    else:
        _append_batch_log(run, "info", "Nenhum processo novo encontrado na caixa de Recebidos.")


def download_and_upload_sei_pdf(processo) -> tuple[bool, str | None]:
    """
    Busca documentos no SEI usando RPA, concatena em um único PDF e sobe para o GCS.
    Retorna (True, caminho_gcs) em caso de sucesso, ou (False, mensagem_erro) em caso de falha.
    """
    if processo.arquivoPdf:
        return True, processo.arquivoPdf

    from app.models import db
    from app.utils import rpasei
    from app.utils.gcs_utils import upload_file_to_gcs
    import fitz
    import base64
    import io

    try:
        resultado = rpasei.run(processo.numero)
    except Exception as e:
        return False, f"Falha ao acessar o SEI: {str(e)}"

    if resultado.get("status") == "erro" or not resultado.get("documentos"):
        msg = resultado.get("mensagem") or resultado.get("erro") or "Nenhum documento retornado pelo SEI."
        return False, f"Extração SEI: {msg}"

    pdf_unificado = fitz.open()
    for doc in resultado["documentos"]:
        try:
            raw_bytes = bytes(doc["base64"]) if isinstance(doc["base64"], (list, bytes)) else base64.b64decode(doc["base64"])
            # Se for imagem (JPEG, PNG, etc.), converte em página PDF
            if raw_bytes.startswith(b"\xff\xd8\xff") or raw_bytes.startswith(b"\x89PNG") or raw_bytes.startswith(b"GIF8"):
                img_doc = fitz.open(stream=raw_bytes, filetype="jpg" if raw_bytes.startswith(b"\xff\xd8\xff") else "png")
                img_pdf_bytes = img_doc.convert_to_pdf()
                temp_pdf = fitz.open("pdf", img_pdf_bytes)
                pdf_unificado.insert_pdf(temp_pdf)
            else:
                temp_pdf = fitz.open(stream=raw_bytes, filetype="pdf")
                pdf_unificado.insert_pdf(temp_pdf)
        except Exception as e:
            print(f"Documento '{doc.get('nome')}' ignorado: {e}")

    if len(pdf_unificado) == 0:
        return False, "Nenhuma página válida extraída dos documentos do SEI."

    buffer = io.BytesIO(pdf_unificado.write())
    buffer.seek(0)

    filename = f"{processo.numero.replace('/', '-').replace('.', '-')}_completo.pdf"
    try:
        full_path = upload_file_to_gcs(buffer, filename, "application/pdf")
        processo.arquivoPdf = full_path
        processo.erro_processamento = None
        processo.status = "Pré-análise"
        db.session.commit()
        return True, full_path
    except Exception as e:
        return False, f"Falha ao subir PDF para o GCS: {str(e)}"


def _ensure_pdf_in_gcs(processo, run: ResumoBatchRun) -> bool:
    """
    Se o processo não tem PDF no GCS, busca os documentos no SEI,
    concatena em um único PDF e sobe para o GCS.
    Retorna True se o PDF está disponível, False se falhou.
    Registra falha de extração no processo caso não consiga baixar os documentos.
    """
    if processo.arquivoPdf:
        return True

    from app.models import db

    _append_batch_log(run, "info", f"Buscando documentos no SEI para o processo {processo.numero}...")
    sucesso, res_ou_erro = download_and_upload_sei_pdf(processo)

    if sucesso:
        _append_batch_log(run, "info", f"PDF de {processo.numero} salvo no GCS.")
        processo.erro_processamento = None
        processo.status = "Pré-análise"
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
        return True
    else:
        _append_batch_log(run, "error", f"Falha ao obter PDF para {processo.numero}: {res_ou_erro}")
        processo.status_processamento = "Falhou"
        processo.status = "Falha na análise"
        processo.erro_processamento = res_ou_erro
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
        return False
