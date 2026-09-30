"""Removed surfaces stay unavailable; matching never makes a hiring decision."""
import importlib.util
import pytest
from hiring.config import Config
from hiring.matching import evidence, questions
from test_product import client, register
from test_integrations import key, PREFIX


def test_removed_routes_are_absent_from_openapi(client):
    paths = client.get('/openapi.json').json()['paths']
    for suffix in ('/compatibility', '/github', '/review', '/draft-from-resume',
                   '/ai-skills', '/ai-draft', '/ai-status'):
        assert not any(path.endswith(suffix) for path in paths)
    for module in ('ai_questions', 'llm', 'compatibility', 'github_review', 'review_bot'):
        assert importlib.util.find_spec('hiring.' + module) is None


def test_old_model_settings_have_no_runtime_effect(monkeypatch):
    monkeypatch.setenv('HIRING_LLM_PROVIDER', 'unsupported')
    monkeypatch.setenv('CLOUDRU_API_KEY', 'synthetic-unused-secret')
    config = Config()
    config.validate()
    assert not hasattr(config, 'llm') and 'synthetic-unused-secret' not in repr(config)


def test_obsolete_write_scope_rejected(client):
    owner = register(client, 'owner', 'employer')
    response = client.post('/api/integration-keys', headers=owner,
        json={'name': 'obsolete', 'scopes': ['applications:read', 'applications:pii', 'applications:write']})
    assert response.status_code == 422


def test_three_states_without_scores_or_transitive_inference():
    reqs = [{'id': s, 'skill': s, 'label': s, 'type': 'must'} for s in ('python', 'sql', 'docker')]
    resume = 'Создал сервис на питоне. Использовал postres. Docker не использовал.'
    report = evidence(resume, {}, reqs)
    assert [r['state'] for r in report['requirements']] == ['mentioned', 'indirect', 'review']
    assert set(report) == {'requirements', 'total', 'mentioned', 'review', 'indirect', 'unknown'}
    assert [q['id'] for q in questions(resume, reqs)] == ['sql', 'docker']
    for row in report['requirements']:
        assert all(quote in resume for quote in row['snippets'])
        assert set(row) == {'id', 'skill', 'label', 'type', 'state', 'indirect_sources', 'snippets', 'answer'}
    assert report['requirements'][1]['indirect_sources'] == ['postgresql']
