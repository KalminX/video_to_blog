def test_upload_page(client):
    response = client.get('/upload')
    assert response.status_code in (200, 302)

def test_dashboard_redirect(client):
    response = client.get('/dashboard')
    assert response.status_code in (200, 302)
