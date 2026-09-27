"""Synthetic HTTP smoke, for an isolated database/container only; no MAX accounts."""
import argparse
import json
import time
from urllib import error, request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8000')
    parser.add_argument('--wait', action='store_true')
    parser.add_argument('--verify-existing', action='store_true')
    args = parser.parse_args()
    base = args.base_url.rstrip('/')

    def call(method, path, body=None, token=None, expected=200):
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        req = request.Request(base + path, data=json.dumps(body).encode() if body is not None else None, headers=headers, method=method)
        try:
            with request.urlopen(req, timeout=5) as response:
                assert response.status == expected, (path, response.status)
                return json.load(response) if expected != 204 else None
        except error.HTTPError as exc:
            if exc.code != expected:
                raise AssertionError(f'{path}: HTTP {exc.code}') from None

    for i in range(30 if args.wait else 1):
        try:
            call('GET', '/health')
            break
        except (OSError, AssertionError):
            if i == (29 if args.wait else 0):
                raise
            time.sleep(1)
    accounts = [('employer', 'smoke-employer@example.com'), ('candidate', 'smoke-candidate@example.com')]
    tokens = {}
    for role, email in accounts:
        if not args.verify_existing:
            call('POST', '/api/auth/register', {'email': email, 'password': 'synthetic-smoke-password',
                 'name': 'Synthetic smoke', 'company': 'Synthetic company', 'role': role,
                 'code': 'synthetic-ci-code' if role == 'employer' else ''}, expected=201)
        tokens[role] = call('POST', '/api/auth/login', {'email': email, 'password': 'synthetic-smoke-password'})['token']
    employer, candidate = tokens['employer'], tokens['candidate']
    prefix = '/api/integrations/v1'
    issued = call('POST', '/api/integration-keys', {'name': 'Synthetic smoke ATS',
        'scopes': ['jobs:read', 'jobs:write', 'applications:read', 'events:read', 'invitations:write']}, employer, 201)
    machine = issued['token']
    talent_key = call('POST', '/api/integration-keys', {'name': 'Synthetic talent smoke',
        'scopes': ['jobs:write', 'jobs:read', 'tests:read', 'tests:write', 'applications:read', 'applications:pii', 'applications:write']}, employer, 201)
    talent = talent_key['token']
    if args.verify_existing:
        rows = call('GET', '/api/applications', token=candidate)
        assert len(rows) == 1 and rows[0]['status'] == 'confirmed'
        assert rows[0]['resume'], 'Persistent volume did not retain the application'
        synced = call('GET', prefix + '/applications/' + rows[0]['id'], token=machine)
        assert synced['status'] == 'confirmed' and 'resume' not in synced
        assert call('GET', prefix + '/events', token=machine)['items']
        assert call('GET', prefix + '/tests', token=talent)['items']
        assert call('GET', prefix + '/applications/' + rows[0]['id'] + '/review', token=talent)['stage'] == 'shortlisted'
        assert rows[0]['answers']['test_0']
        call('DELETE', '/api/integration-keys/' + talent_key['id'], token=employer, expected=204)
        call('DELETE', '/api/integration-keys/' + issued['id'], token=employer, expected=204)
        call('GET', prefix + '/jobs', token=machine, expected=401)
        print('Restart smoke: persistent application and HR feed verified; no MAX sends.')
        return
    vacancy = {'title': 'Synthetic smoke vacancy',
        'description': 'Synthetic local verification vacancy. Python is required.',
        'requirements': [{'id': 'python', 'skill': 'python', 'label': 'Python', 'type': 'must'}],
        'screening_questions': ['screen_conditions']}
    jid = call('PUT', prefix + '/jobs/by-external/smoke/REQ-1', vacancy, machine)['id']
    assert call('PUT', prefix + '/jobs/by-external/smoke/REQ-1', vacancy, machine)['id'] == jid
    template = call('POST', prefix + '/tests', {'title': 'Synthetic Python exercise',
        'questions': [{'text': 'Describe a small test case for a catalogue.', 'rubric': 'Private synthetic checklist'}]}, talent, 201)
    call('PUT', prefix + '/jobs/' + jid + '/assessment', {'template_id': template['id'], 'expected_version': 1}, talent)
    draft = call('POST', prefix + '/jobs/draft-from-resume', {'resume': 'I used Python to build a synthetic catalogue with tests.'}, talent)
    assert draft['review_required'] and not draft['published']
    app = call('POST', f'/api/jobs/{jid}/apply', {'name': 'Synthetic candidate', 'resume': 'I used Python to build a synthetic catalog with automated tests.', 'consent': True}, candidate, 201)
    aid = app['id']
    assert app['status'] == 'clarifying'
    app = call('POST', f'/api/applications/{aid}/answers',
               {'answers': {'screen_conditions': 'Synthetic conditions: discuss at interview.',
                            'test_0': 'Synthetic test checks a missing catalogue item.'}}, candidate)
    assert app['status'] == 'ready'
    assert 'Private synthetic checklist' not in json.dumps(app)
    review_path = prefix + '/applications/' + aid + '/review'
    body = {'expected_version': 0, 'stage': 'shortlisted', 'note': 'Synthetic note', 'tags': ['smoke']}
    assert call('PATCH', review_path, body, talent)['version'] == 1
    call('PATCH', review_path, body, talent, expected=409)
    call('GET', prefix + '/applications/' + aid + '/resume.pdf', token=machine, expected=403)
    call('POST', prefix + f'/applications/{aid}/invite', {'message': 'Synthetic invitation for container smoke.'}, machine)
    assert call('POST', f'/api/applications/{aid}/confirm', token=candidate)['status'] == 'confirmed'
    assert call('GET', prefix + '/events', token=machine)['items']
    call('DELETE', '/api/integration-keys/' + issued['id'], token=employer, expected=204)
    call('DELETE', '/api/integration-keys/' + talent_key['id'], token=employer, expected=204)
    call('GET', prefix + '/jobs', token=machine, expected=401)
    print('HTTP smoke: HR upsert, test snapshot, draft, review conflict, screening, invitation, confirmation and revocation verified; no MAX sends.')


if __name__ == '__main__':
    main()
