import json
from datetime import timedelta
import httpx
import pytest
from sqlalchemy import select, func
from hiring.bot import process_event
from hiring.db import Application, Audit, BotAction, BotSession, Job, Outbox, User, now
from hiring.outbox import deliver_one
from test_employer_bot import send
from test_product import client, register, job


def button(client, uid, command):
    with client.app.state.factory() as db:
        user = db.scalar(select(User).where(User.max_id == str(uid)))
        row = db.scalar(select(BotAction).where(BotAction.user_id == user.id, BotAction.command == command, BotAction.used == False).order_by(BotAction.expires_at.desc()))
        assert row is not None, command
        return row.id


def click(client, uid, ticket, event_id):
    process_event(client.app.state.factory, {'update_type': 'message_callback',
        'callback': {'callback_id': event_id, 'payload': ticket, 'user': {'user_id': uid}},
        'message': {'recipient': {'chat_type': 'dialog'}}}, client.app.state.config)


def test_end_to_end_buttons_and_repeated_click(client):
    client.app.state.config.employer_code = 'test-invite-code'
    send(client, 101, '/start', 'e0')
    click(client, 101, button(client, 101, '/employer'), 'e1')
    send(client, 101, 'test-invite-code', 'e2')
    send(client, 101, 'Синтетическая компания', 'e3')
    click(client, 101, button(client, 101, '/newjob'), 'e4')
    send(client, 101, 'Junior Python', 'e5')
    send(client, 101, 'Создаём внутренний сервис на Python. Требуется также Docker.', 'e6')
    ticket = button(client, 101, 'Публиковать')
    click(client, 101, ticket, 'e7')
    click(client, 101, ticket, 'e8')
    with client.app.state.factory() as db:
        assert db.scalar(select(func.count()).select_from(Job)) == 1
        jid = db.scalar(select(Job)).id
    send(client, 202, '/start apply_' + jid, 'c1')
    click(client, 202, button(client, 202, 'Согласен'), 'c2')
    send(client, 202, 'Разработал на Python учебный сервис для учёта вымышленных книг.', 'c3')
    send(client, 202, '/cancel', 'c4')
    send(client, 202, '/status', 'c5')
    with client.app.state.factory() as db:
        aid = db.scalar(select(Application)).id
    click(client, 202, button(client, 202, '/application ' + aid), 'c6')
    click(client, 202, button(client, 202, '/continue ' + aid), 'c7')
    send(client, 202, 'Нет опыта с Docker', 'c8')
    click(client, 101, button(client, 101, '/view ' + aid), 'e9')
    click(client, 101, button(client, 101, '/invite ' + aid), 'e10')
    send(client, 101, 'Приглашаем на знакомство завтра в 15:00, напишите нам в MAX.', 'e11')
    ticket = button(client, 202, '/confirm ' + aid)
    click(client, 202, ticket, 'c9')
    click(client, 202, ticket, 'c9')
    click(client, 202, ticket, 'c10')
    with client.app.state.factory() as db:
        assert db.get(Application, aid).status == 'confirmed'
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.application_id == aid, Audit.action == 'confirmed')) == 1


def test_foreign_and_stale_buttons(client):
    owner = register(client, 'owner', 'employer')
    first, second = job(client, owner), job(client, owner)
    send(client, 1, '/start apply_' + first, 'a1')
    ticket = button(client, 1, 'Согласен')
    send(client, 2, '/start', 'b1')
    click(client, 2, ticket, 'b2')
    send(client, 1, '/start apply_' + second, 'a2')
    click(client, 1, ticket, 'a3')
    with client.app.state.factory() as db:
        user = db.scalar(select(User).where(User.max_id == '1'))
        assert db.get(BotSession, user.id).state == {'step': 'consent', 'job_id': second}
        assert db.get(BotAction, ticket).used is False


def test_jobs_pagination_beyond_twenty(client):
    send(client, 3, '/start', 'start')
    with client.app.state.factory() as db:
        user = db.scalar(select(User).where(User.max_id == '3'))
        user.role, user.company = 'employer', 'Тест'
        for i in range(26):
            db.add(Job(owner_id=user.id, title=f'Vacancy {i}', company='Тест', description='Synthetic', requirements=[]))
        db.commit()
    send(client, 3, '/jobs 4', 'page4')
    click(client, 3, button(client, 3, '/jobs 5'), 'page5')
    with client.app.state.factory() as db:
        row = db.scalar(select(Outbox).where(Outbox.callback_id.is_(None)).order_by(Outbox.available_at.desc()))
        assert 'страница 6' in row.body['text']


def test_withdraw_clears_pending_and_sent_copies(client):
    owner, candidate = register(client, 'owner', 'employer'), register(client, 'person')
    jid = job(client, owner)
    aid = client.post(f'/api/jobs/{jid}/apply', headers=candidate, json={'name': 'Synthetic', 'resume': 'Python PostgreSQL: wrote a synthetic catalog and unit tests for it.', 'consent': True}).json()['id']
    with client.app.state.factory() as db:
        for status in ('pending', 'sent', 'failed'):
            db.add(Outbox(max_id='999', body={'text': 'synthetic private copy'}, status=status, application_id=aid))
        db.commit()
    assert client.delete(f'/api/applications/{aid}', headers=candidate).status_code == 204
    sent = []
    deliver_one(client.app.state.factory, client.app.state.config, post=lambda *a, **k: sent.append(k))
    assert not sent
    with client.app.state.factory() as db:
        rows = list(db.scalars(select(Outbox).where(Outbox.application_id == aid)))
        assert all(r.body == {} and r.status in ('sent', 'cancelled') for r in rows)


@pytest.mark.parametrize('status, body, expected', [(200, {'message': {}}, 'sent'), (200, {}, 'pending'), (200, {'success': False}, 'pending'), (429, {}, 'pending'), (401, {}, 'failed'), (503, {}, 'pending')])
def test_delivery_status_and_retry(client, status, body, expected):
    with client.app.state.factory() as db:
        row = Outbox(max_id='77', body={'text': 'Synthetic message'})
        db.add(row)
        db.commit()
        oid = row.id
    deliver_one(client.app.state.factory, client.app.state.config, post=lambda *a, **k: httpx.Response(status, json=body, headers={'Retry-After': '120'}))
    with client.app.state.factory() as db:
        row = db.get(Outbox, oid)
        assert row.status == expected and row.attempts == 1
        assert bool(row.body) == (expected != 'sent')
        if status == 429:
            assert row.available_at > (now() + timedelta(seconds=100)).replace(tzinfo=None)


def test_callback_ack_uses_answers_endpoint(client):
    with client.app.state.factory() as db:
        db.add(Outbox(max_id='1', callback_id='cb1', body={}))
        db.commit()
    captured = []
    def post(url, **kwargs):
        captured.append((url, kwargs['params']))
        return httpx.Response(200, json={'success': True})
    deliver_one(client.app.state.factory, client.app.state.config, post)
    assert captured[0][0].endswith('/answers') and captured[0][1] == {'callback_id': 'cb1'}


def test_employer_code_limit_survives_cancel(client):
    client.app.state.config.employer_code = 'right-code'
    for i in range(5):
        send(client, 5, '/employer wrong', f'bad{i}')
        send(client, 5, '/cancel', f'cancel{i}')
    send(client, 5, '/employer right-code', 'right')
    send(client, 5, 'Компания', 'company')
    with client.app.state.factory() as db:
        assert db.scalar(select(User).where(User.max_id == '5')).role == 'candidate'
