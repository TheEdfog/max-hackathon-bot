"""Synthetic MAX events only. No browser, network or real candidate records."""
from datetime import timedelta
import pytest
from sqlalchemy import select
from hiring.db import Application, BotSession, ImportTask, Outbox, ResumeDocument, now
from hiring.imports import deliver_import
from hiring.sources import SourceResult
from test_buttons_delivery import button, click
from test_employer_bot import send
from test_product import client
from test_talent import RESUME, candidate_import, pdf_bytes


@pytest.mark.parametrize('mode', ['add', 'edit'])
def test_edit_requires_confirmation_and_invalidates_previous_buttons(client, mode):
    tid, _ = candidate_import(client)
    deliver_import(client.app.state.factory, lambda _: SourceResult(RESUME, 'yandex', 'Synthetic', pdf_bytes()))
    previous_confirm = button(client, 300, '/import-confirm ' + tid)
    click(client, 300, button(client, 300, '/import-' + mode + ' ' + tid), 'edit-0')
    send(client, 300, '/import-confirm ' + tid, 'edit-1')
    with client.app.state.factory() as db:
        assert db.scalar(select(Application)) is None
    revised = 'My contribution: I built the catalogue tests and reviewed the Python code.'
    send(client, 300, revised, 'edit-2')
    click(client, 300, previous_confirm, 'edit-3')
    with client.app.state.factory() as db:
        task = db.get(ImportTask, tid)
        assert task.text == (RESUME + '\n\nДополнение кандидата:\n' + revised if mode == 'add' else revised)
        assert bool(task.pdf) == (mode == 'add')
        assert db.scalar(select(Application)) is None
        assert db.get(BotSession, task.user_id).state['import_revision'] == 1
        # Superseded server copies are cleared, even if already sent to MAX.
        assert any(not row.body for row in db.scalars(select(Outbox).where(Outbox.import_id == tid)))
    click(client, 300, button(client, 300, '/import-confirm ' + tid), 'edit-4')
    with client.app.state.factory() as db:
        row = db.scalar(select(Application))
        assert revised in row.resume
        assert bool(db.get(ResumeDocument, row.id)) == (mode == 'add')
        assert not db.get(ImportTask, tid).text


@pytest.mark.parametrize('mode,text', [('edit', 'too short'), ('edit', ' ' * 40), ('add', 'x'), ('add', 'x' * 20000)])
def test_invalid_edit_does_not_destroy_original(client, mode, text):
    tid, _ = candidate_import(client)
    deliver_import(client.app.state.factory, lambda _: SourceResult(RESUME, 'yandex', 'Synthetic'))
    send(client, 300, '/import-' + mode + ' ' + tid, 'size-0')
    send(client, 300, text, 'size-1')
    with client.app.state.factory() as db:
        task = db.get(ImportTask, tid)
        assert task.text == RESUME
        assert db.get(BotSession, task.user_id).state['step'] == 'import_editing'
        assert db.scalar(select(Application)) is None
    send(client, 300, '/import-preview ' + tid, 'size-2')
    send(client, 300, '/import-confirm ' + tid, 'size-3')
    with client.app.state.factory() as db:
        assert db.scalar(select(Application)).resume == RESUME


def test_foreign_edit_expiry_and_cancel_cannot_send(client):
    tid, _ = candidate_import(client)
    deliver_import(client.app.state.factory, lambda _: SourceResult(RESUME, 'yandex', 'Synthetic'))
    send(client, 301, '/import-edit ' + tid, 'foreign-0')
    send(client, 301, 'A completely different synthetic profile with no real candidate data.', 'foreign-1')
    send(client, 300, '/import-edit ' + tid, 'foreign-2')
    with client.app.state.factory() as db:
        assert db.get(ImportTask, tid).text == RESUME
        db.get(ImportTask, tid).expires_at = now() - timedelta(seconds=1)
        db.commit()
    send(client, 300, 'A completely different synthetic profile with no real candidate data.', 'expired-0')
    send(client, 300, '/import-confirm ' + tid, 'expired-1')
    send(client, 300, '/cancel', 'expired-2')
    with client.app.state.factory() as db:
        task = db.get(ImportTask, tid)
        assert not task.text and task.status == 'cancelled'
        assert db.scalar(select(Application)) is None


def test_opening_vacancy_again_clears_abandoned_import(client):
    tid, jid = candidate_import(client)
    deliver_import(client.app.state.factory, lambda _: SourceResult(RESUME, 'yandex', 'Synthetic'))
    send(client, 300, '/start apply_' + jid, 'new-flow')
    with client.app.state.factory() as db:
        task = db.get(ImportTask, tid)
        assert task.status == 'cancelled' and not task.text
        assert db.get(BotSession, task.user_id).state['step'] == 'consent'
