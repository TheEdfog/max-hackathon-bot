"""Offline tests: no provider, MAX, hh.ru or HR network requests."""
import io
import json
import sqlite3
from urllib import error

import httpx
import pytest
from sqlalchemy import select

from examples.hr_sync import sync_page, validate_page
from hiring.db import IntegrationEvent, Job, User, connect
from scripts import free_dev_draft
from test_product import client, register, job
from test_integrations import PREFIX, key


def test_adapter_reads_actual_api_and_replays_idempotently(client):
    owner = register(client, 'adapter', 'employer')
    jid = job(client, owner)
    auth, _ = key(client, owner)
    client.headers.update(auth)
    sink = {}
    def apply(resource, document, seq):
        sink[(resource, document['id'])] = document
    cursor, more = sync_page(client, 0, apply)
    assert cursor > 0 and more is False
    assert sink[('jobs', jid)]['id'] == jid
    assert sync_page(client, 0, apply) == (cursor, False)
    assert len(sink) == 1
    assert sync_page(client, cursor, apply) == (cursor, False)


def test_adapter_failure_never_returns_advanced_cursor():
    jid = 'a' * 32
    calls = []
    def transport(request):
        if request.url.path.endswith('/events'):
            return httpx.Response(200, json={'items': [
                {'seq': 2, 'type': 'job.created', 'resource_id': jid},
                {'seq': 4, 'type': 'job.updated', 'resource_id': jid}],
                'next_cursor': '4', 'has_more': False})
        return httpx.Response(404)
    def fail_second(resource, document, seq):
        calls.append((resource, document, seq))
        if seq == 4:
            raise RuntimeError('Synthetic HR outage')
    with httpx.Client(base_url='https://synthetic.invalid', transport=httpx.MockTransport(transport)) as api:
        with pytest.raises(RuntimeError):
            sync_page(api, 0, fail_second)
    assert len(calls) == 2 and calls[0][1] == {'id': jid, 'deleted': True}


@pytest.mark.parametrize('page,after', [
    ({'items': [], 'next_cursor': '0', 'has_more': True}, 0),
    ({'items': [], 'next_cursor': '01', 'has_more': False}, 1),
    ({'items': [{'seq': True}], 'next_cursor': '1', 'has_more': False}, 0),
    ({'items': [{'seq': 2}, {'seq': 2}], 'next_cursor': '2', 'has_more': False}, 0),
    ({'items': [], 'next_cursor': '2', 'has_more': False}, 1),
    ({'items': [{'seq': 2**63}], 'next_cursor': str(2**63), 'has_more': False}, 0),
    ({'items': [], 'next_cursor': '0', 'has_more': False}, False),
])
def test_adapter_rejects_invalid_pages(page, after):
    with pytest.raises(ValueError):
        validate_page(page, after)


def test_existing_jobs_get_empty_screening_without_losing_data(tmp_path):
    path = tmp_path / 'legacy.db'
    engine, factory = connect('sqlite:///' + str(path))
    with factory() as db:
        owner = User(name='Synthetic employer', role='employer')
        db.add(owner)
        db.flush()
        row = Job(owner_id=owner.id, title='Legacy job', company='Synthetic',
                  description='Preserve this description', requirements=[])
        db.add(row)
        db.commit()
        jid = row.id
    engine.dispose()
    with sqlite3.connect(path) as db:
        db.execute('ALTER TABLE hiring_jobs DROP COLUMN screening_questions')
        db.execute('DROP TABLE hiring_integration_events')
    engine, factory = connect('sqlite:///' + str(path))
    with factory() as db:
        row = db.get(Job, jid)
        assert row.title == 'Legacy job' and row.description == 'Preserve this description'
        assert row.screening_questions == []
        assert list(db.scalars(select(IntegrationEvent))) == []
    engine.dispose()


@pytest.mark.parametrize('value', ['', 'x' * 4001, 'contact: synthetic@example.invalid',
                                  'api_key=synthetic', 'Bearer synthetic', 'x' * 48])
def test_free_helper_refuses_obvious_sensitive_or_unbounded_input(value):
    with pytest.raises(ValueError):
        free_dev_draft.validate(value)


def test_free_helper_bounded_request_and_untrusted_result(monkeypatch):
    class Reply(io.BytesIO):
        pass
    captured = []
    class Opener:
        def open(self, request, timeout):
            captured.append((request, timeout))
            return Reply(json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': 'Draft only'}}],
                                     'usage': {'total_tokens': 12, 'private_field': 'do not echo'}}).encode())
    monkeypatch.setattr(free_dev_draft.request, 'build_opener', lambda *args: Opener())
    result = free_dev_draft.draft('Synthetic task: validate an increasing integer cursor.', 'kilo')
    req, timeout = captured[0]
    assert timeout == 25 and 'Authorization' not in req.headers
    assert json.loads(req.data)['max_tokens'] == 800
    assert result['untrusted_draft'] == 'Draft only' and result['provider_usage'] == {'total_tokens': 12}
    with pytest.raises(ValueError):
        free_dev_draft.draft('Synthetic task', 'paid-provider')
    with pytest.raises(ValueError):
        free_dev_draft.draft('Synthetic task', 'ovh', budget=801)


def test_free_helper_explicit_one_fallback(monkeypatch, capsys):
    class Input(io.StringIO):
        def reconfigure(self, **kwargs):
            pass
    class Output(io.StringIO):
        def reconfigure(self, **kwargs):
            pass
    attempted = []
    def fail(text, provider):
        attempted.append(provider)
        raise error.URLError('Synthetic outage')
    monkeypatch.setattr(free_dev_draft.sys, 'stdin', Input('Synthetic cursor helper'))
    monkeypatch.setattr(free_dev_draft.sys, 'stdout', Output())
    monkeypatch.setattr(free_dev_draft.sys, 'argv', ['helper', '--data-class', 'synthetic', '--send', '--fallback'])
    monkeypatch.setattr(free_dev_draft, 'draft', fail)
    assert free_dev_draft.main() == 2
    assert attempted == ['ovh', 'kilo']
