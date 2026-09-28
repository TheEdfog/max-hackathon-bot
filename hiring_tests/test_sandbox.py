"""Isolated test personas; all IDs and texts below are synthetic.

Draft-recovery and role-labelled-notification cases were informed by a reviewed
anonymous Qwen draft (334 provider tokens); assertions and implementation verified locally.
"""
from types import SimpleNamespace
import httpx
import pytest
from sqlalchemy import func, select
from hiring.bot import process_event
from hiring.config import Config
from hiring.db import Application, BotAction, BotSession, Job, Outbox, SandboxSwitch, User, connect
from hiring.main import create_app
from hiring.outbox import deliver_one
from hiring.polling import PollCursor, open_cursor_store, transport_url
from hiring.runtime_lock import polling_lock
from hiring.sandbox import check_storage
from test_employer_bot import send
from test_buttons_delivery import click


def test_transport_marker_and_lock_shared_between_modes(tmp_path):
    normal_url = 'sqlite:///' + str(tmp_path / 'hiring.db')
    sandbox_url = 'sqlite:///' + str(tmp_path / 'hiring.sandbox.db')
    shared = transport_url(normal_url)
    assert shared == transport_url(sandbox_url)
    with polling_lock(shared), pytest.raises(RuntimeError, match='already running'):
        with polling_lock(transport_url(sandbox_url)):
            pass
    engines = []
    try:
        normal_engine, normal = connect(normal_url)
        sandbox_engine, isolated = connect(sandbox_url)
        engines.extend([normal_engine, sandbox_engine])
        for factory, marker in ((normal, 'old-normal-marker'), (isolated, 'latest-sandbox-marker')):
            with factory() as db:
                db.add(PollCursor(id='42', marker=marker))
                db.commit()
        engine, transport = open_cursor_store(shared, isolated, '42')
        engines.append(engine)
        with transport() as db:
            assert db.get(PollCursor, '42').marker == 'latest-sandbox-marker'
            db.get(PollCursor, '42').marker = 'after-test-traffic'
            db.commit()
        # A normal-mode restart must not replay the sandbox's traffic.
        engine, transport = open_cursor_store(shared, normal, '42')
        engines.append(engine)
        with transport() as db:
            assert db.get(PollCursor, '42').marker == 'after-test-traffic'
        with normal() as db:
            assert db.get(PollCursor, '42').marker == 'old-normal-marker'
            assert db.scalar(select(func.count()).select_from(User)) == 0
        from sqlalchemy import inspect
        assert inspect(engine).get_table_names() == ['hiring_poll_cursor']
    finally:
        for engine in engines:
            engine.dispose()


@pytest.fixture
def sandbox(tmp_path):
    config = Config(database_url='sqlite:///' + str(tmp_path / 'test.sandbox.db'),
                    secret='synthetic-secret-12345678901234567890', sandbox=True,
                    sandbox_users=('11', '22'), worker=False, bot_token='test-bot-token', bot_name='test_bot')
    config.validate()
    engine, factory = connect(config.database_url)
    check_storage(factory)
    yield SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(factory=factory, config=config)))
    engine.dispose()


def user(db, uid, persona):
    return db.scalar(select(User).where(User.max_id == f'sandbox:{uid}:{persona}'))


def state(sandbox, uid, persona):
    with sandbox.app.state.factory() as db:
        actor = user(db, uid, persona)
        return dict(db.get(BotSession, actor.id).state) if actor else {}


def button(sandbox, uid, persona, command):
    with sandbox.app.state.factory() as db:
        actor = user(db, uid, persona)
        row = db.scalar(select(BotAction).where(BotAction.user_id == actor.id, BotAction.command == command,
                                               BotAction.used == False).order_by(BotAction.expires_at.desc()))
        assert row is not None, command
        return row.id


def latest(sandbox, uid):
    with sandbox.app.state.factory() as db:
        row = db.scalar(select(Outbox).where(Outbox.max_id == str(uid), Outbox.callback_id.is_(None)).order_by(Outbox.available_at.desc()))
        return row.body.get('text', '')


