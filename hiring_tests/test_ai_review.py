import json
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select
from fastapi.testclient import TestClient

from hiring.ai_review import (analyze_resume, employer_policy, parse_report,
                              provider_for, purge_expired, deliver_one)
from hiring.config import Config
from hiring.db import AIReview, Application, Audit, BotSession, User, now
from hiring.main import create_app
from hiring.screening import recent_timeline_question
from test_employer_bot import send
from test_product import job, register


@pytest.fixture
def client(tmp_path):
    config = Config(database_url=f"sqlite:///{tmp_path / 'ai-test.db'}",
                    secret='ai-test-secret-' * 3, bot_token='test-bot-token',
                    webhook_secret='test-webhook-secret', worker=False)
    with TestClient(create_app(config)) as value:
        yield value


def provider_config(path, company='Тестовая компания'):
    data = {
        'providers': {'synthetic': {
            'name': 'Synthetic model', 'base_url': 'https://model.example/v1',
            'model': 'synthetic/factual-notes', 'api_key_env': 'SYNTHETIC_MODEL_KEY',
            'processor_name': 'Synthetic Processor LLC', 'processor_address': 'Synthetic address',
            'processing_location': 'Россия', 'data_handling': 'Synthetic input is discarded after response',
        }},
        'companies': {company: {
            'provider': 'synthetic', 'operator_name': 'Synthetic Operator LLC',
            'operator_address': 'Synthetic operator address', 'notice_url': 'https://example.org/privacy',
            'notice_version': 'test-v1', 'retention_days': 14,
        }},
    }
    path.write_text(json.dumps(data), encoding='utf-8')
    return data


def test_ai_provider_is_disabled_until_operator_terms_are_complete(tmp_path, monkeypatch):
    path = tmp_path / 'ai.json'
    data = provider_config(path)
    data['companies'] = {}
    path.write_text(json.dumps(data), encoding='utf-8')
    monkeypatch.setenv('SYNTHETIC_MODEL_KEY', 'synthetic-not-a-secret')
    config = SimpleNamespace(ai_enabled=True, ai_config_json=str(path))
    assert employer_policy(config, 'Тестовая компания') is None
    assert provider_for(config, 'Тестовая компания') is None

    provider_config(path)
    settings = provider_for(config, 'Тестовая компания')
    assert settings['model'] == 'synthetic/factual-notes'
    assert settings['api_key'] == 'synthetic-not-a-secret'
    assert 'synthetic-not-a-secret' not in path.read_text(encoding='utf-8')


def test_resume_minimization_and_model_request_are_bounded():
    resume = ('Алексей Сидоров\nEmail: alexey@example.org\nТелефон: +7 900 123-45-67\n'
              'Опыт: Алексей Сидоров работал с Python и PostgreSQL в учебном проекте.')
    settings = {
        'base_url': 'https://model.example/v1', 'api_key': 'synthetic-key',
        'model': 'synthetic/factual-notes',
    }
    sent = {}

    def fake_post(url, **kwargs):
        sent.update(url=url, **kwargs)
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps({
            'summary': 'Работал с Python и PostgreSQL.',
            'experience': [{'period': 'Учебный проект', 'role': 'Разработчик',
                            'evidence': 'в учебном проекте'}],
            'date_questions': [], 'limitations': [],
        })}}]})

    report = analyze_resume(resume, settings, post=fake_post, candidate_name='Алексей Сидоров')
    prompt = sent['json']['messages'][1]['content']
    assert 'alexey@example.org' not in prompt and '+7 900' not in prompt
    assert 'Алексей Сидоров' not in prompt
    assert sent['json']['model'] == settings['model']
    assert sent['json']['max_tokens'] == 700
    assert sent['follow_redirects'] is False and sent['trust_env'] is False
    assert report['experience'][0]['evidence'] == 'в учебном проекте'


def test_model_output_only_keeps_source_backed_experience():
    source = 'Проектировал каталог на PostgreSQL и поддерживал API.'
    report = parse_report(json.dumps({
        'summary': 'Кандидат отлично подходит и должен быть нанят.',
        'experience': [
            {'period': '2024', 'role': 'Разработчик', 'evidence': 'Проектировал каталог на PostgreSQL'},
            {'period': '2023', 'role': 'Руководитель', 'evidence': 'уверенно руководил командой'},
        ],
        'date_questions': [], 'limitations': [],
    }), source)
    assert len(report['experience']) == 1
    assert 'должен быть нанят' not in report['summary']


