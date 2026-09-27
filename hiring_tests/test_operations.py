import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import httpx
import pytest
from sqlalchemy import select, func
from hiring.bot import process_event
from hiring.config import Config
from hiring.db import Application, Outbox, User, connect, now
from hiring.maintenance import backup
from hiring.outbox import deliver_one
from hiring.verify_accounts import provision
from test_product import client, register
from scripts.check_data_api import run
import yaml


def test_remote_checker_executes_actual_manifest_locally(client):
    document = yaml.safe_load((Path(__file__).resolve().parents[1] / 'DATA-API.yaml').read_text(encoding='utf-8'))
    roles = {'public': {}, 'employer': register(client, 'checker-employer', 'employer'),
             'candidate': register(client, 'checker-candidate'), 'other_employer': register(client, 'checker-other', 'employer')}
    run(client, document, roles)


def test_additive_migration_preserves_legacy_rows(tmp_path):
    path = tmp_path / 'old.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE hiring_outbox (id VARCHAR(32) PRIMARY KEY, max_id VARCHAR(40), body JSON, status VARCHAR(20), attempts INTEGER, available_at DATETIME)')
        db.execute("INSERT INTO hiring_outbox VALUES ('old','1','{}','sent',1,'2026-09-22 00:00:00')")
    engine, factory = connect('sqlite:///' + str(path))
    with factory() as db:
        row = db.get(Outbox, 'old')
        assert row.status == 'sent' and row.application_id is None and row.callback_id is None
    engine.dispose()


def test_backup_restore_and_no_overwrite(tmp_path):
    path, destination = tmp_path / 'source.db', tmp_path / 'backup.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE test(value TEXT)')
        db.execute("INSERT INTO test VALUES ('synthetic')")
    backup('sqlite:///' + str(path), destination)
    with sqlite3.connect(destination) as restored:
        assert restored.execute('SELECT value FROM test').fetchone() == ('synthetic',)
    with pytest.raises(FileExistsError):
        backup('sqlite:///' + str(path), destination)


def test_provisioned_accounts_have_no_max_and_credentials_are_private(tmp_path):
    url, file = 'sqlite:///' + str(tmp_path / 'verify.db'), tmp_path / 'secrets.json'
    provision(url, file)
    credentials = json.loads(file.read_text())
    assert set(credentials) == {'employer', 'candidate', 'other_employer'}
    engine, factory = connect(url)
    with factory() as db:
        assert all(not u.max_id and u.password != credentials[next(k for k in credentials if credentials[k]['email'] == u.email)]['password'] for u in db.scalars(select(User)))
    engine.dispose()
    with pytest.raises(ValueError):
        provision(url, file)


def test_two_senders_do_not_send_same_row(client):
    with client.app.state.factory() as db:
        db.add(Outbox(max_id='1', body={'text': 'Synthetic concurrent delivery'}))
        db.commit()
    calls = []
    def post(*args, **kwargs):
        calls.append(1)
        time.sleep(0.03)
        return httpx.Response(200, json={'message': {}})
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(deliver_one, client.app.state.factory, client.app.state.config, post) for _ in range(2)]
        for future in futures:
            future.result()
    assert len(calls) == 1


def test_duplicate_event_concurrency(client):
    event = {'update_type': 'bot_started', 'timestamp': 123, 'user': {'user_id': 111}}
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(process_event, client.app.state.factory, event, client.app.state.config) for _ in range(3)]
        for future in futures:
            future.result()
    with client.app.state.factory() as db:
        assert db.scalar(select(func.count()).select_from(User)) == 1
        assert db.scalar(select(func.count()).select_from(Outbox)) == 1


def test_oversize_webhook_stream_rejected(client):
    response = client.post('/api/max/webhook', headers={'X-Max-Bot-Api-Secret': 'test-webhook-secret'}, content=(b'x' * 70000 for _ in range(2)))
    assert response.status_code == 413


def test_non_ascii_wrong_employer_code_is_rejected_not_server_error(client):
    client.app.state.config.employer_code = 'synthetic-correct-code'
    response = client.post('/api/auth/register', json={'email': 'wrong-code@example.com',
        'password': 'synthetic-password', 'name': 'Тест', 'company': 'Тест', 'role': 'employer', 'code': 'неверный'})
    assert response.status_code == 403


def test_retry_exhaustion_and_network_error(client):
    with client.app.state.factory() as db:
        row = Outbox(max_id='1', body={'text': 'Synthetic retry'}, attempts=5)
        db.add(row)
        db.commit()
        oid = row.id
    def post(*args, **kwargs):
        raise httpx.ConnectTimeout('synthetic timeout')
    deliver_one(client.app.state.factory, client.app.state.config, post)
    with client.app.state.factory() as db:
        assert db.get(Outbox, oid).status == 'failed'


def test_storage_failure_webhook_can_retry_without_echoing_data(client, monkeypatch):
    from sqlalchemy.exc import OperationalError
    import hiring.main as backend
    def fail(*args):
        raise OperationalError('statement', {'private': 'DO_NOT_ECHO_SYNTHETIC'}, Exception('failure'))
    monkeypatch.setattr(backend, 'process_event', fail)
    response = client.post('/api/max/webhook', headers={'X-Max-Bot-Api-Secret': 'test-webhook-secret'},
                           json={'update_type': 'bot_started', 'user': {'user_id': 456}})
    assert response.status_code == 503 and 'DO_NOT_ECHO' not in response.text