def test_full_real_domain_cycle_three_personas_one_physical_account(sandbox):
    send(sandbox, 11, '/test', '0')
    send(sandbox, 11, '/newjob', '1')
    send(sandbox, 11, 'Synthetic Python role', '2')
    send(sandbox, 11, 'Build a synthetic Python catalogue. PostgreSQL is required.', '3')
    send(sandbox, 11, 'Python\nPostgreSQL', '4')
    click(sandbox, 11, button(sandbox, 11, 'e', 'Публиковать'), '5')
    with sandbox.app.state.factory() as db:
        jid = db.scalar(select(Job)).id
    send(sandbox, 11, '/test c', '6')
    send(sandbox, 11, '/start apply_' + jid, '7')
    click(sandbox, 11, button(sandbox, 11, 'c', 'Согласен'), '8')
    send(sandbox, 11, 'Python: I built a synthetic library catalogue with unit tests.', '9')
    click(sandbox, 11, button(sandbox, 11, 'c', 'Опыта нет.'), '10')
    with sandbox.app.state.factory() as db:
        row = db.scalar(select(Application))
        aid = row.id
        assert row.status == 'ready'
        assert row.user_id != db.get(Job, jid).owner_id
        assert db.get(Job, jid).company == 'Тестовая компания А'
    send(sandbox, 11, '/test x', '11')
    send(sandbox, 11, '/view ' + aid, '12')
    assert 'Отклик не найден' in latest(sandbox, 11)
    send(sandbox, 11, '/test e', '13')
    send(sandbox, 11, '/view ' + aid, '14')
    click(sandbox, 11, button(sandbox, 11, 'e', '/invite ' + aid), '15')
    send(sandbox, 11, 'Synthetic interview tomorrow at noon. This is only a test.', '16')
    candidate_ticket = button(sandbox, 11, 'c', '/confirm ' + aid)
    with sandbox.app.state.factory() as db:
        pending = list(db.scalars(select(Outbox).where(Outbox.callback_id.is_(None))))
        assert all(r.max_id == '11' and r.body['text'].startswith('[ТЕСТ · ') for r in pending)
        assert any(r.body['text'].startswith('[ТЕСТ · Кандидат]') and 'приглашают' in r.body['text'] for r in pending)
    click(sandbox, 11, candidate_ticket, '17')
    assert 'устарела' in latest(sandbox, 11)
    with sandbox.app.state.factory() as db:
        assert db.get(Application, aid).status == 'invited'
    send(sandbox, 11, '/test c', '18')
    click(sandbox, 11, candidate_ticket, '19')
    with sandbox.app.state.factory() as db:
        assert db.get(Application, aid).status == 'confirmed'
    send(sandbox, 11, '/withdraw ' + aid, '20')
    click(sandbox, 11, button(sandbox, 11, 'c', 'Отозвать'), '21')
    with sandbox.app.state.factory() as db:
        assert db.get(Application, aid).resume == ''
    send(sandbox, 11, '/test e', '22')
    send(sandbox, 11, '/view ' + aid, '23')
    assert 'Отклик не найден' in latest(sandbox, 11)


def test_switch_recovers_each_personas_draft_and_survives_restart(sandbox):
    send(sandbox, 11, '/test', 'd0')
    send(sandbox, 11, '/newjob', 'd1')
    send(sandbox, 11, 'Synthetic draft', 'd2')
    expected = state(sandbox, 11, 'e')
    send(sandbox, 11, '/test c', 'd3')
    send(sandbox, 11, '/help', 'd4')
    assert state(sandbox, 11, 'c') == {}
    engine, factory = connect(sandbox.app.state.config.database_url)
    restarted = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(factory=factory, config=sandbox.app.state.config)))
    try:
        send(restarted, 11, '/test e', 'd5')
        assert state(restarted, 11, 'e') == expected
        send(restarted, 11, 'Synthetic description of Python projects and requirements.', 'd6')
        assert state(restarted, 11, 'e')['step'] == 'job_review'
    finally:
        engine.dispose()


