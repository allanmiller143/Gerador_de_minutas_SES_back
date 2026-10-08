import pytest
from app.models import db, ProcessoSEI, ResumoBatchRun, ResumoTecnicoVersion
from app.routes.processos import rehydrate_pending_analysis, analysis_queue, _process_queued_analysis
from app.routes.mock_data import _finish_interrupted_runs_on_startup

def test_rehydrate_enqueues_pending_processes_and_starts_new_batch(app, monkeypatch):
    with app.app_context():
        # Intercepta colocação na fila para teste determinístico sem consumo concorrente da worker thread
        enqueued_items = []
        monkeypatch.setattr(
            "app.routes.processos.analysis_queue.put",
            lambda item: enqueued_items.append(item)
        )

        # 1. Simula uma execução anterior que estava em andamento no momento da queda/crash
        run_antiga = ResumoBatchRun(
            triggered_by="sistema",
            trigger_type="manual",
            status="running",
        )
        run_antiga.append_log("info", "Execução interrompida pela queda.")
        db.session.add(run_antiga)
        db.session.commit()
        run_antiga_id = run_antiga.id

        # 2. Cria processo com status_processamento='Pendente' sem PDF (como recém-importado do SEI)
        p_pendente = ProcessoSEI(
            numero="0001111-22.2024.8.26.0053",
            assunto="Processo Pendente Sem PDF",
            status="Pré-análise",
            prioridade="Média",
            status_processamento="Pendente",
            arquivoPdf=None,
        )
        # Cria processo que ficou travado em 'Processando' durante queda/crash do sistema
        p_interrompido = ProcessoSEI(
            numero="0002222-33.2024.8.26.0053",
            assunto="Processo Interrompido em Andamento",
            status="Pré-análise",
            prioridade="Alta",
            status_processamento="Processando",
            arquivoPdf=None,
            erro_processamento="Tentativa anterior",
        )
        db.session.add_all([p_pendente, p_interrompido])
        db.session.commit()

        # Executa a reidratação de inicialização
        novo_batch = rehydrate_pending_analysis(app, force=True)

        # 1. Verifica se a execução anterior foi marcada como interrupted
        run_antiga_atualizada = db.session.get(ResumoBatchRun, run_antiga_id)
        assert run_antiga_atualizada.status == "interrupted"
        assert "reinicialização do sistema" in run_antiga_atualizada.error_message

        # 2. Verifica se o novo ResumoBatchRun foi criado para registrar os processos da reidratação
        assert novo_batch is not None
        assert novo_batch.id != run_antiga_id
        assert novo_batch.status == "running"
        assert novo_batch.trigger_type == "reidratação"
        assert novo_batch.total_seis == 2
        assert "reidratação" in novo_batch.logs[0]["message"].lower()

        # 3. Verifica se o processo interrompido voltou para 'Pendente' e não para 'Falhou'
        db.session.refresh(p_interrompido)
        assert p_interrompido.status_processamento == "Pendente"
        assert p_interrompido.erro_processamento is None

        # 4. Verifica se ambos foram colocados na fila de análise vinculados ao novo ResumoBatch
        enqueued_ids = [item[1] for item in enqueued_items]
        assert p_pendente.id in enqueued_ids
        assert p_interrompido.id in enqueued_ids

        for item in enqueued_items:
            # item tuple: (app, processo_id, apenas_minuta, retry_count, batch_run_id)
            assert len(item) == 5
            assert item[4] == novo_batch.id


def test_finish_interrupted_runs_on_startup(app):
    with app.app_context():
        run = ResumoBatchRun(
            triggered_by="sistema",
            trigger_type="manual",
            status="running",
        )
        run.append_log("info", "Execução em andamento antes do crash.")
        db.session.add(run)
        db.session.commit()
        run_id = run.id

        _finish_interrupted_runs_on_startup()

        updated_run = db.session.get(ResumoBatchRun, run_id)
        assert updated_run.status == "interrupted"
        assert "reinicialização do sistema" in updated_run.error_message


def test_rehydrated_batch_updates_on_process_completion(app, monkeypatch):
    with app.app_context():
        # Mock para evitar chamadas de rede ou RPA externas
        monkeypatch.setattr(
            "app.routes.mock_data.download_and_upload_sei_pdf",
            lambda proc: (True, "gs://mock-bucket/mock.pdf")
        )
        monkeypatch.setattr(
            "app.routes.processos._extract_process_text_once",
            lambda proc, file_uri=None, mime_type="application/pdf": type("Extraction", (), {"text": "Texto processo", "text_chars": 14})()
        )
        monkeypatch.setattr(
            "app.routes.mock_data._persist_generated_resumo",
            lambda sei, generated_by, source, batch_run_id=None, **kwargs: ResumoTecnicoVersion.create_new(
                sei_id=sei["id"],
                payload={"resumo": "mock"},
                minuta="Minuta mock",
                generated_by=generated_by,
                source=source,
                batch_run_id=batch_run_id,
            )
        )
        monkeypatch.setattr(
            "app.routes.processos._execute_analise_processo",
            lambda proc, apenas_minuta=False, process_text=None: setattr(proc, "minuta", "Minuta final")
        )

        p = ProcessoSEI(
            numero="0003333-44.2024.8.26.0053",
            assunto="Processo Teste Conclusão",
            status="Pré-análise",
            prioridade="Média",
            status_processamento="Pendente",
            arquivoPdf="gs://mock-bucket/mock.pdf",
        )
        batch = ResumoBatchRun(
            triggered_by="sistema",
            trigger_type="reidratação",
            status="running",
            total_seis=1,
        )
        batch.append_log("info", "Início do teste.")
        db.session.add_all([p, batch])
        db.session.commit()

        # Executa o processamento com o batch_run_id
        _process_queued_analysis(p.id, apenas_minuta=False, retry_count=0, batch_run_id=batch.id)

        db.session.refresh(p)
        db.session.refresh(batch)

        assert p.status_processamento == "Concluído"
        assert batch.status == "success"
        assert batch.generated_count == 1
        assert str(p.id) in batch.sei_ids
        assert any("Resumo gerado para o processo SEI" in log["message"] for log in batch.logs)
