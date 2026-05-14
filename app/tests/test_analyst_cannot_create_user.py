def test_analyst_cannot_create_user(client):

    client.post('/auth/register', json={
        'username': 'user1',
        'email': 'user1@email.com',
        'password': '123456',
        'role': 'analyst'
    })

    login = client.post('/auth/login', json={
        'email': 'user1@email.com',
        'password': '123456'
    })

    token = login.get_json()['access_token']

    response = client.post(
        '/users/create',
        json={
            'username': 'novo',
            'email': 'novo@email.com',
            'password': '123456'
        },
        headers={
            'Authorization': f'Bearer {token}'
        }
    )

    assert response.status_code == 403