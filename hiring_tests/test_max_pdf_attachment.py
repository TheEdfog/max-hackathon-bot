import httpx
import pytest
from sqlalchemy import select
from hiring.bot import process_event
from hiring.db import Application, BotSession, ImportTask, Outbox, ResumeDocument, User
from hiring.imports import deliver_import
from hiring.pdf_extract import MAX_BYTES
from hiring.sources import PublicReader, SourceError, SourceResult, allowed_download, import_max_attachment
from test_employer_bot import send
from test_product import client, job, register
from test_talent import RESUME, pdf_bytes


URL = 'https://fd.oneme.ru/getfile?signature=temporary-test-signature'


def begin_resume_upload(client, uid=700):
    employer = register(client, 'attachment-owner', 'employer')
    job_id = job(client, employer)
    send(client, uid, '/start apply_' + job_id, 1)
    send(client, uid, 'Согласен', 2)
    return job_id


def upload_event(client, uid, raw, *, filename='resume.pdf', size=None, url=URL, mid=3):
    process_event(client.app.state.factory, {
        'update_type': 'message_created',
        'message': {
            'sender': {'user_id': uid}, 'recipient': {'chat_type': 'dialog'},
            'body': {'mid': str(mid), 'attachments': [{
                'type': 'file', 'filename': filename, 'size': len(raw) if size is None else size,
                'payload': {'url': url, 'token': 'temporary-token'},
            }]},
        },
    }, client.app.state.config)


def test_max_pdf_attachment_is_previewed_then_saved_only_after_confirmation(client):
    job_id = begin_resume_upload(client)
    raw = pdf_bytes()
    upload_event(client, 700, raw)

    with client.app.state.factory() as db:
        task = db.scalar(select(ImportTask))
        user = db.scalar(select(User).where(User.max_id == '700'))
        session = db.get(BotSession, user.id)
        assert session.state == {'step': 'import_waiting', 'job_id': job_id, 'import_id': task.id}
        assert task.provider == 'max_pending' and task.url == URL
        assert db.scalar(select(Application)) is None
        queued = db.scalars(select(Outbox)).all()
        assert all(URL not in str(row.body) for row in queued)

    class MaxFile:
        def get(self, url, provider):
            assert url == URL and provider == 'max'
            return raw

    result = import_max_attachment(URL, len(raw), MaxFile())
    assert result.text.strip() == RESUME
    assert result.pdf == raw and result.provider == 'max'

    assert deliver_import(client.app.state.factory,
                          fetch_attachment=lambda url, size: import_max_attachment(url, size, MaxFile()))
    with client.app.state.factory() as db:
        task = db.scalar(select(ImportTask))
        assert task.status == 'ready' and task.provider == 'max' and task.url == ''
        assert db.scalar(select(Application)) is None

    send(client, 700, '/import-confirm ' + task.id, 4)
    with client.app.state.factory() as db:
        application = db.scalar(select(Application))
        document = db.get(ResumeDocument, application.id)
        task = db.get(ImportTask, task.id)
        assert application.job_id == job_id and application.resume.strip() == RESUME
        assert document.data == raw and document.provider == 'max'
        assert task.status == 'used' and not task.url and not task.text and task.pdf is None


@pytest.mark.parametrize('filename,size,url', [
    ('resume.docx', 100, URL), ('resume.pdf', MAX_BYTES + 1, URL),
    ('resume.pdf', 100, 'https://127.0.0.1/private'),
])
def test_invalid_max_files_never_create_import_task(client, filename, size, url):
    begin_resume_upload(client, uid=701)
    raw = b'%PDF-1.7'
    upload_event(client, 701, raw, filename=filename, size=size, url=url)
    with client.app.state.factory() as db:
        assert db.scalar(select(ImportTask)) is None
        session = db.get(BotSession, db.scalar(select(User).where(User.max_id == '701')).id)
        assert session.state['step'] == 'resume'
        assert db.scalar(select(Application)) is None
        assert any('PDF' in str(row.body) or 'импорта' in str(row.body)
                   for row in db.scalars(select(Outbox)))


def test_max_file_download_is_bounded_and_host_allowlisted(monkeypatch):
    raw = pdf_bytes()
    calls = []
    monkeypatch.setattr('hiring.sources.socket.getaddrinfo',
                        lambda *args, **kwargs: [(2, 1, 6, '', ('93.184.216.34', 443))])
    real_client = httpx.Client

    def response(request):
        calls.append(request)
        assert 'authorization' not in request.headers and 'cookie' not in request.headers
        return httpx.Response(200, content=raw)

    monkeypatch.setattr('hiring.sources.httpx.Client',
                        lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(response)))
    assert PublicReader().get(URL, 'max') == raw
    assert allowed_download('fd.oneme.ru', 'max')
    assert not allowed_download('fd.oneme.ru.attacker.test', 'max')
    assert len(calls) == 1

    monkeypatch.setattr('hiring.sources.httpx.Client', lambda **kwargs: real_client(
        **kwargs, transport=httpx.MockTransport(
            lambda _: httpx.Response(302, headers={'location': 'https://127.0.0.1/private'}))))
    with pytest.raises(SourceError, match='неподдерживаемый'):
        PublicReader().get(URL, 'max')
    assert len(calls) == 1
