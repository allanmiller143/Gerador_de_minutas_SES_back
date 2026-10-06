def auth_headers(client):
    client.post(
        "/auth/register",
        json={
            "username": "textos_padroes_test",
            "email": "textos_padroes_test@email.com",
            "password": "password123",
            "role": "admin",
        },
    )
    login = client.post(
        "/auth/login",
        json={"email": "textos_padroes_test@email.com", "password": "password123"},
    )
    token = login.get_json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_textos_padroes_crud_and_partial_update(client):
    headers = auth_headers(client)

    unauthorized = client.get("/textos-padroes/")
    assert unauthorized.status_code == 401

    categoria_response = client.post(
        "/categorias-textos-padroes/",
        json={"nome": "Atendimento"},
        headers=headers,
    )
    assert categoria_response.status_code == 201
    categoria = categoria_response.get_json()

    created = client.post(
        "/textos-padroes/",
        json={
            "titulo": "Resposta inicial",
            "conteudo": "Conteúdo original",
            "categoriaId": categoria["id"],
        },
        headers=headers,
    )
    assert created.status_code == 201
    texto = created.get_json()
    texto_id = texto["id"]
    assert texto == {
        "id": texto_id,
        "titulo": "Resposta inicial",
        "conteudo": "Conteúdo original",
        "categoriaId": categoria["id"],
    }

    listing = client.get("/textos-padroes/", headers=headers)
    assert listing.status_code == 200
    assert texto in listing.get_json()

    updated = client.put(
        f"/textos-padroes/{texto_id}",
        json={"titulo": "Resposta atualizada"},
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.get_json() == {
        "id": texto_id,
        "titulo": "Resposta atualizada",
        "conteudo": "Conteúdo original",
        "categoriaId": categoria["id"],
    }

    deleted = client.delete(f"/textos-padroes/{texto_id}", headers=headers)
    assert deleted.status_code == 200
    assert deleted.get_json()["msg"]
    assert client.delete(
        f"/textos-padroes/{texto_id}",
        headers=headers,
    ).status_code == 404


def test_textos_padroes_validates_texts_and_categories(client):
    headers = auth_headers(client)

    for payload in (
        {"conteudo": "Conteúdo", "categoriaId": 1},
        {"titulo": "Título", "categoriaId": 1},
        {"titulo": " ", "conteudo": "Conteúdo", "categoriaId": 1},
        {"titulo": "Título", "conteudo": " ", "categoriaId": 1},
        {"titulo": "Título", "conteudo": "Conteúdo", "categoriaId": "1"},
    ):
        response = client.post("/textos-padroes/", json=payload, headers=headers)
        assert response.status_code == 400
        assert "msg" in response.get_json()

    unknown_category = client.post(
        "/textos-padroes/",
        json={"titulo": "Título", "conteudo": "Conteúdo", "categoriaId": 987654},
        headers=headers,
    )
    assert unknown_category.status_code == 404
    assert "msg" in unknown_category.get_json()

    empty_category = client.post(
        "/categorias-textos-padroes/",
        json={"nome": "  "},
        headers=headers,
    )
    assert empty_category.status_code == 400

    categoria = client.post(
        "/categorias-textos-padroes/",
        json={"nome": "Padrões"},
        headers=headers,
    ).get_json()
    texto = client.post(
        "/textos-padroes/",
        json={
            "titulo": "Título",
            "conteudo": "Conteúdo",
            "categoriaId": categoria["id"],
        },
        headers=headers,
    ).get_json()

    invalid_update = client.put(
        f"/textos-padroes/{texto['id']}",
        json={"conteudo": ""},
        headers=headers,
    )
    assert invalid_update.status_code == 400

    nonexistent_update = client.put(
        "/textos-padroes/987654",
        json={"titulo": "Título"},
        headers=headers,
    )
    assert nonexistent_update.status_code == 404

    nonexistent_delete = client.delete("/categorias-textos-padroes/987654", headers=headers)
    assert nonexistent_delete.status_code == 404


def test_deleting_category_keeps_texts_without_category(client):
    headers = auth_headers(client)
    categoria = client.post(
        "/categorias-textos-padroes/",
        json={"nome": "Temporária"},
        headers=headers,
    ).get_json()
    texto = client.post(
        "/textos-padroes/",
        json={
            "titulo": "Mantido",
            "conteudo": "Continua disponível",
            "categoriaId": categoria["id"],
        },
        headers=headers,
    ).get_json()

    deleted = client.delete(
        f"/categorias-textos-padroes/{categoria['id']}",
        headers=headers,
    )
    assert deleted.status_code == 200

    listed = client.get("/textos-padroes/", headers=headers)
    texto_atualizado = next(item for item in listed.get_json() if item["id"] == texto["id"])
    assert texto_atualizado["categoriaId"] == 0
    assert texto_atualizado["titulo"] == "Mantido"

    categories = client.get("/categorias-textos-padroes/", headers=headers)
    assert categoria not in categories.get_json()
