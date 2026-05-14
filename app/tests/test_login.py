def test_login(client):

    client.post('/auth/register', json={
        'username': 'allan1',
        'email': 'allan1@email.com',
        'password': '123456',
        'role': 'admin'
    })

    response = client.post('/auth/login', json={
        'email': 'allan1@email.com',
        'password': '123456'
    })

    data = response.get_json()

    assert response.status_code == 200
    assert 'access_token' in data