"""Synthetic HTTP smoke, for an isolated database/container only; no MAX accounts."""
import argparse
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib import error, request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def synthetic_pdf():
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    writer = PdfWriter()
    page = writer.add_blank_page(600, 800)
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'),
                             NameObject('/BaseFont'): NameObject('/Helvetica')})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): font})})
    content = DecodedStreamObject()
    content.set_data(b'BT /F1 12 Tf 30 700 Td (I used Python to build a synthetic catalogue with automated tests.) Tj ET')
    page[NameObject('/Contents')] = writer._add_object(content)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def local_lifecycle():
    """Run the HTTP smoke against a private temporary database, including recovery."""
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    directory = Path(tempfile.mkdtemp(prefix='rezumit-http-smoke-'))
    env = {**os.environ, 'HIRING_DATABASE_URL': 'sqlite:///' + (directory / 'test.db').as_posix(),
           'HIRING_ENV': 'development', 'HIRING_DEMO': 'false', 'HIRING_WORKER': 'false',
           'HIRING_SECRET': 'synthetic-smoke-secret-not-production',
           'HIRING_EMPLOYER_CODE': 'synthetic-ci-code', 'MAX_BOT_TOKEN': '', 'PYTHONIOENCODING': 'utf-8'}
    base = f'http://127.0.0.1:{port}'
    for phase in ('create', 'restart', 'restore'):
        if phase == 'restore':
            env['HIRING_DATABASE_URL'] = 'sqlite:///' + (directory / 'recovered.db').as_posix()
        with (directory / 'server.log').open('ab') as log:
            process = subprocess.Popen(
                [sys.executable, '-m', 'uvicorn', 'hiring.main:app', '--host', '127.0.0.1',
                 '--port', str(port), '--no-access-log'], cwd=ROOT, env=env, stdout=log, stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            try:
                command = [sys.executable, str(Path(__file__).resolve()), '--base-url', base, '--wait']
                if phase != 'create':
                    command.append('--verify-existing')
                result = subprocess.run(command, cwd=ROOT, env=env, timeout=60)
                if result.returncode:
                    raise SystemExit('Smoke failed; private diagnostics: ' + str(directory))
            finally:
                if process.poll() is None:
                    if os.name == 'nt':
                        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True)
                    else:
                        process.terminate()
                        process.wait(timeout=20)
        if phase == 'create':
            from hiring.maintenance import backup
            backup(env['HIRING_DATABASE_URL'], directory / 'recovered.db')
    print('PASS: HTTP lifecycle, restart and SQLite recovery; synthetic diagnostics:', directory)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8000')
    parser.add_argument('--wait', action='store_true')
    parser.add_argument('--verify-existing', action='store_true')
    parser.add_argument('--local', action='store_true', help='Start an isolated API and test create, restart and backup recovery')
    args = parser.parse_args()
    if args.local:
        local_lifecycle()
        return
    base = args.base_url.rstrip('/')

    def call(method, path, body=None, token=None, expected=200, content_type='application/json'):
        headers = {'Content-Type': content_type}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        payload = body if isinstance(body, bytes) else (json.dumps(body).encode() if body is not None else None)
        req = request.Request(base + path, data=payload, headers=headers, method=method)
        try:
            with request.urlopen(req, timeout=5) as response:
                assert response.status == expected, (path, response.status)
                if response.headers.get_content_type() == 'application/pdf':
                    return response.read()
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
        'scopes': ['jobs:write', 'jobs:read', 'tests:read', 'tests:write', 'applications:read', 'applications:pii']}, employer, 201)
    talent = talent_key['token']
    if args.verify_existing:
        rows = call('GET', '/api/applications', token=candidate)
        assert len(rows) == 1 and rows[0]['status'] == 'confirmed'
        assert rows[0]['resume'], 'Persistent volume did not retain the application'
        synced = call('GET', prefix + '/applications/' + rows[0]['id'], token=machine)
        assert synced['status'] == 'confirmed' and 'resume' not in synced
        assert call('GET', prefix + '/events', token=machine)['items']
        assert call('GET', prefix + '/tests', token=talent)['items']
        assert rows[0]['answers']['test_0']
        document = prefix + '/applications/' + rows[0]['id'] + '/resume.pdf'
        assert call('GET', document, token=talent).startswith(b'%PDF-')
        call('DELETE', '/api/applications/' + rows[0]['id'], token=candidate, expected=204)
        call('GET', document, token=talent, expected=410)
        assert call('GET', prefix + '/applications/' + rows[0]['id'], token=machine)['deleted']
        call('DELETE', '/api/integration-keys/' + talent_key['id'], token=employer, expected=204)
        call('DELETE', '/api/integration-keys/' + issued['id'], token=employer, expected=204)
        call('GET', prefix + '/jobs', token=machine, expected=401)
        print('Restart smoke: application, PDF, test answers persisted; withdrawal removes private API access. No MAX sends.')
        return
    vacancy = {'title': 'Synthetic smoke vacancy',
        'description': 'Synthetic local verification vacancy. Python is required.',
        'requirements': [{'id': 'python', 'skill': 'python', 'label': 'Python', 'type': 'must'}],
        'screening_questions': ['screen_conditions']}
    jid = call('PUT', prefix + '/jobs/by-external/smoke/REQ-1', vacancy, machine)['id']
    assert call('PUT', prefix + '/jobs/by-external/smoke/REQ-1', vacancy, machine)['id'] == jid
    assert call('GET', prefix + '/jobs/by-external/smoke/REQ-1', token=machine)['id'] == jid
    template = call('POST', prefix + '/tests', {'title': 'Synthetic Python exercise',
        'questions': [{'text': 'Describe a small test case for a catalogue.', 'rubric': 'Private synthetic checklist'}]}, talent, 201)
    call('PUT', prefix + '/jobs/' + jid + '/assessment', {'template_id': template['id'], 'expected_version': 1}, talent)
    pdf = synthetic_pdf()
    boundary = 'rezumit-synthetic-smoke-boundary'
    multipart = (f'--{boundary}\r\nContent-Disposition: form-data; name="name"\r\n\r\nSynthetic candidate\r\n'
                 f'--{boundary}\r\nContent-Disposition: form-data; name="consent"\r\n\r\ntrue\r\n'
                 f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="synthetic.pdf"\r\n'
                 'Content-Type: application/pdf\r\n\r\n').encode() + pdf + f'\r\n--{boundary}--\r\n'.encode()
    app = call('POST', f'/api/jobs/{jid}/apply-pdf', multipart, candidate, 201, 'multipart/form-data; boundary=' + boundary)
    aid = app['id']
    assert app['status'] == 'clarifying'
    app = call('POST', f'/api/applications/{aid}/answers',
               {'answers': {'screen_conditions': 'Synthetic conditions: discuss at interview.',
                            'test_0': 'Synthetic test checks a missing catalogue item.'}}, candidate)
    assert app['status'] == 'ready'
    assert 'Private synthetic checklist' not in json.dumps(app)
    call('GET', prefix + '/applications/' + aid + '/resume.pdf', token=machine, expected=403)
    assert call('GET', prefix + '/applications/' + aid + '/resume.pdf', token=talent) == pdf
    call('POST', prefix + f'/applications/{aid}/invite', {'message': 'Synthetic invitation for container smoke.'}, machine)
    assert call('POST', f'/api/applications/{aid}/confirm', token=candidate)['status'] == 'confirmed'
    assert call('GET', prefix + '/events', token=machine)['items']
    call('DELETE', '/api/integration-keys/' + issued['id'], token=employer, expected=204)
    call('DELETE', '/api/integration-keys/' + talent_key['id'], token=employer, expected=204)
    call('GET', prefix + '/jobs', token=machine, expected=401)
    print('HTTP smoke: HR upsert, test snapshot, screening, invitation, confirmation and revocation verified; no MAX sends.')


if __name__ == '__main__':
    main()
