from hiring.sources import allowed_download
from sqlalchemy import select
from hiring.db import ImportTask, Outbox
from test_employer_bot import send
from test_product import client, job, register


def test_only_max_attachment_host_can_be_downloaded():
    assert allowed_download('fd.oneme.ru', 'max')
    assert not allowed_download('fd.oneme.ru.attacker.test', 'max')
    assert not allowed_download('disk.yandex.ru', 'yandex')
    assert not allowed_download('drive.google.com', 'google')
    assert not allowed_download('cloud.mail.ru', 'mail')


def test_candidate_must_attach_files_in_max(client):
    employer = register(client, 'link-owner', 'employer')
    jid = job(client, employer)
    send(client, 701, '/start apply_' + jid, 1)
    send(client, 701, 'Согласен', 2)
    send(client, 701, 'https://disk.yandex.ru/i/synthetic', 3)
    with client.app.state.factory() as db:
        assert db.scalar(select(ImportTask)) is None
        assert any('Ссылки на файлы не поддерживаются' in row.body.get('text', '')
                   for row in db.scalars(select(Outbox)))
