from datetime import datetime, timezone

from app.models import ResumoBatchRun, ResumoBatchSchedule, ResumoReexecutionRequest, ResumoTecnicoVersion, db


def test_manual_generation_persists_resumo_history_and_active_version(client, monkeypatch):
    from app.routes import mock_data as mock_data_route

    counter = {"n": 0}

    def fake_generate(sei):
        counter["n"] += 1
        return {
            "resumo_processo": {"tipo_demanda": f"geração {counter['n']}"},
            "evidencias_clinicas_do_processo": [f"evidência {counter['n']}"],
            "confronto_documentacao_suporte": {"cid_validado": False, "observacoes": []},
            "insumo_parecer": {"conclusao_tecnica_sugerida": f"conclusão {counter['n']}"},
            "fontes_consultadas": ["teste"],
        }

    monkeypatch.setattr(mock_data_route, "_generate_resumo_tecnico_from_pdf", fake_generate)

    first = client.post("/api/seis/1/resumos/generate", json={"triggered_by": "analista@ses.test"})
    second = client.post("/api/seis/1/resumos/generate", json={"triggered_by": "analista@ses.test"})

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.get_json()["version"] == 1
    assert second.get_json()["version"] == 2
    assert second.get_json()["is_active"] is True
    assert second.get_json()["generated_by"] == "analista@ses.test"
    assert second.get_json()["generated_at"]

    versions = client.get("/api/seis/1/resumos").get_json()["resumos"]
    assert [version["version"] for version in versions] == [2, 1]
    assert [version["is_active"] for version in versions] == [True, False]

    current = client.get("/api/seis/1/resumo-tecnico").get_json()
    assert current["resumoTecnico"]["resumo_processo"]["tipo_demanda"] == "geração 2"


def test_restore_old_resumo_version_marks_it_active(client, monkeypatch):
    from app.routes import mock_data as mock_data_route

    payloads = [
        {"resumo_processo": {"tipo_demanda": "versão antiga"}},
        {"resumo_processo": {"tipo_demanda": "versão nova"}},
    ]

    monkeypatch.setattr(mock_data_route, "_generate_resumo_tecnico_from_pdf", lambda sei: payloads.pop(0))

    old_id = client.post("/api/seis/1/resumos/generate", json={"triggered_by": "ana"}).get_json()["id"]
    client.post("/api/seis/1/resumos/generate", json={"triggered_by": "ana"})

    restored = client.post(f"/api/seis/1/resumos/{old_id}/restore", json={"triggered_by": "coordenadora"})

    assert restored.status_code == 200
    assert restored.get_json()["is_active"] is True
    assert restored.get_json()["resumoTecnico"]["resumo_processo"]["tipo_demanda"] == "versão antiga"


def test_batch_run_generates_only_missing_or_requeued_summaries(client, monkeypatch):
    from app.routes import mock_data as mock_data_route

    generated = []

    def fake_generate(sei):
        generated.append(sei["id"])
        return {"resumo_processo": {"tipo_demanda": f"gerado para {sei['id']}"}}

    monkeypatch.setattr(mock_data_route, "_generate_resumo_tecnico_from_pdf", fake_generate)

    # Preexistente: SEI 1 não deve ser regerado sem marcação.
    with client.application.app_context():
        ResumoTecnicoVersion.create_new(
            sei_id="1",
            payload={"resumo_processo": {"tipo_demanda": "pré-existente"}},
            minuta="minuta",
            generated_by="setup",
            source="manual",
        )
        db.session.add(ResumoReexecutionRequest(sei_id="2", requested_by="ana"))
        db.session.commit()

    with client.application.app_context():
        data = mock_data_route._run_resumo_batch("scheduler").to_dict()

    assert data["status"] == "success"
    assert "1" not in generated
    assert "2" in generated
    assert data["generated_count"] == 9
    assert data["triggered_by"] == "scheduler"
    assert data["duration_seconds"] >= 0
    with client.application.app_context():
        assert ResumoBatchRun.query.count() == 1


def test_manual_batch_start_returns_immediately_without_running_generation(client, monkeypatch):
    from app.routes import mock_data as mock_data_route

    started_runs = []

    def fail_if_called(sei):
        raise AssertionError("a geração pesada não deve rodar dentro da requisição HTTP")

    def fake_start(app, run_id):
        started_runs.append(run_id)

    monkeypatch.setattr(mock_data_route, "_generate_resumo_tecnico_from_pdf", fail_if_called)
    monkeypatch.setattr(mock_data_route, "_start_resumo_batch_thread", fake_start)

    response = client.post("/api/resumo-batch/run", json={"triggered_by": "admin@ses.test"})

    assert response.status_code == 202
    data = response.get_json()
    assert data["status"] == "running"
    assert data["triggered_by"] == "admin@ses.test"
    assert started_runs == [data["id"]]


