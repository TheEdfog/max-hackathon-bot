import json
import pytest
import httpx
from sqlalchemy import func, select
from hiring import ai_questions as ai
from hiring.db import AssessmentTemplate
from test_product import client, register
from test_integrations import key, PREFIX

BODY = {'skills': ['питон', 'sql'], 'level': 'junior', 'allow_external_generation': True}
DRAFT = {'title': 'Python и SQL', 'questions': [{'text': 'Как проверить корректность загрузки данных в таблицу?',
                                               'rubric': 'Обсудить дубликаты, типы и контрольные суммы.'}]}


def headers(client, name='owner', scopes=None):
    owner = register(client, name, 'employer')
    return key(client, owner, scopes or ['tests:read', 'tests:write'])[0]


def enable(client):
    client.app.state.config.gigachat_enabled = True
    client.app.state.config.cloudru_api_key = 'synthetic-runtime-key'


def test_local_fallback_and_no_implicit_publication(client, monkeypatch):
    auth = headers(client)
    monkeypatch.setattr(ai, 'generate', lambda *args: pytest.fail('Disabled provider called'))
    response = client.post(PREFIX + '/tests/ai-draft', headers=auth, json=BODY)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['source'] == 'local' and data['review_required'] and not data['published']
    assert client.get(PREFIX + '/tests', headers=auth).json()['items'] == []
    assert 'python' in client.get(PREFIX + '/tests/ai-skills', headers=auth).json()


@pytest.mark.parametrize('change', [{'skills': ['secret@example.com']}, {'resume': 'private'},
    {'allow_external_generation': False}, {'skills': ['python', 'питон']}, {'level': 'anything'}])
def test_no_free_text_or_pii_and_explicit_consent(client, change):
    response = client.post(PREFIX + '/tests/ai-draft', headers=headers(client), json={**BODY, **change})
    assert response.status_code == 422


def test_scopes_and_owner_cache(client, monkeypatch):
    enable(client)
    seen = []
    def fake(body, api_key):
        seen.append(body.model_dump())
        return ai.TestBody.model_validate(DRAFT)
    monkeypatch.setattr(ai, 'generate', fake)
    auth = headers(client)
    first = client.post(PREFIX + '/tests/ai-draft', headers=auth, json=BODY).json()
    second = client.post(PREFIX + '/tests/ai-draft', headers=auth, json=BODY).json()
    assert first['source'] == 'gigachat' and not first['cached'] and second['cached']
    other = headers(client, 'other')
    assert not client.post(PREFIX + '/tests/ai-draft', headers=other, json=BODY).json()['cached']
    assert len(seen) == 2 and seen[0]['skills'] == ['python', 'sql']
    read_only = headers(client, 'reader', ['tests:read'])
    assert client.post(PREFIX + '/tests/ai-draft', headers=read_only, json=BODY).status_code == 403
    assert client.post(PREFIX + '/tests', headers=auth, json=first['draft']).status_code == 201
    with client.app.state.factory() as db:
        assert db.scalar(select(func.count()).select_from(AssessmentTemplate)) == 1


def test_persistent_limit_and_failed_request_not_retried(client, monkeypatch):
    enable(client)
    auth = headers(client)
    calls = []
    def fail(*args):
        calls.append(1)
        raise httpx.ConnectError('secret upstream details must not leak')
    monkeypatch.setattr(ai, 'generate', fail)
    for _ in range(2):
        response = client.post(PREFIX + '/tests/ai-draft', headers=auth, json=BODY)
        assert response.json()['source'] == 'local' and 'upstream' not in response.text
    assert len(calls) == 1
    monkeypatch.setattr(ai, 'LIFETIME_LIMIT', 1)
    assert client.post(PREFIX + '/tests/ai-draft', headers=auth, json={**BODY, 'level': 'middle'}).status_code == 429


@pytest.mark.parametrize('kind', ['ok', 'error', 'redirect', 'truncated', 'invalid', 'contact', 'large'])
def test_provider_fixed_origin_payload_limits_and_validation(monkeypatch, kind):
    real_client = httpx.Client
    calls = []
    def respond(request):
        calls.append(request)
        assert str(request.url) == ai.ENDPOINT
        payload = json.loads(request.content)
        assert payload['model'] == ai.MODEL and payload['max_tokens'] == 1000
        assert json.loads(payload['messages'][1]['content']) == {'skills': ['python', 'sql'], 'level': 'junior'}
        if kind == 'error': return httpx.Response(429)
        if kind == 'redirect': return httpx.Response(302, headers={'location': 'https://evil.test'})
        if kind == 'large': return httpx.Response(200, content=b' ' * 70000)
        data = DRAFT if kind != 'contact' else {**DRAFT, 'title': 'Write to fake@example.com'}
        return httpx.Response(200, json={'choices': [{'finish_reason': 'length' if kind == 'truncated' else 'stop',
            'message': {'content': 'bad' if kind == 'invalid' else json.dumps(data)}}]})
    def factory(**kwargs):
        assert kwargs['follow_redirects'] is False and kwargs['trust_env'] is False
        return real_client(**kwargs, transport=httpx.MockTransport(respond))
    monkeypatch.setattr(ai.httpx, 'Client', factory)
    body = ai.AiDraftBody.model_validate(BODY)
    if kind == 'ok':
        assert ai.generate(body, 'synthetic-key').title == DRAFT['title']
    else:
        with pytest.raises(ValueError): ai.generate(body, 'synthetic-key')
    assert len(calls) == 1


def test_api_key_not_in_config_repr(client):
    enable(client)
    assert 'synthetic-runtime-key' not in repr(client.app.state.config)