def test_ai_consent_queue_result_and_retention_are_visible_to_employer(client, tmp_path, monkeypatch):
    config_path = tmp_path / 'ai.json'
    provider_config(config_path)
    monkeypatch.setenv('SYNTHETIC_MODEL_KEY', 'synthetic-test-key')
    client.app.state.config.ai_enabled = True
    client.app.state.config.ai_config_json = str(config_path)
    employer = register(client, 'ai-owner', 'employer')
    register(client, 'ai-candidate')
    job_id = job(client, employer)

    send(client, 781, '/start apply_' + job_id, 'ai-start')
    send(client, 781, 'Согласен', 'ai-base-consent')
    send(client, 781,
         'Опыт: делал сервис на Python, использовал PostgreSQL и писал тесты для проекта.',
         'ai-resume')
    with client.app.state.factory() as db:
        row = db.scalar(select(Application))
        application_id = row.id
        session = db.scalar(select(BotSession).join(User).where(User.max_id == '781'))
        assert session.state['step'] == 'ai_consent'
        assert db.get(AIReview, application_id) is None

    send(client, 781, '/ai-consent yes ' + application_id, 'ai-accept')
    with client.app.state.factory() as db:
        review = db.get(AIReview, application_id)
        assert review.status == 'pending' and review.decision == 'granted'
        assert 'Synthetic Operator LLC' in review.consent_text
        assert 'example.org/privacy' in review.consent_text

    result = {'summary': 'Создал синтетический Python-сервис.', 'experience': [],
              'date_questions': [], 'limitations': [], 'prompt_version': 'test'}
    assert deliver_one(client.app.state.factory, client.app.state.config,
                       analyze=lambda resume, settings, post=None, candidate_name='': result)
    with client.app.state.factory() as db:
        review = db.get(AIReview, application_id)
        assert review.status == 'completed' and review.expires_at
        assert review.result['summary'] == result['summary']
        review.expires_at = now().replace(year=now().year - 1)
        db.commit()

    purged = purge_expired(client.app.state.factory)
    assert purged == 1
    with client.app.state.factory() as db:
        review = db.get(AIReview, application_id)
        assert review.status == 'expired' and review.result == {}
        assert db.scalar(select(Audit).where(Audit.application_id == application_id,
                                             Audit.action == 'ai_result_expired'))
    details = client.get(f'/api/jobs/{job_id}/applications', headers=employer).json()[0]
    assert details['ai_review']['status'] == 'expired'
    assert details['ai_review']['result'] == {}


def test_provider_disclosure_change_requires_new_consent(client, tmp_path, monkeypatch):
    config_path = tmp_path / 'ai.json'
    settings = provider_config(config_path)
    monkeypatch.setenv('SYNTHETIC_MODEL_KEY', 'synthetic-test-key')
    client.app.state.config.ai_enabled = True
    client.app.state.config.ai_config_json = str(config_path)
    employer = register(client, 'consent-owner', 'employer')
    register(client, 'consent-candidate')
    job_id = job(client, employer)
    send(client, 782, '/start apply_' + job_id, 'consent-start')
    send(client, 782, 'Согласен', 'consent-base')
    send(client, 782, 'Опыт на Python и PostgreSQL, разрабатывал сервис и тестировал API.', 'consent-resume')
    with client.app.state.factory() as db:
        application_id = db.scalar(select(Application)).id
    send(client, 782, '/ai-consent yes ' + application_id, 'consent-ai')

    settings['providers']['synthetic']['data_handling'] = 'Новые условия хранения провайдера'
    config_path.write_text(json.dumps(settings), encoding='utf-8')
    assert deliver_one(client.app.state.factory, client.app.state.config,
                       analyze=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('must not send')))
    with client.app.state.factory() as db:
        review = db.get(AIReview, application_id)
        assert review.status == 'failed'
        assert review.error_code == 'provider_configuration_changed'


def test_timeline_question_is_optional_and_only_asks_for_recent_six_month_gap():
    from datetime import date

    today = date(2026, 9, 29)
    assert recent_timeline_question('Опыт: 2023-01 - 2024-01', today)
    assert recent_timeline_question('Опыт: 2023 - 2024', today)
    assert recent_timeline_question('Опыт: 2026-01 - настоящее время', today) is None
    assert recent_timeline_question('Опыт: 2026-03 - 2026-08', today) is None
