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

    response = client.post("/api/resumo-batch/run", json={"triggered_by": "scheduler"})

    assert response.status_code == 201
    data = response.get_json()
    assert data["status"] == "success"
    assert "1" not in generated
    assert "2" in generated
    assert data["generated_count"] == 9
    assert data["triggered_by"] == "scheduler"
    assert data["duration_seconds"] >= 0
    with client.application.app_context():
        assert ResumoBatchRun.query.count() == 1


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


def test_batch_history_exposes_status_actor_duration_and_affected_seis(client):
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
        db.session.add(run)
        db.session.commit()

    response = client.get("/api/resumo-batch/runs")

    assert response.status_code == 200
    run = response.get_json()["runs"][0]
    assert run["status"] == "success"
    assert run["triggered_by"] == "admin@ses.test"
    assert run["duration_seconds"] == 120
    assert run["sei_ids"] == ["1", "2"]