def test_old_role_button_does_not_change_candidate_or_employer_draft(sandbox):
    send(sandbox, 11, '/test', 'b0')
    send(sandbox, 11, '/help', 'b1')
    ticket = button(sandbox, 11, 'e', '/newjob')
    send(sandbox, 11, '/test c', 'b2')
    click(sandbox, 11, ticket, 'b3')
    assert 'устарела' in latest(sandbox, 11)
    assert state(sandbox, 11, 'e') == state(sandbox, 11, 'c') == {}
    with sandbox.app.state.factory() as db:
        assert not db.get(BotAction, ticket).used


def test_other_physical_tester_cannot_use_actors_buttons(sandbox):
    for uid in (11, 22):
        send(sandbox, uid, '/test', 'a0')
        send(sandbox, uid, '/help', 'a1')
    ticket = button(sandbox, 11, 'e', '/newjob')
    click(sandbox, 22, ticket, 'a2')
    assert 'устарела' in latest(sandbox, 22)
    assert state(sandbox, 11, 'e') == state(sandbox, 22, 'e') == {}


def test_allowlist_and_explicit_activation_no_replay(sandbox):
    send(sandbox, 999, '/test', 'r0')
    send(sandbox, 11, '/newjob', 'r1')
    send(sandbox, 11, 'Old text must not become test data.', 'r2')
    with sandbox.app.state.factory() as db:
        assert db.scalar(select(User)) is None
        assert db.scalar(select(SandboxSwitch)) is None
        assert db.scalar(select(Outbox)) is None


@pytest.mark.parametrize('change', [dict(production=True), dict(database_url='sqlite:///data/hiring.db'), dict(sandbox_users=()), dict(sandbox_users=('not-an-id',))])
def test_sandbox_unsafe_config_rejected(sandbox, change):
    config = sandbox.app.state.config
    for key, value in change.items():
        setattr(config, key, value)
    with pytest.raises(ValueError):
        config.validate()


def test_public_api_rejects_sandbox(sandbox):
    with pytest.raises(ValueError, match='local polling'):
        create_app(sandbox.app.state.config)


def test_storage_refuses_real_account_rows(sandbox):
    with sandbox.app.state.factory() as db:
        db.add(User(name='Synthetic normal account', max_id='11'))
        db.commit()
    with pytest.raises(ValueError, match='non-sandbox'):
        check_storage(sandbox.app.state.factory)


def test_sender_only_uses_physical_max_id(sandbox):
    send(sandbox, 11, '/test', 's0')
    captured = []
    def post(url, **kwargs):
        captured.append(kwargs)
        return httpx.Response(200, json={'message': {}})
    deliver_one(sandbox.app.state.factory, sandbox.app.state.config, post)
    assert captured[0]['params']['user_id'] == '11'
    assert captured[0]['json']['text'].startswith('[ТЕСТ · Работодатель]')


def test_candidate_cannot_convert_persona_role(sandbox):
    send(sandbox, 11, '/test', 'p0')
    send(sandbox, 11, '/test c', 'p1')
    send(sandbox, 11, '/employer private-test-code', 'p2')
    with sandbox.app.state.factory() as db:
        assert user(db, 11, 'c').role == 'candidate'


def test_normal_mode_has_no_persona_impersonation(sandbox):
    sandbox.app.state.config.sandbox = False
    send(sandbox, 11, '/test e', 'n0')
    with sandbox.app.state.factory() as db:
        assert db.scalar(select(SandboxSwitch)) is None
        assert db.scalar(select(User)).role == 'candidate'


def test_whoami_uses_authenticated_event_identity_not_text(sandbox):
    send(sandbox, 11, '/whoami', 'w0')
    assert latest(sandbox, 11).endswith('11')
    with sandbox.app.state.factory() as db:
        assert db.scalar(select(User)) is None


@pytest.mark.parametrize('description', [
    'Обязательны Python и PostgreSQL. Docker будет плюсом.',
    'Будет плюсом Docker. Python требуется для внутренних сервисов.',
    'Требования: Python. Docker будет преимуществом.',
    'Python is required. Docker is optional.',
])
def test_optional_modifier_after_skill_does_not_inherit_previous_requirement(description):
    from hiring.matching import extract
    requirements = {r['skill']: r['type'] for r in extract(description)}
    assert requirements['docker'] == 'nice'
    assert requirements['python'] == 'must'
