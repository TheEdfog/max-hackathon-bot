import hashlib
import io
import json
from datetime import timedelta
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
import pytest
from sqlalchemy import select
from hiring.db import Application, ApplicationReview, AssessmentTemplate, BotSession, ImportTask, Job, Outbox, ResumeDocument, User, now
from hiring.imports import deliver_import
from hiring.matching import evidence, extract
from hiring.sources import SourceError, SourceResult, allowed_download, import_source, source_kind
from test_product import client, register, job
from test_integrations import key, PREFIX
from test_employer_bot import send
import httpx
from hiring.sources import PublicReader

ALL = ['jobs:read', 'jobs:write', 'applications:read', 'applications:pii', 'applications:write',
       'tests:read', 'tests:write', 'events:read']
TEST = {'title': 'SQL practice', 'questions': [{'text': 'Explain how you would find duplicates in SQL.', 'rubric': 'GROUP BY + HAVING'}]}
RESUME = 'Python PostgreSQL: built a synthetic catalogue with tests and documentation.'


def pdf_bytes(text=RESUME):
    # In-memory synthetic test fixture, not a user's document or a deliverable.
    writer = PdfWriter()
    page = writer.add_blank_page(width=600, height=800)
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'),
                             NameObject('/BaseFont'): NameObject('/Helvetica')})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): font})})
    stream = DecodedStreamObject()
    stream.set_data(('BT /F1 12 Tf 30 700 Td (' + text + ') Tj ET').encode())
    page[NameObject('/Contents')] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def test_long_pdf_is_rejected_not_silently_truncated():
    from hiring.pdf_extract import extract_pdf
    with pytest.raises(ValueError):
        extract_pdf(pdf_bytes('Python ' * 3000))


@pytest.mark.parametrize('text,skill,state', [
    ('Разработал сервис на питоне.', 'python', 'mentioned'), ('Использовал postres для каталога.', 'postgresql', 'mentioned'),
    ('Использовал postres для каталога.', 'sql', 'inferred'), ('Использовал SQL для каталога.', 'postgresql', 'unknown'),
    ('Нет опыта PostgreSQL.', 'sql', 'unknown'), ('Хочу изучить PostgreSQL.', 'sql', 'unknown'),
    ('Использовал PostgreSQL, но SQL не знаю.', 'sql', 'negative'),
    ('Работал с NoSQL.', 'sql', 'unknown'), ('Использовал MySQL.', 'sql', 'inferred'),
    ('Использовал Python, но Docker не знаю.', 'python', 'mentioned'),
])
def test_directional_aliases(text, skill, state):
    result = evidence(text, {}, [{'id': 'r', 'skill': skill, 'label': skill, 'type': 'must'}])
    assert result['requirements'][0]['state'] == state
    if state == 'inferred':
        assert result['covered'] == result['inferred'] == 1 and result['direct_covered'] == 0
        assert result['requirements'][0]['snippets'][0] in text


def test_alias_extraction_and_duplicates(client):
    assert {'python', 'postgresql'} <= {r['skill'] for r in extract('Питон и postres для разработки внутреннего сервиса')}
    employer = register(client, 'owner', 'employer')
    body = {'title': 'Python role', 'description': 'A synthetic vacancy for a Python programmer.',
            'requirements': [{'id': 'a', 'skill': 'питон', 'label': 'Python'}, {'id': 'b', 'skill': 'python', 'label': 'Python'}]}
    assert client.post('/api/jobs', headers=employer, json=body).status_code == 422


def test_related_technology_in_clarification():
    requirements = [{'id': 'sql', 'skill': 'sql', 'label': 'SQL', 'type': 'must'}]
    answer = 'Создал таблицы в postres для учебного каталога.'
    result = evidence('Разрабатывал учебный каталог книг.', {'sql': answer}, requirements)
    assert result['requirements'][0]['state'] == 'inferred'
    assert result['requirements'][0]['related'][0]['source'] == 'answer'
    assert result['requirements'][0]['answer'] == answer
    conflict = evidence('SQL не знаю.', {'sql': answer}, requirements)
    assert conflict['requirements'][0]['state'] == 'conflict'


