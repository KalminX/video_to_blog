def test_home_route(client):
    response = client.get('/')
    assert response.status_code in (200, 302)
