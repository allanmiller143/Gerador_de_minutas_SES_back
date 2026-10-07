from unittest.mock import MagicMock
import pytest

from app.utils.adk_resumo_service import AdkResumoService


@pytest.fixture
def fake_blobs():
    return [
        "base_conhecimento/rename-2024.pdf",
        "base_conhecimento/REESME-2025.pdf",
        "base_conhecimento/protocolos_clinicos/Doença de Crohn.pdf",
        "base_conhecimento/protocolos_clinicos/Artrite Reumatoide.pdf",
        "base_conhecimento/protocolos_clinicos/Espondilite Ancilosante.pdf",
        "base_conhecimento/protocolos_clinicos/Psoríase.pdf",
        "base_conhecimento/protocolos_clinicos/Esclerose Múltipla.pdf",
        "base_conhecimento/protocolos_clinicos/Dermatite Atópica.pdf",
        "base_conhecimento/normas_tecnicas/ASMA PERSISTENTE GRAVE, NORMA TECNICA 08 2012.pdf",
        "base_conhecimento/normas_tecnicas/DOENCA DE PARKINSON, NORMA TECNICA 05 2013.pdf",
    ]


@pytest.fixture
def service_with_mock_storage(monkeypatch, fake_blobs):
    AdkResumoService._cached_knowledge_blobs = None
    AdkResumoService._cache_timestamp = 0.0

    service = AdkResumoService(client=MagicMock())
    monkeypatch.setattr(service, "_get_knowledge_blobs", lambda: fake_blobs)
    return service


def test_find_matching_knowledge_files_by_cid_and_medication(service_with_mock_storage):
    files = service_with_mock_storage._find_matching_knowledge_files(
        medicamento="Adalimumabe 40mg",
        cid="K50.0",
        diagnostico="Doença de Crohn",
    )
    assert any("Doença de Crohn.pdf" in f for f in files)
    assert any("rename-2024.pdf" in f for f in files)
    assert any("REESME-2025.pdf" in f for f in files)


def test_find_matching_knowledge_files_with_only_cid_finds_correct_pcdt(service_with_mock_storage):
    # Mesmo sem o diagnóstico explícito no texto, o CID-10 M05.8 deve localizar Artrite Reumatoide
    files = service_with_mock_storage._find_matching_knowledge_files(
        medicamento="Medicamento N/I",
        cid="M05.8",
        diagnostico="",
    )
    assert any("Artrite Reumatoide.pdf" in f for f in files)
    assert any("rename-2024.pdf" in f for f in files)


def test_find_matching_knowledge_files_with_high_cost_biologic_finds_pcdt(service_with_mock_storage):
    # Dupilumabe deve associar a Dermatite Atópica ou Asma
    files = service_with_mock_storage._find_matching_knowledge_files(
        medicamento="Dupilumabe 300mg",
        cid="L20",
        diagnostico="Eczema atópico",
    )
    assert any("Dermatite Atópica.pdf" in f for f in files)


def test_consultar_protocolo_sus_executes_rag_and_formats_output(service_with_mock_storage, monkeypatch):
    mock_rag = MagicMock()
    mock_rag.rag.return_value = {
        "context": "[Fonte: Doença de Crohn.pdf, página 17]\nPosologia recomendada: 40mg a cada 14 dias.",
        "hits": [{"score": 0.89, "source": "Doença de Crohn.pdf", "page": 17, "text": "Posologia..."}],
    }
    service_with_mock_storage.rag = mock_rag

    res = service_with_mock_storage._consultar_protocolo_sus(
        medicamento="Adalimumabe",
        cid="K50.0",
        diagnostico="Doença de Crohn",
    )

    assert "=== DIRETRIZES TÉCNICAS E NORMAS OFICIAIS DO SUS (PCDT / RENAME / REESME) ===" in res
    assert "Doença de Crohn.pdf" in res
    assert "Posologia recomendada: 40mg a cada 14 dias" in res
    assert mock_rag.rag.called


def test_consultar_protocolo_sus_falls_back_when_rag_fails(service_with_mock_storage, monkeypatch):
    mock_rag = MagicMock()
    mock_rag.rag.side_effect = RuntimeError("Erro de conexão no GCP RAG")
    service_with_mock_storage.rag = mock_rag

    monkeypatch.setattr(
        "app.utils.support_document_service.SupportDocumentService.build_context",
        lambda self, max_trechos_suporte=6: "Contexto técnico local de contingência",
    )

    res = service_with_mock_storage._consultar_protocolo_sus(
        medicamento="Infliximabe",
        cid="K51",
        diagnostico="Retocolite Ulcerativa",
    )

    assert "Contexto técnico local de contingência" in res
