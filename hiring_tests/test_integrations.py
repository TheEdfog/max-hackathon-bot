from datetime import timedelta
import json
import pytest
from sqlalchemy import func, select
from hiring.db import Application, IntegrationEvent, IntegrationKey, Job, now
from test_product import client, register, job
from test_employer_bot import send
from test_buttons_delivery import click, button

PREFIX = '/api/integrations/v1'


def key(client, owner, scopes=None):
    result = client.post('/api/integration-keys', headers=owner, json={
        'name': 'Synthetic ATS', **({'scopes': scopes} if scopes else {})})
    assert result.status_code == 201, result.text
    value = result.json()
    return {'Authorization': 'Bearer ' + value['token']}, value['id']


def vacancy():
    return {'title': 'Synthetic Data Engineer', 'description': 'Build a synthetic Python catalogue, with PostgreSQL storage.',
            'requirements': [{'id': 'py', 'skill': 'python', 'label': 'Python'},
                             {'id': 'pg', 'skill': 'postgresql', 'label': 'PostgreSQL'}],
            'screening_questions': ['screen_conditions', 'screen_availability']}


def test_upsert_scope_rotation_revocation_and_pagination(client):
    owner = register(client, 'owner', 'employer')
    reader, _ = key(client, owner)
    writer, kid = key(client, owner, ['jobs:read', 'jobs:write', 'events:read'])
    path = PREFIX + '/jobs/by-external/ats/REQ-42'
    assert client.put(path, headers=reader, json=vacancy()).status_code == 403
    first = client.put(path, headers=writer, json=vacancy())
    assert first.status_code == 200, first.text
    jid = first.json()['id']
    assert client.put(path, headers=writer, json=vacancy()).json()['id'] == jid
    with client.app.state.factory() as db:
        assert db.scalar(select(func.count()).select_from(Job)) == 1
        assert db.scalar(select(func.count()).select_from(IntegrationEvent)) == 1
        stored = db.get(IntegrationKey, kid)
        assert writer['Authorization'][7:] not in stored.digest
    second = client.put(PREFIX + '/jobs/by-external/ats/REQ-43', headers=writer, json=vacancy()).json()
    page1 = client.get(PREFIX + '/jobs?limit=1', headers=reader).json()
    page2 = client.get(PREFIX + '/jobs', params={'limit': 1, 'after': page1['next_cursor']}, headers=reader).json()
    assert {page1['items'][0]['id'], page2['items'][0]['id']} == {jid, second['id']}
    assert page2['next_cursor'] is None
    rotated, _ = key(client, owner, ['jobs:write', 'jobs:read'])
    assert client.put(path, headers=rotated, json=vacancy()).json()['id'] == jid
    listed = client.get('/api/integration-keys', headers=owner).json()
    assert all('token' not in item and 'digest' not in item for item in listed)
    assert client.delete('/api/integration-keys/' + kid, headers=owner).status_code == 204
    assert client.get(PREFIX + '/jobs', headers=writer).status_code == 401
    assert client.get('/api/me', headers=rotated).status_code == 401


