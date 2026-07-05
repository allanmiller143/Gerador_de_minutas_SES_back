import io
import time
from app.models import db, ProcessoSEI, Role
from app.routes.processos import analysis_queue

def test_upload_pdf_queues_and_processes_analysis(client, monkeypatch):
    # 1. Register and log in
    client.post('/auth/register', json={
        'username': 'analista1',
        'email': 'analista1@saude.gov.br',
        'password': 'password123',
        'role': 'analyst'
    })
    login_resp = client.post('/auth/login', json={
        'email': 'analista1@saude.gov.br',
        'password': 'password123'
    })
    token = login_resp.get_json()['access_token']

    # 2. Mock GCS upload
    monkeypatch.setattr(
        "app.routes.processos.upload_file_to_gcs",
        lambda stream, filename, content_type: "gs://test-bucket/processos/test_file.pdf"
    )

    # 3. Mock Resumo Service (Phase 1)
    resumo_called = []
    def mock_persist_resumo(sei, generated_by, source, batch_run_id=None):
        resumo_called.append(sei)
        from app.models import ResumoTecnicoVersion
        version = ResumoTecnicoVersion(
            sei_id=sei["id"],
            version=1,
            minuta="Minuta mockada do resumo",
            generated_by=generated_by,
            source=source,
            is_active=True
        )
        version.payload = {"resumo_processo": {"tipo_demanda": "test"}}
        db.session.add(version)
        db.session.flush()
        return version

    monkeypatch.setattr(
        "app.routes.mock_data._persist_generated_resumo",
        mock_persist_resumo
    )

    # 4. Mock Gemini Service (Phase 2)
    gemini_called = []
    class MockGeminiService:
        def generate_response_with_file(self, file_uri, mime_type):
            gemini_called.append((file_uri, mime_type))
            return {
                "text": "Sugestão de minuta gerada pelo Gemini",
                "confidence": 0.95,
                "files": ["j1", "j3"]
            }

    monkeypatch.setattr(
        "app.utils.gemini_service.GeminiService",
        MockGeminiService
    )

    # 5. Perform the upload request
    pdf_content = b"%PDF-1.4 mock pdf data"
    response = client.post(
        "/processos/upload",
        data={
            "file": (io.BytesIO(pdf_content), "processo-upload-teste.pdf"),
            "numero": "12345.678910/2026-99",
            "assunto": "Fornecimento de medicamento especial",
            "prioridade": "Alta"
        },
        content_type="multipart/form-data",
        headers={
            "Authorization": f"Bearer {token}"
        }
    )

    assert response.status_code == 201
    data = response.get_json()
    assert data["message"] == "Upload successful"
    assert data["processo"]["status_processamento"] == "Processando"
    processo_id = int(data["processo"]["id"])

    # 6. Wait for the background queue worker to finish processing
    analysis_queue.join()

    # 7. Check database record updates after worker completes
    with client.application.app_context():
        proc = db.session.get(ProcessoSEI, processo_id)
        assert proc is not None
        assert proc.status_processamento == "Concluído"
        assert proc.status == "Pré-análise"
        assert proc.iaSugestao == "Sugestão de minuta gerada pelo Gemini"
        assert proc.iaConfidence == 0.95
        assert proc.jurisprudenciasSugeridas == ["j1", "j3"]
        assert proc.tempo_analise is not None
        assert proc.tempo_analise >= 0

    assert len(resumo_called) == 1
    assert resumo_called[0]["id"] == str(processo_id)
    assert len(gemini_called) == 1
    assert gemini_called[0][0] == "gs://test-bucket/processos/test_file.pdf"


