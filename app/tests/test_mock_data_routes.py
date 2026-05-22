def test_list_seis_returns_frontend_mock_data(client):
    response = client.get("/api/seis")

    assert response.status_code == 200
    data = response.get_json()
    assert "seis" in data
    assert len(data["seis"]) == 10
    first = data["seis"][0]
    assert first["id"] == "1"
    assert first["numero"] == "0001234-56.2024.8.26.0053"
    assert first["jurisprudenciasSugeridas"] == ["j1", "j2", "j4"]


def test_get_sei_returns_associated_jurisprudencias_and_minuta(client):
    response = client.get("/api/seis/1")

    assert response.status_code == 200
    data = response.get_json()
    assert data["sei"]["id"] == "1"
    assert len(data["jurisprudencias"]) == 3
    assert {j["id"] for j in data["jurisprudencias"]} == {"j1", "j2", "j4"}
    assert "Processo SEI: 0001234-56.2024.8.26.0053" in data["minuta"]
    assert "Assunto: Fornecimento de medicamento oncológico" in data["minuta"]
    assert data["sei"]["documentoPdf"] == {
        "filename": "exemplo-processo-2.pdf",
        "mime_type": "application/pdf",
        "url": "/api/seis/1/pdf",
    }


def test_get_sei_returns_resumo_tecnico_in_project_spec(client):
    response = client.get("/api/seis/1")

    assert response.status_code == 200
    resumo = response.get_json()["resumoTecnico"]
    assert set(resumo) == {
        "resumo_processo",
        "evidencias_clinicas_do_processo",
        "confronto_documentacao_suporte",
        "insumo_parecer",
        "fontes_consultadas",
    }
    assert resumo["resumo_processo"] == {
        "tipo_demanda": "solicitação administrativa de medicamento",
        "medicamento_solicitado": "medicamento oncológico de alto custo não incorporado ao SUS",
        "cid_informado": "não informado no mock",
        "diagnostico_informado": "tratamento oncológico",
        "objetivo_da_solicitacao": "fornecimento de medicamento para continuidade terapêutica",
    }
    assert resumo["confronto_documentacao_suporte"]["cid_validado"] is False
    assert resumo["insumo_parecer"]["necessita_revisao_humana"] is True
    assert resumo["insumo_parecer"]["nivel_confianca"] == "médio"


def test_get_sei_pdf_returns_pdf_bytes_for_resumo(client):
    response = client.get("/api/seis/1/pdf")

    assert response.status_code == 200
    data = response.get_json()
    assert data["filename"] == "exemplo-processo-2.pdf"
    assert data["mime_type"] == "application/pdf"
    assert data["pdf_bytes"][:4] == [37, 80, 68, 70]
    assert data["size"] == len(data["pdf_bytes"])


def test_get_unknown_sei_pdf_returns_404(client):
    response = client.get("/api/seis/inexistente/pdf")

    assert response.status_code == 404
    assert response.get_json()["error"] == "SEI não encontrado."


def test_get_unknown_sei_returns_404(client):
    response = client.get("/api/seis/inexistente")

    assert response.status_code == 404
    assert response.get_json()["error"] == "SEI não encontrado."


def test_list_jurisprudencias_returns_frontend_mock_data(client):
    response = client.get("/api/jurisprudencias")

    assert response.status_code == 200
    data = response.get_json()
    assert "jurisprudencias" in data
    assert len(data["jurisprudencias"]) == 5
    assert data["jurisprudencias"][0]["id"] == "j1"