def test_company_isolation_pii_and_withdrawal_tombstone(client):
    owner, other = register(client, 'owner', 'employer'), register(client, 'other', 'employer')
    candidate = register(client, 'candidate')
    reader, _ = key(client, owner)
    full, _ = key(client, owner, ['applications:read', 'applications:pii', 'events:read', 'invitations:write', 'jobs:write'])
    outsider, _ = key(client, other, ['jobs:read', 'applications:read', 'applications:pii', 'invitations:write', 'events:read'])
    jid = client.put(PREFIX + '/jobs/by-external/ats/42', headers=full, json=vacancy()).json()['id']
    resume = 'Python: I built a synthetic catalogue with tests and documentation.'
    result = client.post('/api/jobs/' + jid + '/apply', headers=candidate, json={'name': 'Private synthetic name', 'resume': resume, 'consent': True}).json()
    aid = result['id']
    assert len(result['questions']) == 3
    assert client.get(PREFIX + '/jobs/' + jid, headers=outsider).status_code == 404
    assert client.get(PREFIX + '/applications/' + aid, headers=outsider).status_code == 404
    assert client.get(PREFIX + '/events', headers=outsider).json()['items'] == []
    summary = client.get(PREFIX + '/applications/' + aid, headers=reader).json()
    assert 'name' not in summary and 'resume' not in summary and 'answers' not in summary
    detail = client.get(PREFIX + '/applications/' + aid, headers=full).json()
    assert detail['resume'] == resume and detail['name'] == 'Private synthetic name'
    answers = {q['id']: 'Пропускаю уточнение, сведений недостаточно.' for q in result['questions']}
    assert client.post('/api/applications/' + aid + '/answers', headers=candidate, json={'answers': answers}).json()['status'] == 'ready'
    changed = {**vacancy(), 'title': 'Changed after application'}
    assert client.put(PREFIX + '/jobs/by-external/ats/42', headers=full, json=changed).status_code == 409
    assert client.patch(PREFIX + '/jobs/' + jid, headers=full, json={'active': False}).status_code == 200
    assert client.post(PREFIX + '/applications/' + aid + '/invite', headers=outsider, json={'message': 'Synthetic invitation'}).status_code == 404
    assert client.post(PREFIX + '/applications/' + aid + '/invite', headers=full, json={'message': 'Synthetic invitation'}).json()['status'] == 'invited'
    client.post('/api/applications/' + aid + '/confirm', headers=candidate).raise_for_status()
    client.delete('/api/applications/' + aid, headers=candidate).raise_for_status()
    tombstone = client.get(PREFIX + '/applications/' + aid, headers=full).json()
    assert tombstone == {'id': aid, 'job_id': jid, 'status': 'withdrawn', 'deleted': True}
    feed = client.get(PREFIX + '/events', headers=full).json()
    assert feed['items'][-1]['type'] == 'application.withdrawn'
    assert resume not in json.dumps(feed) and 'Private synthetic name' not in json.dumps(feed)
    assert client.get(PREFIX + '/events', params={'after': feed['next_cursor']}, headers=full).json()['items'] == []
    assert client.get(PREFIX + '/applications', headers=reader).json()['items'] == [tombstone]


@pytest.mark.parametrize('cursor', ['-1', '+1', '01', ' 1', '١', '1.0', '9223372036854775808'])
def test_bad_event_cursors_rejected(client, cursor):
    owner = register(client, 'owner', 'employer')
    auth, _ = key(client, owner)
    assert client.get(PREFIX + '/events', params={'after': cursor}, headers=auth).status_code == 422


def test_events_transactional_expiry_and_bounds(client):
    owner = register(client, 'owner', 'employer')
    auth, kid = key(client, owner)
    with client.app.state.factory() as db:
        before = db.scalar(select(func.count()).select_from(IntegrationEvent))
        from hiring.db import User
        user = db.scalar(select(User).where(User.role == 'employer'))
        db.add(Job(owner_id=user.id, company='Synthetic', title='Rollback', description='Synthetic rollback job', requirements=[]))
        db.flush()
        db.rollback()
        assert db.scalar(select(func.count()).select_from(IntegrationEvent)) == before
    assert client.get(PREFIX + '/jobs?limit=100000', headers=auth).status_code == 422
    assert client.post('/api/integration-keys', headers=owner, json={'name': 'Bad', 'scopes': ['root']}).status_code == 422
    with client.app.state.factory() as db:
        db.get(IntegrationKey, kid).expires_at = now() - timedelta(seconds=1)
        db.commit()
    assert client.get(PREFIX + '/jobs', headers=auth).status_code == 401


