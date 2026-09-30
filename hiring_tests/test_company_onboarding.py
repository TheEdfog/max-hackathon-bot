import json
from sqlalchemy import select
from datetime import timedelta

from hiring.db import CompanyInvite, User, now
from hiring.bot import process_event
from test_product import client, register, job as create_job, signed_data


def max_message(client, max_id, text, message_id):
    event = {'update_type': 'message_created', 'message': {
        'sender': {'user_id': max_id, 'name': f'Synthetic MAX {max_id}'},
        'recipient': {'chat_type': 'dialog'},
        'body': {'mid': str(message_id), 'text': text}}}
    process_event(client.app.state.factory, event, client.app.state.config)


def test_company_admin_links_max_and_onboards_shared_recruiter(client):
    admin_headers = register(client, 'team-admin', 'employer')
    link = client.post('/api/company/max-link', headers=admin_headers).json()
    max_message(client, 701, link['command'], 'link-admin')
    admin_max = client.post('/api/auth/max', json={'init_data': signed_data({
        'user': json.dumps({'id': 701, 'first_name': 'Synthetic admin'})})})
    assert admin_max.status_code == 200 and admin_max.json()['user']['company_role'] == 'admin'

    recruiter_invite = client.post('/api/company/recruiter-invitations', headers=admin_headers)
    assert recruiter_invite.status_code == 200
    max_message(client, 702, recruiter_invite.json()['command'], 'join-recruiter')
    recruiter_max = client.post('/api/auth/max', json={'init_data': signed_data({
        'user': json.dumps({'id': 702, 'first_name': 'Synthetic recruiter'})})})
    assert recruiter_max.status_code == 200 and recruiter_max.json()['user']['company_role'] == 'recruiter'
    recruiter_headers = {'Authorization': 'Bearer ' + recruiter_max.json()['token']}

    with client.app.state.factory() as db:
        admin = db.scalar(select(User).where(User.email == 'team-admin@example.com'))
        recruiter = db.scalar(select(User).where(User.max_id == '702'))
        assert admin.max_id == '701' and admin.company_role == 'admin'
        assert recruiter.role == 'employer' and recruiter.company_id == admin.company_id
        assert recruiter.company_role == 'recruiter'

    jid = create_job(client, admin_headers)
    assert [row['id'] for row in client.get('/api/jobs', headers=recruiter_headers).json()] == [jid]
    max_message(client, 702, '/job ' + jid, 'recruiter-opens-admin-job')
    max_message(client, 702, '/close ' + jid, 'recruiter-closes-admin-job')
    assert client.get('/api/jobs', headers=admin_headers).json()[0]['active'] is False
    max_message(client, 702, '/newjob', 'recruiter-newjob')
    max_message(client, 702, 'Python Analyst', 'recruiter-title')
    max_message(client, 702, 'Build internal data tools using Python and PostgreSQL with clear tests.', 'recruiter-description')
    max_message(client, 702, 'Публиковать', 'recruiter-publish')
    admin_jobs = client.get('/api/jobs', headers=admin_headers).json()
    assert len(admin_jobs) == 2
    recruiter_bot_job_id = next(row['id'] for row in admin_jobs if row['id'] != jid)
    recruiter_job = client.post('/api/jobs', headers=recruiter_headers, json={
        'title': 'Recruiter-created Python role',
        'description': 'Synthetic role for testing team-owned vacancy access and shared candidate workflow.',
        'requirements': [{'id': 'python', 'skill': 'python', 'label': 'Python', 'type': 'must'}]})
    assert recruiter_job.status_code == 201
    recruiter_job_id = recruiter_job.json()['id']
    company_job_ids = {jid, recruiter_bot_job_id, recruiter_job.json()['id']}
    assert {row['id'] for row in client.get('/api/jobs', headers=admin_headers).json()} == company_job_ids
    members = client.get('/api/company/members', headers=admin_headers).json()
    assert {row['role'] for row in members if row['active']} == {'admin', 'recruiter'}

    key = client.post('/api/integration-keys', headers=admin_headers, json={
        'name': 'company test', 'scopes': ['jobs:read']}).json()
    token = {'Authorization': 'Bearer ' + key['token']}
    integration_job_ids = {row['id'] for row in client.get('/api/integrations/v1/jobs', headers=token).json()['items']}
    assert company_job_ids <= integration_job_ids

    other_headers = register(client, 'team-other', 'employer')
    assert client.get(f'/api/jobs/{jid}', headers=other_headers).status_code == 404
    assert client.delete(f"/api/company/members/{recruiter.id}", headers=admin_headers).status_code == 204
    assert client.get(f'/api/jobs/{jid}', headers=admin_headers).status_code == 200
    assert client.get('/api/jobs', headers=recruiter_headers).status_code == 401
    assert client.get('/api/company/members', headers=admin_headers).json()[1]['active'] is False


def test_company_codes_are_one_time_expiring_and_accounts_are_not_merged(client):
    admin = register(client, 'code-admin', 'employer')
    invite = client.post('/api/company/recruiter-invitations', headers=admin).json()
    max_message(client, 710, invite['command'], 'join-once')
    max_message(client, 711, invite['command'], 'join-replay')
    with client.app.state.factory() as db:
        assert db.scalar(select(User).where(User.max_id == '710'))
        assert db.scalar(select(User).where(User.max_id == '711')) is None

    register(client, 'existing-max-candidate')
    with client.app.state.factory() as db:
        candidate = db.scalar(select(User).where(User.email == 'existing-max-candidate@example.com'))
        candidate.max_id = '712'
        db.commit()
    second = client.post('/api/company/recruiter-invitations', headers=admin).json()
    max_message(client, 712, second['command'], 'candidate-cannot-promote')
    with client.app.state.factory() as db:
        candidate = db.scalar(select(User).where(User.email == 'existing-max-candidate@example.com'))
        assert candidate.role == 'candidate' and candidate.company_role == ''

    expired = client.post('/api/company/recruiter-invitations', headers=admin).json()
    code = expired['command'].split(maxsplit=1)[1]
    import hashlib
    with client.app.state.factory() as db:
        row = db.scalar(select(CompanyInvite).where(CompanyInvite.code_hash == hashlib.sha256(code.encode()).hexdigest()))
        row.expires_at = now() - timedelta(seconds=1)
        db.commit()
    max_message(client, 713, expired['command'], 'join-expired')
    with client.app.state.factory() as db:
        assert db.scalar(select(User).where(User.max_id == '713')) is None
