from app.utils.pdf_extraction_service import PdfExtractionResult


def test_resumo_requires_pdf_bytes(client):
    response = client.post("/api/resumo", json={})
    assert response.status_code == 400
    assert "pdf_bytes" in response.get_json()["error"]


def test_resumo_rejects_non_list_pdf_bytes(client):
    response = client.post("/api/resumo", json={"pdf_bytes": {"bytes": [37, 80]}})
    assert response.status_code == 400


def test_resumo_success(client, monkeypatch):
    from app.routes import resumo as resumo_route

    monkeypatch.setattr(
        resumo_route.PdfExtractionService,
        "extract_text",
        staticmethod(lambda _pdf: PdfExtractionResult(text="texto do processo", text_chars=17)),
    )
    monkeypatch.setattr(
        resumo_route.SupportDocumentService,
        "build_context",
        lambda self, max_trechos_suporte=12: "contexto de suporte",
    )
    monkeypatch.setattr(
        resumo_route.ResumoService,
        "generate_resumo",
        lambda self, **kwargs: {
            "resumo_processo": {"tipo_demanda": "administrativa"},
            "evidencias_clinicas_do_processo": [],
            "confronto_documentacao_suporte": {},
            "insumo_parecer": {"necessita_revisao_humana": True},
            "fontes_consultadas": [],
        },
    )

    response = client.post(
        "/api/resumo",
        json={"pdf_bytes": [37, 80, 68, 70, 45, 49, 46, 55], "filename": "x.pdf"},
    )
    data = response.get_json()

    assert response.status_code == 200
    assert data["metadata"]["filename"] == "x.pdf"
    assert data["metadata"]["model"] == "gemini-2.5-pro"
    assert "resumo" in data


def test_resumo_rejects_byte_out_of_range(client):
    response = client.post("/api/resumo", json={"pdf_bytes": [256, 0, 0]})
    assert response.status_code == 400
    assert "entre 0 e 255" in response.get_json()["error"]


def test_resumo_rejects_non_pdf_bytes(client):
    response = client.post("/api/resumo", json={"pdf_bytes": [1, 2, 3, 4]})
    assert response.status_code == 422
    assert "extrair texto do PDF" in response.get_json()["error"]


def test_resumo_gemini_failure_returns_500(client, monkeypatch):
    from app.routes import resumo as resumo_route

    monkeypatch.setattr(
        resumo_route.PdfExtractionService,
        "extract_text",
        staticmethod(lambda _pdf: PdfExtractionResult(text="texto do processo", text_chars=17)),
    )
    monkeypatch.setattr(
        resumo_route.ResumoService,
        "generate_resumo",
        lambda self, **kwargs: None,
    )

    response = client.post(
        "/api/resumo",
        json={"pdf_bytes": [37, 80, 68, 70, 45, 49, 46, 55]},
    )
    assert response.status_code == 500


def test_resumo_rejects_invalid_options_object(client):
    response = client.post(
        "/api/resumo",
        json={"pdf_bytes": [37, 80, 68, 70, 45, 49, 46, 55], "options": "x"},
    )
    assert response.status_code == 400
    assert "options" in response.get_json()["error"]


def test_resumo_rejects_invalid_max_trechos(client):
    response = client.post(
        "/api/resumo",
        json={
            "pdf_bytes": [37, 80, 68, 70, 45, 49, 46, 55],
            "options": {"max_trechos_suporte": 0},
        },
    )
    assert response.status_code == 400
    assert "max_trechos_suporte" in response.get_json()["error"]


def test_resumo_options_disable_support_context(client, monkeypatch):
    from app.routes import resumo as resumo_route

    support_calls = {"count": 0}
    captured = {}

    monkeypatch.setattr(
        resumo_route.PdfExtractionService,
        "extract_text",
        staticmethod(lambda _pdf: PdfExtractionResult(text="texto do processo", text_chars=17)),
    )

    def _build_context(self, max_trechos_suporte=12):
        support_calls["count"] += 1
        return "contexto de suporte"

    monkeypatch.setattr(resumo_route.SupportDocumentService, "build_context", _build_context)

    def _generate_resumo(self, **kwargs):
        captured.update(kwargs)
        return {
            "resumo_processo": {"tipo_demanda": "administrativa"},
            "evidencias_clinicas_do_processo": [],
            "confronto_documentacao_suporte": {},
            "insumo_parecer": {"necessita_revisao_humana": True},
            "fontes_consultadas": [],
        }

    monkeypatch.setattr(resumo_route.ResumoService, "generate_resumo", _generate_resumo)

    response = client.post(
        "/api/resumo",
        json={
            "pdf_bytes": [37, 80, 68, 70, 45, 49, 46, 55],
            "options": {"usar_documentacao_suporte": False},
        },
    )

    assert response.status_code == 200
    assert support_calls["count"] == 0
    assert captured["support_context"] == ""


def test_resumo_options_pass_model_and_minuta_flag(client, monkeypatch):
    from app.routes import resumo as resumo_route

    captured = {}

    monkeypatch.setattr(
        resumo_route.PdfExtractionService,
        "extract_text",
        staticmethod(lambda _pdf: PdfExtractionResult(text="texto do processo", text_chars=17)),
    )
    monkeypatch.setattr(
        resumo_route.SupportDocumentService,
        "build_context",
        lambda self, max_trechos_suporte=12: "contexto de suporte",
    )

    def _generate_resumo(self, **kwargs):
        captured.update(kwargs)
        return {
            "resumo_processo": {"tipo_demanda": "administrativa"},
            "evidencias_clinicas_do_processo": [],
            "confronto_documentacao_suporte": {},
            "insumo_parecer": {"necessita_revisao_humana": True},
            "fontes_consultadas": [],
        }

    monkeypatch.setattr(resumo_route.ResumoService, "generate_resumo", _generate_resumo)

    response = client.post(
        "/api/resumo",
        json={
            "pdf_bytes": [37, 80, 68, 70, 45, 49, 46, 55],
            "model": "gemini-2.5-flash",
            "options": {"incluir_minuta_parecer": False},
        },
    )

    assert response.status_code == 200
    assert captured["model"] == "gemini-2.5-flash"
    assert captured["include_minuta"] is False