def test_testbank_snapshot_rubric_isolation_and_versioning(client):
    owner, other, candidate = register(client, 'owner', 'employer'), register(client, 'other', 'employer'), register(client, 'candidate')
    auth, _ = key(client, owner, ALL)
    foreign, _ = key(client, other, ALL)
    jid = job(client, owner)
    template = client.post(PREFIX + '/tests', headers=auth, json=TEST)
    assert template.status_code == 201, template.text
    tid = template.json()['id']
    assert client.get(PREFIX + '/tests/' + tid, headers=foreign).status_code == 404
    assert client.get(PREFIX + '/tests', headers=foreign).json()['items'] == []
    link = PREFIX + '/jobs/' + jid + '/assessment'
    assert client.put(link, headers=auth, json={'template_id': tid, 'expected_version': 2}).status_code == 409
    assert client.put(link, headers=auth, json={'template_id': tid, 'expected_version': 1}).status_code == 200
    assert client.put(PREFIX + '/tests/' + tid, headers=auth, json={**TEST, 'expected_version': 1, 'questions': [{'text': 'A changed question for later vacancies'}]}).status_code == 200
    assert client.put(PREFIX + '/tests/' + tid, headers=auth, json={**TEST, 'expected_version': 1}).status_code == 409
    public = client.get('/api/public/jobs/' + jid).text
    assert 'HAVING' not in public and 'rubric' not in public
    result = client.post('/api/jobs/' + jid + '/apply', headers=candidate, json={'name': 'Candidate', 'resume': RESUME, 'consent': True}).json()
    assert result['questions'] == [{'id': 'test_0', 'kind': 'assessment', 'label': 'Задание 1', 'text': TEST['questions'][0]['text']}]
    assert 'HAVING' not in json.dumps(result)
    assert client.put(link, headers=auth, json={'template_id': None}).status_code == 409
    assert client.post('/api/applications/' + result['id'] + '/answers', headers=candidate, json={'answers': {'test_0': 'GROUP BY with HAVING COUNT(*) > 1'}}).json()['status'] == 'ready'


def test_pdf_original_review_and_withdrawal(client):
    owner, other, candidate = register(client, 'owner', 'employer'), register(client, 'other', 'employer'), register(client, 'candidate')
    auth, _ = key(client, owner, ALL)
    foreign, _ = key(client, other, ALL)
    reader, _ = key(client, owner)
    jid, raw = job(client, owner), pdf_bytes()
    path = '/api/jobs/' + jid + '/apply-pdf'
    files = {'file': ('private-name.pdf', raw, 'application/pdf')}
    assert client.post(path, headers=candidate, files=files, data={'name': 'Candidate', 'consent': 'false'}).status_code == 422
    response = client.post(path, headers=candidate, files=files, data={'name': 'Candidate', 'consent': 'true'})
    assert response.status_code == 201, response.text
    aid = response.json()['id']
    assert client.post(path, headers=candidate, files=files, data={'name': 'Changed name', 'consent': 'true'}).json()['name'] == 'Candidate'
    path = PREFIX + '/applications/' + aid
    assert client.get(path + '/resume.pdf', headers=reader).status_code == 403
    assert client.get(path + '/resume.pdf', headers=foreign).status_code == 404
    pdf = client.get(path + '/resume.pdf', headers=auth)
    assert pdf.content == raw and pdf.headers['X-Content-SHA256'] == hashlib.sha256(raw).hexdigest()
    assert 'private-name' not in str(pdf.headers) and pdf.headers['cache-control'] == 'no-store'
    assert RESUME in client.get(path + '/resume.txt', headers=auth).text
    review = {'stage': 'shortlisted', 'note': 'Synthetic note', 'tags': ['python'], 'expected_version': 0}
    assert client.patch(path + '/review', headers=reader, json=review).status_code == 403
    assert client.patch(path + '/review', headers=foreign, json=review).status_code == 404
    assert client.patch(path + '/review', headers=auth, json=review).json()['version'] == 1
    assert client.patch(path + '/review', headers=auth, json=review).status_code == 409
    assert client.get(path + '/review', headers=auth).json()['stage'] == 'shortlisted'
    client.delete('/api/applications/' + aid, headers=candidate).raise_for_status()
    assert client.get(path + '/resume.pdf', headers=auth).status_code == 410
    assert client.get(path + '/review', headers=auth).status_code == 410
    with client.app.state.factory() as db:
        assert db.get(ResumeDocument, aid) is None and db.get(ApplicationReview, aid) is None


def test_draft_no_pii_no_publication(client):
    auth, _ = key(client, register(client, 'owner', 'employer'), ALL)
    text = 'Synthetic Person, fictional@example.com. Использовал Python. Нет опыта Docker.'
    response = client.post(PREFIX + '/jobs/draft-from-resume', headers=auth, json={'resume': text})
    assert response.status_code == 200, response.text
    assert not response.json()['published'] and response.json()['review_required']
    assert 'fictional@' not in response.text and 'Synthetic Person' not in response.text
    assert [r['skill'] for r in response.json()['requirements']] == ['python']
    assert client.get(PREFIX + '/jobs', headers=auth).json()['items'] == []


@pytest.mark.parametrize('url', ['http://github.com/user', 'https://127.0.0.1/test.pdf', 'https://github.com.evil.test/user',
    'https://evil.test@github.com/user', 'https://github.com:444/user', 'https://github.com/user\\evil',
    'https://drive.google.com.evil.test/file/d/abc/view', 'https://disk.yandex.ru.evil.test/d/abc'])
