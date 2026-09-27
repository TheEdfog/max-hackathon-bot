from test_product import client, register, job


def test_api_root_replaces_website_and_unknown_pages_are_404(client):
    client.app.state.config.bot_name = 'test_bot'
    result = client.get('/')
    assert result.json()['channel'] == 'MAX'
    assert result.json()['bot_url'] == 'https://max.ru/test_bot'
    assert client.get('/old-dashboard').status_code == 404
    assert client.get('/assets/app.js').status_code == 404
    assert client.post('/api/auth/demo').status_code == 404


def test_apply_links_redirect_only_for_active_jobs(client):
    owner = register(client, 'owner', 'employer')
    jid = job(client, owner)
    assert client.get(f'/apply/{jid}', follow_redirects=False).status_code == 503
    client.app.state.config.bot_name = 'test_bot'
    result = client.get(f'/apply/{jid}', follow_redirects=False)
    assert result.status_code == 307
    assert result.headers['location'] == f'https://max.ru/test_bot?start=apply_{jid}'
    client.patch(f'/api/jobs/{jid}', headers=owner, json={'active': False})
    assert client.get(f'/apply/{jid}', follow_redirects=False).status_code == 404
    assert client.get('/apply/missing', follow_redirects=False).status_code == 404