def test_bot_screening_pause_restart_progress_and_skip(client):
    client.app.state.config.employer_code = 'synthetic-code'
    send(client, 101, '/employer synthetic-code', 'e0')
    send(client, 101, 'Synthetic company', 'e1')
    send(client, 101, '/newjob', 'e2')
    send(client, 101, 'Python developer', 'e3')
    send(client, 101, 'Build a synthetic Python catalogue with documentation.', 'e4')
    click(client, 101, button(client, 101, '/screening'), 'e5')
    click(client, 101, button(client, 101, '/screening-on'), 'e6')
    click(client, 101, button(client, 101, 'Публиковать'), 'e7')
    with client.app.state.factory() as db:
        row = db.scalar(select(Job))
        jid = row.id
        assert len(row.screening_questions) == 3
    send(client, 202, '/start apply_' + jid, 'c0')
    send(client, 202, 'Согласен', 'c1')
    send(client, 202, 'Python: I developed a synthetic library catalogue with tests.', 'c2')
    with client.app.state.factory() as db:
        row = db.scalar(select(Application))
        aid = row.id
        assert row.questions[0]['id'] == 'screen_motivation'
    send(client, 202, 'Хочу разрабатывать каталог книг.', 'c3')
    click(client, 202, button(client, 202, '/pause'), 'c4')
    # All state lives in the DB; new event transactions resume it.
    click(client, 202, button(client, 202, '/continue ' + aid), 'c5')
    send(client, 202, 'Удалённо, ожидания обсуждаемы.', 'c6')
    click(client, 202, button(client, 202, 'Пропускаю уточнение, сведений недостаточно.'), 'c7')
    with client.app.state.factory() as db:
        row = db.get(Application, aid)
        assert row.status == 'ready' and len(row.answers) == 3
        assert row.answers['screen_motivation'] == 'Хочу разрабатывать каталог книг.'
        assert db.scalar(select(IntegrationEvent).where(IntegrationEvent.resource_id == aid))


def test_events_paging_no_cross_tenant_rows(client):
    owner = register(client, 'owner', 'employer')
    other = register(client, 'other', 'employer')
    auth, _ = key(client, owner)
    job(client, owner)
    job(client, other)
    jid = job(client, owner)
    one = client.get(PREFIX + '/events?limit=1', headers=auth).json()
    assert one['has_more'] is True
    two = client.get(PREFIX + '/events', params={'limit': 1, 'after': one['next_cursor']}, headers=auth).json()
    assert two['has_more'] is False and two['items'][0]['resource_id'] == jid
    assert int(two['next_cursor']) > int(one['next_cursor']) + 1


def test_data_engineering_vocabulary_and_evidence_aliases():
    from hiring.matching import extract, evidence
    requirements = extract('Требуются Spark/Hadoop, Greenplum и ETL. Используем pandas и SQL. Обеспечиваем качество данных.')
    skills = {r['skill'] for r in requirements}
    assert {'spark', 'hadoop', 'greenplum', 'etl', 'pandas', 'sql', 'data quality'} <= skills
    resume = 'Разработал ELT на PySpark. Написал DQ проверки. Greenplum не использовал.'
    rows = {r['skill']: r for r in evidence(resume, {}, requirements)['requirements']}
    assert rows['spark']['state'] == 'mentioned'
    assert rows['etl']['state'] == 'mentioned'
    assert rows['data quality']['state'] == 'mentioned'
    assert rows['greenplum']['state'] == 'review'
    assert rows['hadoop']['state'] == 'unknown'
    assert all(s in resume for row in rows.values() for s in row['snippets'])


def test_reserved_question_ids_and_duplicate_screening(client):
    owner = register(client, 'owner', 'employer')
    bad = vacancy()
    bad['requirements'][0]['id'] = 'screen_conditions'
    assert client.post('/api/jobs', headers=owner, json=bad).status_code == 422
    bad = vacancy()
    bad['screening_questions'] = ['screen_conditions', 'screen_conditions']
    assert client.post('/api/jobs', headers=owner, json=bad).status_code == 422
