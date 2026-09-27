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
    if args.verify_existing:
        rows = call('GET', '/api/applications', token=candidate)
        assert len(rows) == 1 and rows[0]['status'] == 'confirmed'
        assert rows[0]['resume'], 'Persistent volume did not retain the application'
        print('Restart smoke: persistent confirmed application verified; no MAX sends.')
        return
    jid = call('POST', '/api/jobs', {'title': 'Synthetic smoke vacancy',
        'description': 'Synthetic local verification vacancy. Python is required.',
        'requirements': [{'id': 'python', 'skill': 'python', 'label': 'Python', 'type': 'must'}]}, employer, 201)['id']
    app = call('POST', f'/api/jobs/{jid}/apply', {'name': 'Synthetic candidate', 'resume': 'I used Python to build a synthetic catalog with automated tests.', 'consent': True}, candidate, 201)
    aid = app['id']
    assert app['status'] == 'ready'
    call('POST', f'/api/applications/{aid}/invite', {'message': 'Synthetic invitation for container smoke.'}, employer)
    assert call('POST', f'/api/applications/{aid}/confirm', token=candidate)['status'] == 'confirmed'
    print('HTTP smoke: hiring cycle completed using synthetic, non-MAX accounts.')


if __name__ == '__main__':
    main()
