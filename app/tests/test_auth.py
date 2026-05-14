def test_register(client):

    response = client.post('/auth/register', json={
        'username': 'allan',
        'email': 'allan@email.com',
        'password': '123456',
        'role': 'admin'
    })

    assert response.status_code == 201