def test_unsafe_urls_rejected(url):
    with pytest.raises(SourceError):
        source_kind(url)


def test_source_contracts_and_mail_fallback():
    assert source_kind('https://cloud.mail.ru/public/abc/def') == 'mail'
    with pytest.raises(SourceError, match='пока не поддерживается'):
        import_source('https://cloud.mail.ru/public/abc/def')
    assert allowed_download('downloader.disk.yandex.ru', 'yandex')
    assert not allowed_download('downloader.disk.yandex.ru.evil.test', 'yandex')
    class Fake:
        def json(self, url, provider):
            return {'href': 'https://downloader.disk.yandex.ru/fake'}
        def get(self, url, provider):
            return pdf_bytes()
    for url, provider in [('https://disk.yandex.ru/d/synthetic', 'yandex'), ('https://drive.google.com/file/d/synthetic/view', 'google')]:
        result = import_source(url, Fake())
        assert RESUME in result.text and result.pdf.startswith(b'%PDF') and result.provider == provider


def test_public_reader_redirect_dns_size_and_no_auth(monkeypatch):
    calls = []
    monkeypatch.setattr('hiring.sources.socket.getaddrinfo', lambda *a, **k: [(2, 1, 6, '', ('93.184.216.34', 443))])
    real_client = httpx.Client
    def response(request):
        calls.append(request)
        assert not request.headers.get('authorization') and not request.headers.get('cookie')
        return httpx.Response(302, headers={'location': 'https://127.0.0.1/private'})
    monkeypatch.setattr('hiring.sources.httpx.Client', lambda **kw: real_client(**kw, transport=httpx.MockTransport(response)))
    with pytest.raises(SourceError, match='неподдерживаемый'):
        PublicReader().get('https://api.github.com/users/synthetic/repos', 'github')
    assert len(calls) == 1
    monkeypatch.setattr('hiring.sources.socket.getaddrinfo', lambda *a, **k: [(2, 1, 6, '', ('127.0.0.1', 443))])
    with pytest.raises(SourceError, match='недоступен'):
        PublicReader().get('https://api.github.com/users/synthetic/repos', 'github')
    assert len(calls) == 1
    monkeypatch.setattr('hiring.sources.socket.getaddrinfo', lambda *a, **k: [(2, 1, 6, '', ('93.184.216.34', 443))])
    monkeypatch.setattr('hiring.sources.httpx.Client', lambda **kw: real_client(**kw, transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b'x' * 30))))
    with pytest.raises(SourceError, match='слишком большой'):
        PublicReader().get('https://api.github.com/users/synthetic/repos', 'github', limit=20)


def test_no_pdf_fabrication_and_parse_scope(client):
    owner, candidate = register(client, 'owner', 'employer'), register(client, 'candidate')
    auth, _ = key(client, owner, ALL)
    reader, _ = key(client, owner)
    jid = job(client, owner)
    aid = client.post('/api/jobs/' + jid + '/apply', headers=candidate, json={'name': 'Candidate', 'resume': RESUME, 'consent': True}).json()['id']
    assert client.get(PREFIX + '/applications/' + aid + '/resume.pdf', headers=auth).status_code == 404
    files = {'file': ('synthetic.pdf', pdf_bytes(), 'application/pdf')}
    assert client.post(PREFIX + '/documents/parse', headers=reader, files=files).status_code == 403
    parsed = client.post(PREFIX + '/documents/parse', headers=auth, files=files)
    assert parsed.status_code == 200 and not parsed.json()['stored']


def test_github_metadata_only_and_source_api(client, monkeypatch):
    class Github:
        def json(self, url, provider):
            if url.endswith('/languages'):
                return {'Python': 123}
            return [{'full_name': 'SyntheticExample/project', 'private': False, 'fork': True, 'description': 'Public synthetic project'}]
    result = import_source('https://github.com/SyntheticExample', Github())
    assert 'форк' in result.text and 'Python' in result.text and 'не доказывают' in result.warning
    monkeypatch.setattr('hiring.sources.import_source', lambda url: result)
    auth, _ = key(client, register(client, 'owner', 'employer'), ALL)
    data = client.post(PREFIX + '/sources/preview', headers=auth, json={'url': 'https://github.com/SyntheticExample'})
    assert data.status_code == 200 and not data.json()['stored']


def candidate_import(client, uid=300):
    employer = register(client, 'owner', 'employer')
    jid = job(client, employer)
    send(client, uid, '/start apply_' + jid, 500)
    send(client, uid, 'Согласен', 501)
    send(client, uid, 'https://github.com/SyntheticExample', 502)
    with client.app.state.factory() as db:
        row = db.scalar(select(ImportTask))
        assert row.status == 'pending'
        return row.id, jid


