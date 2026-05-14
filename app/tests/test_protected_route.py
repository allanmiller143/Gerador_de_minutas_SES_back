def test_protected_route(client):

    client.post('/auth/register', json={
        'username': 'allan',
        'email': 'allan@email.com',
        'password': '123456',
        'role': 'admin'
    })

    login = client.post('/auth/login', json={
        'email': 'allan@email.com',
        'password': '123456'
    })

    token = login.get_json()['access_token']

    response = client.get(
        '/auth/protected',
        headers={
            'Authorization': f'Bearer {token}'
        }
    )

    assert response.status_code == 200