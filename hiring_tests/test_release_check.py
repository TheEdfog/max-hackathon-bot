import pytest
from scripts.release_check import deployment_url_set, safe_release_name


@pytest.mark.parametrize('name', ['data/test.db', '.env.hiring', 'nested/.env', 'nested/token.pem',
    'secret.key', 'run.log', 'storage/attachments/resume.pdf', '../escape', '/absolute', 'data\\secret', '.ENV.HIRING'])
def test_release_rejects_private_paths(name):
    assert not safe_release_name(name)


@pytest.mark.parametrize('name', ['README.md', 'hiring/bot.py', '.env.hiring.example', 'storage/attachments/.gitkeep'])
def test_release_allows_source_and_templates(name):
    assert safe_release_name(name)


@pytest.mark.parametrize('url', ['http://service.ru', 'https://deployment-required.invalid',
    'https://api.example.com', 'https://localhost', 'https://127.0.0.1', 'https://192.168.1.5',
    'https://host.local', 'https://user:pass@service.ru', 'https://service.ru:8000',
    'https://service.ru/api', 'https://service.ru?token=example', 'https://service.ru#fragment',
    'https://service.ru:bad'])
def test_placeholder_or_unsafe_submission_url_rejected(url):
    assert not deployment_url_set(url)


def test_deployment_url_check_is_syntax_only():
    assert deployment_url_set('https://synthetic-hackathon-host.ru')
    assert deployment_url_set('https://synthetic-hackathon-host.ru:443/')