def test_max_import_preview_confirmation_and_cleanup(client):
    tid, jid = candidate_import(client)
    assert deliver_import(client.app.state.factory, lambda url: SourceResult(RESUME, 'yandex', 'Synthetic', pdf_bytes()))
    with client.app.state.factory() as db:
        assert db.scalar(select(Application)) is None
        assert db.get(ImportTask, tid).url == ''
    send(client, 301, '/import-confirm ' + tid, 503)
    with client.app.state.factory() as db:
        assert db.scalar(select(Application)) is None
    send(client, 300, '/import-preview ' + tid, 504)
    send(client, 300, '/import-confirm ' + tid, 505)
    with client.app.state.factory() as db:
        row = db.scalar(select(Application))
        assert row.resume == RESUME and db.get(ResumeDocument, row.id)
        task = db.get(ImportTask, tid)
        assert task.status == 'used' and not task.text and not task.pdf
        assert all(not o.body for o in db.scalars(select(Outbox).where(Outbox.import_id == tid)))


def test_import_cancel_during_download_and_expiry(client):
    tid, _ = candidate_import(client)
    def slow_source(url):
        # If fetch held the write transaction, this independent bot event would deadlock.
        send(client, 300, '/cancel', 506)
        return SourceResult(RESUME, 'github', 'Synthetic')
    deliver_import(client.app.state.factory, slow_source)
    with client.app.state.factory() as db:
        assert db.get(ImportTask, tid).status == 'cancelled'
        assert not db.get(ImportTask, tid).text
        db.get(ImportTask, tid).expires_at = now() - timedelta(seconds=1)
        db.commit()
    deliver_import(client.app.state.factory, lambda _: pytest.fail('Expired task must not fetch'))
    with client.app.state.factory() as db:
        assert db.get(ImportTask, tid) is None


def test_import_lease_recovery_and_expired_preview_not_sent(client):
    tid, _ = candidate_import(client)
    with client.app.state.factory() as db:
        task = db.get(ImportTask, tid)
        task.status, task.lease_until = 'working', now() - timedelta(seconds=1)
        db.commit()
    assert deliver_import(client.app.state.factory, lambda _: SourceResult(RESUME, 'github', 'Synthetic'))
    with client.app.state.factory() as db:
        task = db.get(ImportTask, tid)
        assert task.status == 'ready'
        task.expires_at = now() - timedelta(seconds=1)
        # Focus the delivery guard test on the PII-bearing preview only.
        for other in db.scalars(select(Outbox).where(Outbox.import_id.is_(None))):
            other.status = 'sent'
        db.commit()
    from hiring.outbox import deliver_one
    deliver_one(client.app.state.factory, client.app.state.config, lambda *a, **k: pytest.fail('Expired preview must not be sent'))
    with client.app.state.factory() as db:
        preview = db.scalar(select(Outbox).where(Outbox.import_id == tid))
        assert preview.status == 'cancelled' and not preview.body


def test_max_test_library_and_question_flow(client):
    client.app.state.config.employer_code = 'synthetic-code'
    messages = ['/employer synthetic-code', 'Synthetic Company', '/newtest', 'SQL exercise',
                'Explain how to find duplicates in SQL || GROUP BY and HAVING', '/tests']
    for i, text in enumerate(messages):
        send(client, 400, text, 600 + i)
    with client.app.state.factory() as db:
        tid = db.scalar(select(AssessmentTemplate)).id
    for i, text in enumerate(['/newjob', 'SQL analyst', 'SQL skills for a synthetic catalogue and its reports.', '/use-test ' + tid, 'Публиковать']):
        send(client, 400, text, 610 + i)
    with client.app.state.factory() as db:
        row = db.scalar(select(Job))
        assert row.test_questions[0]['rubric'] == 'GROUP BY and HAVING'
        jid = row.id
    for i, text in enumerate(['/start apply_' + jid, 'Согласен', 'SQL: I designed tables and wrote reports for a synthetic catalogue.', 'GROUP BY with HAVING COUNT(*) > 1']):
        send(client, 500, text, 620 + i)
    with client.app.state.factory() as db:
        app = db.scalar(select(Application))
        assert app.status == 'ready'
        aid = app.id
        candidate_messages = [o.body.get('text', '') for o in db.scalars(select(Outbox).where(Outbox.max_id == '500'))]
        assert not any('GROUP BY and HAVING' in t for t in candidate_messages)
    send(client, 400, '/test-answers ' + aid, 630)
    send(client, 400, '/archive-test ' + tid, 631)
    with client.app.state.factory() as db:
        assert not db.get(AssessmentTemplate, tid).active
        assert db.get(Job, jid).test_questions
