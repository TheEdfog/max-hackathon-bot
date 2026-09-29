"""Release regressions: synthetic records only, no live MAX traffic."""
from concurrent.futures import ThreadPoolExecutor
import io
import subprocess
from threading import Barrier
import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from sqlalchemy import func, select
from hiring.db import Application, BotSession, Job, Outbox, User
from hiring.pdf_extract import extract_pdf
from hiring.polling import polling_lock
from test_buttons_delivery import button, click
from test_employer_bot import send
from test_product import client, job, register


def latest(client, uid):
    with client.app.state.factory() as db:
        return db.scalar(select(Outbox).where(Outbox.max_id == str(uid), Outbox.callback_id.is_(None)).order_by(Outbox.available_at.desc())).body['text']


def test_non_ascii_upgrade_code_and_max_hash_are_client_errors(client):
    candidate = register(client, 'unicode')
    client.app.state.config.employer_code = 'synthetic-code'
    assert client.post('/api/me/employer', headers=candidate, json={'company': 'Тест', 'code': 'неверно'}).status_code == 403
    assert client.post('/api/auth/max', json={'init_data': 'hash=кириллица&auth_date=0&user=%7B%7D'}).status_code == 401


def test_whitespace_resume_and_name_are_rejected(client):
    owner, candidate = register(client, 'space-owner', 'employer'), register(client, 'space-candidate')
    jid = job(client, owner)
    for name, resume in [('   ', 'Synthetic Python experience in a catalogue project.'), ('Тест', ' ' * 50)]:
        assert client.post(f'/api/jobs/{jid}/apply', headers=candidate, json={'name': name, 'resume': resume, 'consent': True}).status_code == 422


@pytest.mark.parametrize('action', ['answers', 'invite', 'confirm'])
def test_api_race_cannot_resurrect_withdrawn_data(client, action):
    owner, candidate = register(client, 'race-owner', 'employer'), register(client, 'race-person')
    jid = job(client, owner)
    raw = 'Python: built a synthetic library catalogue and wrote its tests.'
    row = client.post(f'/api/jobs/{jid}/apply', headers=candidate, json={'name': 'Synthetic', 'resume': raw, 'consent': True}).json()
    aid = row['id']
    if action != 'answers':
        client.post(f'/api/applications/{aid}/answers', headers=candidate, json={'answers': {'pg': 'PostgreSQL: built schema and joins.'}}).raise_for_status()
    if action == 'confirm':
        client.post(f'/api/applications/{aid}/invite', headers=owner, json={'message': 'Synthetic interview tomorrow at noon.'}).raise_for_status()
    gate = Barrier(2)
    def run(withdraw):
        gate.wait(timeout=10)
        if withdraw:
            return client.delete(f'/api/applications/{aid}', headers=candidate)
        kwargs = {'headers': owner if action == 'invite' else candidate}
        if action == 'answers':
            kwargs['json'] = {'answers': {'pg': 'PostgreSQL synthetic private details.'}}
        elif action == 'invite':
            kwargs['json'] = {'message': 'Synthetic private interview invitation.'}
        return client.post(f'/api/applications/{aid}/{action}', **kwargs)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, [False, True]))
    assert results[1].status_code == 204
    assert results[0].status_code in (200, 409)
    with client.app.state.factory() as db:
        row = db.get(Application, aid)
        assert row.status == 'withdrawn' and row.resume == '' and row.answers == {} and row.invitation == ''


def test_answer_buttons_bound_to_question_and_skip_is_not_negative(client):
    owner = register(client, 'question-owner', 'employer')
    jid = job(client, owner)
    send(client, 801, '/start apply_' + jid, 'q0')
    send(client, 801, 'Да', 'q1')
    send(client, 801, 'Разработал учебный каталог книг на Java и написал тесты.', 'q2')
    assert 'Уточнение 1 из 2' in latest(client, 801)
    old = button(client, 801, 'Опыта нет.')
    click(client, 801, button(client, 801, 'Пропускаю уточнение, сведений недостаточно.'), 'q3')
    assert 'Уточнение 2 из 2' in latest(client, 801)
    click(client, 801, old, 'q4')
    assert 'устарела' in latest(client, 801)
    send(client, 801, '/status', 'q5')
    with client.app.state.factory() as db:
        row = db.scalar(select(Application))
        aid = row.id
        assert len(row.answers) == 1
    send(client, 801, '/continue ' + aid, 'q6')
    click(client, 801, old, 'q7')
    assert 'устарела' in latest(client, 801)
    click(client, 801, button(client, 801, 'Опыта нет.'), 'q8')
    with client.app.state.factory() as db:
        from hiring.services import application_view
        view = application_view(db, db.get(Application, aid))
        assert view['status'] == 'ready'
        assert [r['state'] for r in view['assessment']['requirements']] == ['review', 'review']


def test_unknown_command_does_not_become_vacancy_text(client):
    send(client, 802, '/start', 'u0')
    with client.app.state.factory() as db:
        user = db.scalar(select(User).where(User.max_id == '802'))
        user.role, user.company = 'employer', 'Synthetic'
        db.commit()
    send(client, 802, '/newjob', 'u1')
    send(client, 802, '/typo', 'u2')
    with client.app.state.factory() as db:
        assert db.get(BotSession, user.id).state == {'step': 'job_title'}
    assert 'не распознана' in latest(client, 802)


