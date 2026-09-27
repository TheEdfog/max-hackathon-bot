from dataclasses import replace
import json
import httpx
import pytest
from hiring.llm import LLMSettings, LLMError, CLOUDRU_MODEL, CLOUDRU_URL, complete


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    import os
    for name in os.environ:
        if name.startswith('HIRING_LLM_') or name in ('CLOUDRU_API_KEY', 'LLM_API_KEY', 'HIRING_GIGACHAT_ENABLED'):
            monkeypatch.delenv(name)


def test_explicit_disable_overrides_old_settings(monkeypatch):
    monkeypatch.setenv('HIRING_GIGACHAT_ENABLED', 'true')
    monkeypatch.setenv('CLOUDRU_API_KEY', 'synthetic-cloudru-key')
    legacy = LLMSettings.from_env()
    assert legacy.provider == 'cloudru' and legacy.api_key == 'synthetic-cloudru-key'
    monkeypatch.setenv('HIRING_LLM_PROVIDER', 'disabled')
    assert not LLMSettings.from_env().ready


def test_blank_template_values_use_builtin_defaults(monkeypatch):
    monkeypatch.setenv('HIRING_LLM_PROVIDER', 'cloudru')
    monkeypatch.setenv('HIRING_LLM_MODEL', '')
    monkeypatch.setenv('HIRING_LLM_BASE_URL', '')
    settings = LLMSettings.from_env()
    settings.validate()
    assert settings.model == CLOUDRU_MODEL and settings.base_url == CLOUDRU_URL


def test_app_settings_repr_hides_all_credentials():
    from hiring.config import Config
    config = Config(secret='synthetic-jwt-secret', employer_code='synthetic-employer-code',
                    bot_token='synthetic-bot-token', webhook_secret='synthetic-webhook-secret',
                    llm=LLMSettings(api_key='synthetic-model-key'))
    assert 'synthetic-' not in repr(config)


@pytest.mark.parametrize('provider', ['deepseek', 'openai_compatible'])
def test_cloudru_key_never_reused_for_other_provider(monkeypatch, provider):
    monkeypatch.setenv('HIRING_LLM_PROVIDER', provider)
    monkeypatch.setenv('CLOUDRU_API_KEY', 'synthetic-cloudru-key')
    assert not LLMSettings.from_env().api_key
    monkeypatch.setenv('LLM_API_KEY', 'synthetic-other-key')
    assert LLMSettings.from_env().api_key == 'synthetic-other-key'


@pytest.mark.parametrize('change', [
    {'provider': 'invalid'}, {'model': ''}, {'max_tokens': 100000}, {'timeout_seconds': 0},
    {'owner_daily_limit': 0}, {'daily_limit': 1}, {'total_limit': 2},
    {'base_url': 'http://api.provider.test/v1'}, {'base_url': 'https://127.0.0.1/v1'},
    {'base_url': 'https://api.provider.test:444/v1'}, {'base_url': 'https://user:pass@api.provider.test/v1'},
    {'base_url': 'https://api.provider.test/v1?key=x'}, {'base_url': 'https://api.provider.test/v1#fragment'},
    {'base_url': 'https://api.provider.test.evil.test/v1'},
    {'base_url': 'https://api.local/v1', 'allowed_hosts': ('api.local',)},
])
def test_invalid_configuration_rejected(change):
    settings = LLMSettings(provider='openai_compatible', model='model-a',
                           base_url='https://api.provider.test/v1', allowed_hosts=('api.provider.test',))
    with pytest.raises(ValueError):
        replace(settings, **change).validate()


def test_builtin_provider_origin_is_fixed():
    settings = LLMSettings(provider='cloudru', model=CLOUDRU_MODEL, base_url='https://api.provider.test/v1')
    with pytest.raises(ValueError): settings.validate()


@pytest.mark.parametrize('provider,url', [('cloudru', CLOUDRU_URL), ('deepseek', 'https://api.deepseek.com/v1'),
    ('openai_compatible', 'https://api.provider.test/v1')])
def test_all_adapters_use_configured_parameters_without_retry(monkeypatch, provider, url):
    settings = LLMSettings(provider=provider, model='chosen-model', base_url=url, api_key='synthetic-key',
                           allowed_hosts=('api.provider.test',), max_tokens=450, timeout_seconds=5)
    real_client = httpx.Client
    calls = []
    def respond(request):
        calls.append(request)
        assert str(request.url) == url + '/chat/completions'
        assert request.headers['authorization'] == 'Bearer synthetic-key'
        payload = json.loads(request.content)
        assert payload['model'] == 'chosen-model' and payload['max_tokens'] == 450
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': 'answer'}}]})
    def client(**kwargs):
        assert kwargs == {'timeout': 5, 'follow_redirects': False, 'trust_env': False}
        return real_client(**kwargs, transport=httpx.MockTransport(respond))
    monkeypatch.setattr('hiring.llm.httpx.Client', client)
    assert complete(settings, [{'role': 'user', 'content': 'synthetic'}]) == 'answer'
    assert len(calls) == 1


def test_disabled_never_builds_client(monkeypatch):
    monkeypatch.setattr('hiring.llm.httpx.Client', lambda **kwargs: pytest.fail('Network forbidden'))
    with pytest.raises(LLMError): complete(LLMSettings(), [])


def test_network_error_does_not_expose_provider_details(monkeypatch):
    def fail(**kwargs): raise httpx.ConnectError('private-key-or-upstream-body')
    monkeypatch.setattr('hiring.llm.httpx.Client', fail)
    settings = LLMSettings(provider='cloudru', model=CLOUDRU_MODEL, base_url=CLOUDRU_URL, api_key='fake')
    with pytest.raises(LLMError, match='invalid_response_or_network') as error:
        complete(settings, [])
    assert 'private-key' not in str(error.value)
