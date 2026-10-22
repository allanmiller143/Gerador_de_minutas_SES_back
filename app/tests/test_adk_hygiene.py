from app.utils.adk_resumo_service import (
    AdkResumoService,
    extract_cid_candidates,
)


def test_extract_cid_candidates_detects_real_cids():
    text = """
    Laudo médico para solicitação de medicamento.
    Paciente portador de Doença de Crohn diagnosticada sob CID K50 e K50.0.
    Histórico prévio de artrite reumatoide soropositiva (CID M05.8).
    """
    cids = extract_cid_candidates(text)
    assert "K50" in cids
    assert "K50.0" in cids
    assert "M05.8" in cids


def test_extract_cid_candidates_ignores_crm_cep_and_admin_numbers():
    text = """
    Identificação do profissional: Dr. Fulano de Tal, CRM PE 12345.
    Endereço do estabelecimento: Rua das Flores, 123. CEP 52050-000, Recife-PE.
    CNES 2611606. CPF 012.345.678-90. CNPJ 10.572.072/0001-88.
    Processo SEI nº 2300000001.000001/2026-11.
    Telefone: TEL 3184-0000.
    Diagnóstico clínico: CID G35 (Esclerose Múltipla).
    """
    cids = extract_cid_candidates(text)
    assert "G35" in cids
    # Nenhum falso positivo gerado por siglas administrativas
    for candidate in cids:
        assert candidate == "G35"


def test_normalize_payload_reconciles_cids_and_removes_crm_leak():
    raw_payload = {
        "resumo_processo": {
            "medicamento_solicitado": "Infliximabe 100mg",
            "cid_informado": "CRM 12345",  # Falso positivo alucinado
        },
        "confronto_documentacao_suporte": {},
    }
    normalized = AdkResumoService._normalize_payload(raw_payload, cids_detectados=["K50.0"])
    assert normalized["resumo_processo"]["cid_informado"] == "K50.0"
    assert normalized["confronto_documentacao_suporte"]["cid_validado"] is True


def test_normalize_payload_populates_missing_cid_with_detected():
    raw_payload = {
        "resumo_processo": {
            "medicamento_solicitado": "Adalimumabe 40mg",
            "cid_informado": "não informado",
        },
        "confronto_documentacao_suporte": {},
    }
    normalized = AdkResumoService._normalize_payload(raw_payload, cids_detectados=["M05.8"])
    assert normalized["resumo_processo"]["cid_informado"] == "M05.8"
    assert normalized["confronto_documentacao_suporte"]["cid_validado"] is True