def test_metrics_are_company_scoped(client):
    owner, other = register(client, 'metrics-owner', 'employer'), register(client, 'metrics-other', 'employer')
    jid = job(client, owner)
    job(client, other)
    person = register(client, 'metrics-person')
    client.post(f'/api/jobs/{jid}/apply', headers=person, json={'name': 'Synthetic', 'resume': 'Python PostgreSQL: built a synthetic catalogue and test suite.', 'consent': True}).raise_for_status()
    with client.app.state.factory() as db:
        user = db.scalar(select(User).where(User.email == 'metrics-owner@example.com'))
        user.max_id = '803'
        db.commit()
    send(client, 803, '/metrics', 'm0')
    assert 'Открытых вакансий: 1' in latest(client, 803)
    assert 'Ждут вашего решения: 1' in latest(client, 803)


def test_polling_lock_is_exclusive_and_released(tmp_path):
    url = 'sqlite:///' + str(tmp_path / 'lock.db')
    with polling_lock(url):
        with pytest.raises(RuntimeError, match='already running'):
            with polling_lock(url):
                pytest.fail('Acquired second lock')
    with polling_lock(url):
        pass


def synthetic_pdf():
    writer = PdfWriter()
    page = writer.add_blank_page(600, 800)
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
    stream = DecodedStreamObject()
    stream.set_data(b'BT /F1 12 Tf 40 700 Td (Synthetic Python developer built a library catalogue and wrote tests.) Tj ET')
    page[NameObject('/Contents')] = writer._add_object(stream)
    result = io.BytesIO()
    writer.write(result)
    return result.getvalue()


def test_real_pdf_worker_and_api(client):
    raw = synthetic_pdf()
    assert 'Synthetic Python' in extract_pdf(raw)
    headers = register(client, 'pdf-person')
    response = client.post('/api/resume/extract', headers=headers, files={'file': ('synthetic.pdf', raw, 'application/pdf')})
    assert response.status_code == 200 and 'Synthetic Python' in response.json()['text']


def test_pdf_timeout_and_no_inherited_secrets(monkeypatch):
    captured = []
    def timeout(*args, **kwargs):
        captured.append(kwargs['env'])
        raise subprocess.TimeoutExpired('synthetic', 8)
    monkeypatch.setenv('MAX_BOT_TOKEN', 'synthetic-do-not-inherit')
    monkeypatch.setattr('hiring.pdf_extract.subprocess.run', timeout)
    with pytest.raises(ValueError, match='failed'):
        extract_pdf(b'%PDF-synthetic')
    assert 'MAX_BOT_TOKEN' not in captured[0]


def test_many_page_pdf_rejected():
    writer = PdfWriter()
    for _ in range(11):
        writer.add_blank_page(600, 800)
    result = io.BytesIO()
    writer.write(result)
    with pytest.raises(ValueError):
        extract_pdf(result.getvalue())


@pytest.mark.parametrize('choice', ['none', 'skip'])
def test_one_account_demo_isolated_and_restores_draft(client, choice):
    send(client, 804, '/start', 'd0')
    with client.app.state.factory() as db:
        user = db.scalar(select(User).where(User.max_id == '804'))
        user.role, user.company = 'employer', 'Synthetic'
        db.get(BotSession, user.id).state = {'step': 'job_title'}
        db.commit()
    send(client, 804, '/demo', 'd1')
    for index, command in enumerate(['/demo_next 1', '/demo_next 2', '/demo_next 3', '/demo_answer ' + choice,
                                     '/demo_next 5', '/demo_next 6', '/demo_next 7']):
        ticket = button(client, 804, command)
        click(client, 804, ticket, 'demo-' + str(index))
    assert 'Учебный результат' in latest(client, 804)
    with client.app.state.factory() as db:
        assert db.scalar(select(func.count()).select_from(Job)) == 0
        assert db.scalar(select(func.count()).select_from(Application)) == 0
        assert db.get(User, user.id).role == 'employer'
        assert set(db.scalars(select(Outbox.max_id))) == {'804'}
    click(client, 804, button(client, 804, '/demo_exit'), 'd-exit')
    with client.app.state.factory() as db:
        assert db.get(BotSession, user.id).state == {'step': 'job_title'}


def test_demo_cannot_skip_steps_or_capture_real_resume(client):
    send(client, 805, '/demo', 's0')
    send(client, 805, '/demo_next 7', 's1')
    send(client, 805, 'Synthetic text that must not become an application or be stored.', 's2')
    with client.app.state.factory() as db:
        assert db.scalar(select(Application)) is None
    send(client, 805, '/start', 's3')
    with client.app.state.factory() as db:
        user = db.scalar(select(User).where(User.max_id == '805'))
        assert db.get(BotSession, user.id).state == {}


def test_doctor_redacts_secrets_and_does_not_create_records(client):
    from hiring.doctor import inspect_local
    import json
    config = client.app.state.config
    config.employer_code = 'synthetic-private-code'
    result = json.dumps(inspect_local(config))
    assert config.bot_token not in result and config.secret not in result and config.employer_code not in result
    with client.app.state.factory() as db:
        assert db.scalar(select(User)) is None
