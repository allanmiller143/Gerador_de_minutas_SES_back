def get_auth_token(client):
    client.post('/auth/register', json={
        'username': 'testuser_remetente',
        'email': 'remetente@email.com',
        'password': 'password123',
        'role': 'admin'
    })
    login = client.post('/auth/login', json={
        'email': 'remetente@email.com',
        'password': 'password123'
    })
    return login.get_json()['access_token']

def test_remetentes_crud(client):
    token = get_auth_token(client)
    headers = {'Authorization': f'Bearer {token}'}

    # 1. List initial remetentes
    res = client.get('/remetentes/', headers=headers)
    assert res.status_code == 200
    assert isinstance(res.get_json(), list)

    # 2. Create remetente
    payload = {
        'prefixo': 'REQ',
        'nome_completo': 'Tribunal de Justiça de São Paulo',
        'sigla': 'TJSP',
        'cor': '#0055A5'
    }
    res_create = client.post('/remetentes/', json=payload, headers=headers)
    assert res_create.status_code == 201
    created_data = res_create.get_json()
    assert created_data['prefixo'] == 'REQ'
    assert created_data['sigla'] == 'TJSP'
    assert created_data['cor'] == '#0055A5'
    remetente_id = created_data['id']

    # 3. Get created remetente
    res_get = client.get(f'/remetentes/{remetente_id}', headers=headers)
    assert res_get.status_code == 200
    assert res_get.get_json()['nome_completo'] == 'Tribunal de Justiça de São Paulo'

    # 4. Update remetente
    update_payload = {
        'sigla': 'TJSP-CAPITAL',
        'cor': 'rgb(0, 85, 165)'
    }
    res_update = client.put(f'/remetentes/{remetente_id}', json=update_payload, headers=headers)
    assert res_update.status_code == 200
    updated_data = res_update.get_json()
    assert updated_data['sigla'] == 'TJSP-CAPITAL'
    assert updated_data['cor'] == 'rgb(0, 85, 165)'

    # 5. Delete remetente
    res_delete = client.delete(f'/remetentes/{remetente_id}', headers=headers)
    assert res_delete.status_code == 200

    # 6. Verify deletion
    res_verify = client.get(f'/remetentes/{remetente_id}', headers=headers)
    assert res_verify.status_code == 404