def test_schedule_can_be_configured_and_suspended(client):
    response = client.put(
        "/api/resumo-batch/config",
        json={"enabled": True, "time": "03:30", "updated_by": "admin@ses.test"},
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data["enabled"] is True
    assert data["time"] == "03:30"
    assert data["updated_by"] == "admin@ses.test"

    disabled = client.put("/api/resumo-batch/config", json={"enabled": False, "updated_by": "admin@ses.test"})
    assert disabled.status_code == 200
    assert disabled.get_json()["enabled"] is False
    with client.application.app_context():
        assert ResumoBatchSchedule.query.first().enabled is False


def test_batch_history_exposes_status_actor_duration_logs_and_affected_seis(client):
    started = datetime(2026, 1, 1, 3, 0, tzinfo=timezone.utc)
    finished = datetime(2026, 1, 1, 3, 2, tzinfo=timezone.utc)
    with client.application.app_context():
        run = ResumoBatchRun(
            status="success",
            trigger_type="manual",
            triggered_by="admin@ses.test",
            started_at=started,
            finished_at=finished,
            duration_seconds=120,
            total_seis=2,
            generated_count=2,
            failed_count=0,
        )
        run.sei_ids = ["1", "2"]
        run.append_log("info", "Execução criada")
        run.append_log("success", "Execução finalizada")
        db.session.add(run)
        db.session.commit()

    response = client.get("/api/resumo-batch/runs")

    assert response.status_code == 200
    run = response.get_json()["runs"][0]
    assert run["status"] == "success"
    assert run["triggered_by"] == "admin@ses.test"
    assert run["duration_seconds"] == 120
    assert run["sei_ids"] == ["1", "2"]
    assert [log["message"] for log in run["logs"]] == ["Execução criada", "Execução finalizada"]
    assert run["logs"][0]["level"] == "info"
    assert run["logs"][0]["timestamp"]


def test_batch_execution_persists_live_console_logs(client, monkeypatch):
    from app.routes import mock_data as mock_data_route

    generated = []

    def fake_generate(sei):
        generated.append(sei["id"])
        return {"resumo_processo": {"tipo_demanda": f"gerado para {sei['id']}"}}

    monkeypatch.setattr(mock_data_route, "SEIS", mock_data_route.SEIS[:2])
    monkeypatch.setattr(mock_data_route, "_generate_resumo_tecnico_from_pdf", fake_generate)

    with client.application.app_context():
        data = mock_data_route._run_resumo_batch("admin@ses.test").to_dict()

    assert data["status"] == "success"
    assert data["generated_count"] == 2
    assert generated == ["1", "2"]
    messages = [log["message"] for log in data["logs"]]
    assert messages[0] == "Execução manual iniciada por admin@ses.test."
    assert "2 processo(s) SEI pendente(s) para processamento." in messages
    assert any("Iniciando processo SEI 1/2: 0001234-56.2024.8.26.0053 — Fornecimento de medicamento oncológico" in message for message in messages)
    assert any("Resumo gerado para o processo SEI 0001234-56.2024.8.26.0053" in message for message in messages)
    assert any("Iniciando processo SEI 2/2: 0002345-67.2024.8.26.0053" in message for message in messages)
    assert any("Resumo gerado para o processo SEI 0002345-67.2024.8.26.0053" in message for message in messages)
    assert messages[-1] == "Execução finalizada com sucesso: 2 resumo(s) gerado(s), 0 falha(s)."


def test_batch_cancel_request_marks_running_run_and_stops_before_next_sei(client, monkeypatch):
    from app.routes import mock_data as mock_data_route

    generated = []

    def fake_generate(sei):
        generated.append(sei["id"])
        if sei["id"] == "1":
            run = ResumoBatchRun.query.first()
            run.status = "cancel_requested"
            run.append_log("warning", "Cancelamento solicitado por admin@ses.test.")
            db.session.commit()
        return {"resumo_processo": {"tipo_demanda": f"gerado para {sei['id']}"}}

    monkeypatch.setattr(mock_data_route, "SEIS", mock_data_route.SEIS[:3])
    monkeypatch.setattr(mock_data_route, "_generate_resumo_tecnico_from_pdf", fake_generate)

    with client.application.app_context():
        data = mock_data_route._run_resumo_batch("admin@ses.test").to_dict()

    assert generated == ["1"]
    assert data["status"] == "canceled"
    assert data["generated_count"] == 1
    assert data["total_seis"] == 3
    messages = [log["message"] for log in data["logs"]]
    assert any("Cancelamento solicitado" in message for message in messages)
    assert messages[-1] == "Execução suspensa antes do próximo processo: 1 de 3 resumo(s) gerado(s)."


def test_cancel_running_batch_endpoint(client):
    with client.application.app_context():
        run = ResumoBatchRun(triggered_by="sistema", trigger_type="manual", status="running")
        db.session.add(run)
        db.session.commit()
        run_id = run.id

    response = client.post(f"/api/resumo-batch/runs/{run_id}/cancel", json={"triggered_by": "admin@ses.test"})

    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "cancel_requested"
    assert data["logs"][-1]["message"] == "Cancelamento solicitado por admin@ses.test. A execução será suspensa ao concluir o processo atual."


def test_history_marks_old_running_without_logs_as_interrupted(client):
    with client.application.app_context():
        run = ResumoBatchRun(triggered_by="sistema", trigger_type="manual", status="running")
        db.session.add(run)
        db.session.commit()

    response = client.get("/api/resumo-batch/runs")

    assert response.status_code == 200
    data = response.get_json()["runs"][0]
    assert data["status"] == "interrupted"
    assert data["logs"][-1]["level"] == "warning"
    assert "não tinha logs de progresso" in data["logs"][-1]["message"]