def test_gemini_service_confidence_parsing(monkeypatch):
    from app.utils.gemini_service import GeminiService
    
    # Mock GEMINI_API_KEY environment variable to satisfy initialization check
    monkeypatch.setenv("GEMINI_API_KEY", "dummy_api_key")
    
    # Mock genai.Client and the generate_content call
    class MockResponse:
        def __init__(self, text):
            self.text = text
            
    class MockModels:
        def generate_content(self, model, contents, config):
            raw_text = (
                "Esta é uma análise técnica do processo.\n"
                "Paciente necessita do tratamento.\n"
                "CONFIDENCE_SCORE: 0.87"
            )
            return MockResponse(raw_text)
            
    class MockClient:
        def __init__(self, api_key, vertexai):
            self.models = MockModels()
            
    monkeypatch.setattr("google.genai.Client", MockClient)
    
    # Suppress knowledge base search in test
    monkeypatch.setattr(
        GeminiService,
        "filter_files_from_knowledge_base",
        lambda self, file_uri, mime_type: ["doc1.pdf", "doc2.pdf"]
    )
    
    service = GeminiService()
    result = service.generate_response_with_file(
        model="gemini-3.5-flash",
        file_uri="gs://bucket/test.pdf",
        mime_type="application/pdf"
    )
    
    assert result is not None
    # Verify confidence was parsed successfully
    assert result["confidence"] == 0.87
    # Verify the confidence tag was stripped from the clean text
    assert "CONFIDENCE_SCORE" not in result["text"]
    assert "Esta é uma análise técnica" in result["text"]


def test_manual_analysis_queues_and_processes(client, monkeypatch):
    # 1. Register and log in
    client.post('/auth/register', json={
        'username': 'analista2',
        'email': 'analista2@saude.gov.br',
        'password': 'password123',
        'role': 'analyst'
    })
    login_resp = client.post('/auth/login', json={
        'email': 'analista2@saude.gov.br',
        'password': 'password123'
    })
    token = login_resp.get_json()['access_token']

    # 2. Seed a ProcessoSEI in database
    with client.application.app_context():
        proc = ProcessoSEI(
            numero="99999.888888/2026-11",
            assunto="Tratamento médico especial",
            status="Em revisão",
            prioridade="Normal",
            arquivoPdf="gs://test-bucket/processos/manual_file.pdf"
        )
        db.session.add(proc)
        db.session.commit()
        processo_id = proc.id

    # 3. Mock Resumo Service (Phase 1)
    resumo_called = []
    def mock_persist_resumo(sei, generated_by, source, batch_run_id=None):
        resumo_called.append(sei)
        from app.models import ResumoTecnicoVersion
        version = ResumoTecnicoVersion(
            sei_id=sei["id"],
            version=1,
            minuta="Minuta mockada do resumo manual",
            generated_by=generated_by,
            source=source,
            is_active=True
        )
        version.payload = {"resumo_processo": {"tipo_demanda": "test_manual"}}
        db.session.add(version)
        db.session.flush()
        return version

    monkeypatch.setattr(
        "app.routes.mock_data._persist_generated_resumo",
        mock_persist_resumo
    )

    # 4. Mock Gemini Service (Phase 2)
    gemini_called = []
    class MockGeminiService:
        def generate_response_with_file(self, file_uri, mime_type):
            gemini_called.append((file_uri, mime_type))
            return {
                "text": "Sugestão manual gerada pelo Gemini",
                "confidence": 0.88,
                "files": ["j2", "j4"]
            }

    monkeypatch.setattr(
        "app.utils.gemini_service.GeminiService",
        MockGeminiService
    )

    # 5. Perform the manual analysis request
    response = client.post(
        f"/processos/{processo_id}/analisar",
        headers={
            "Authorization": f"Bearer {token}"
        }
    )

    assert response.status_code == 202
    data = response.get_json()
    assert data["message"] == "Análise enfileirada com sucesso"
    assert data["processo"]["status_processamento"] == "Processando"

    # 6. Wait for the background queue worker to finish processing
    analysis_queue.join()

    # 7. Check database record updates after worker completes
    with client.application.app_context():
        proc = db.session.get(ProcessoSEI, processo_id)
        assert proc is not None
        assert proc.status_processamento == "Concluído"
        assert proc.status == "Pré-análise"
        assert proc.iaSugestao == "Sugestão manual gerada pelo Gemini"
        assert proc.iaConfidence == 0.88
        assert proc.jurisprudenciasSugeridas == ["j2", "j4"]
        assert proc.tempo_analise is not None
        assert proc.tempo_analise >= 0

    assert len(resumo_called) == 1
    assert resumo_called[0]["id"] == str(processo_id)
    assert len(gemini_called) == 1
    assert gemini_called[0][0] == "gs://test-bucket/processos/manual_file.pdf"
