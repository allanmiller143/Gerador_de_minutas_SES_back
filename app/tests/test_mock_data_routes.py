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


def test_get_sei_generates_resumo_tecnico_from_pdf_with_project_spec(client, monkeypatch):
    from app.routes import mock_data as mock_data_route
    from app.utils.pdf_extraction_service import PdfExtractionResult

    captured = {}

    monkeypatch.setattr(
        mock_data_route.PdfExtractionService,
        "extract_text",
        staticmethod(lambda _pdf: PdfExtractionResult(text="texto extraído do PDF real", text_chars=26)),
    )
    monkeypatch.setattr(
        mock_data_route.SupportDocumentService,
        "build_context",
        lambda self, max_trechos_suporte=12: "contexto técnico de suporte",
    )

    def _generate_resumo(self, **kwargs):
        captured.update(kwargs)
        return {
            "resumo_processo": {
                "tipo_demanda": "gerado pelo gemini",
                "medicamento_solicitado": "medicamento extraído do PDF",
            },
            "evidencias_clinicas_do_processo": ["evidência gerada"],
            "confronto_documentacao_suporte": {
                "cid_validado": False,
                "medicamento_contemplado_para_o_cid": "indeterminado",
                "observacoes": ["observação gerada"],
            },
            "insumo_parecer": {
                "conclusao_tecnica_sugerida": "conclusão gerada",
                "necessita_revisao_humana": True,
                "nivel_confianca": "alto",
            },
            "fontes_consultadas": ["Texto extraído do PDF do processo"],
        }

    monkeypatch.setattr(mock_data_route.ResumoService, "generate_resumo", _generate_resumo)

    response = client.get("/api/seis/1")

    assert response.status_code == 200
    data = response.get_json()
    resumo = data["resumoTecnico"]
    assert set(resumo) == {
        "resumo_processo",
        "evidencias_clinicas_do_processo",
        "confronto_documentacao_suporte",
        "insumo_parecer",
        "fontes_consultadas",
    }
    assert resumo["resumo_processo"]["tipo_demanda"] == "gerado pelo gemini"
    assert resumo["evidencias_clinicas_do_processo"] == ["evidência gerada"]
    assert resumo["insumo_parecer"]["necessita_revisao_humana"] is True
    assert captured["process_text"] == "texto extraído do PDF real"
    assert captured["support_context"] == "contexto técnico de suporte"
    assert captured["include_minuta"] is True


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
