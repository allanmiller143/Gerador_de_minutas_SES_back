from unittest.mock import MagicMock
import pytest

from app.utils.adk_resumo_service import AdkResumoService
from app.utils.rag_service import IncrementalRAG


@pytest.fixture
def mock_manifest_data():
    return {
        "version": "1.0",
        "created_at": "2026-10-07T18:00:00Z",
        "total_drugs": 2,
        "total_cid_prefixes": 3,
        "medication_to_indications": {
            "adalimumabe": [
                {"cid": "K50", "patologia": "Doença de Crohn"},
                {"cid": "M05/M06", "patologia": "Artrite Reumatoide"},
                {"cid": "L40", "patologia": "Psoríase"},
            ],
            "dupilumabe": [
                {"cid": "L20", "patologia": "Dermatite Atópica"},
            ],
        },
        "medication_to_presentations": {
            "adalimumabe": ["40 mg SOLUÇÃO INJETÁVEL"],
            "dupilumabe": ["300 mg SOLUÇÃO INJETÁVEL"],
        },
        "cid_to_pathology": {
            "K50": "Doença de Crohn",
            "M05": "Artrite Reumatoide",
            "L20": "Dermatite Atópica",
        },
        "cid_to_files": {
            "K50": ["base_conhecimento/protocolos_clinicos/Doença de Crohn.pdf"],
            "M05": ["base_conhecimento/protocolos_clinicos/Artrite Reumatoide.pdf"],
            "L20": ["base_conhecimento/protocolos_clinicos/Dermatite Atópica.pdf"],
        },
        "medication_to_files": {
            "adalimumabe": [
                "base_conhecimento/protocolos_clinicos/Doença de Crohn.pdf",
                "base_conhecimento/protocolos_clinicos/Artrite Reumatoide.pdf",
            ],
            "dupilumabe": [
                "base_conhecimento/protocolos_clinicos/Dermatite Atópica.pdf",
            ],
        },
    }


def test_rag_get_or_build_manifest_uses_memory_cache(mock_manifest_data, monkeypatch):
    rag = IncrementalRAG.__new__(IncrementalRAG)
    rag.ai = None
    rag.bucket = MagicMock()
    rag.knowledge_prefix = "base_conhecimento/"
    rag.index_prefix = "rag-index/"

    IncrementalRAG._manifest_cache = mock_manifest_data
    IncrementalRAG._manifest_cache_time = 9999999999.0

    manifest = rag.get_or_build_manifest()
    assert manifest["version"] == "1.0"
    assert "adalimumabe" in manifest["medication_to_indications"]
    assert not rag.bucket.blob.called  # Servido do cache sem acessar bucket


def test_rag_get_indications_for_medication(mock_manifest_data):
    rag = IncrementalRAG.__new__(IncrementalRAG)
    rag.ai = None
    rag.bucket = MagicMock()
    rag.knowledge_prefix = "base_conhecimento/"
    rag.index_prefix = "rag-index/"

    IncrementalRAG._manifest_cache = mock_manifest_data
    IncrementalRAG._manifest_cache_time = 9999999999.0

    inds = rag.get_indications_for_medication("Adalimumabe 40mg")
    assert len(inds) == 3
    cids = [x["cid"] for x in inds]
    assert "K50" in cids
    assert "M05/M06" in cids


def test_adk_service_uses_dynamic_manifest_for_matching(mock_manifest_data, monkeypatch):
    service = AdkResumoService(client=MagicMock())

    # Injeta manifesto customizado no serviço
    monkeypatch.setattr(service, "_get_manifest", lambda: mock_manifest_data)
    monkeypatch.setattr(
        service,
        "_get_knowledge_blobs",
        lambda: [
            "base_conhecimento/rename-2024.pdf",
            "base_conhecimento/protocolos_clinicos/Doença de Crohn.pdf",
            "base_conhecimento/protocolos_clinicos/Artrite Reumatoide.pdf",
            "base_conhecimento/protocolos_clinicos/Dermatite Atópica.pdf",
        ],
    )

    files = service._find_matching_knowledge_files(
        medicamento="Dupilumabe",
        cid="L20.9",
        diagnostico="Dermatite atópica",
    )
    assert any("Dermatite Atópica.pdf" in f for f in files)
    assert any("rename-2024.pdf" in f for f in files)


def test_adk_service_cids_sus_info_uses_dynamic_manifest(mock_manifest_data):
    info = AdkResumoService._get_cids_sus_info("Adalimumabe", "K29.7", manifest=mock_manifest_data)
    assert "K50" in info
    assert "Doença de Crohn" in info
    assert "M05/M06" in info
    assert "DIRETRIZ OBRIGATÓRIA DE PARECER" in info
