"""Durable MAX imports. Fetches happen outside the event/SQLite write transaction."""
from datetime import timedelta, timezone
from fastapi import HTTPException
from sqlalchemy import func, or_, select
from .chat_ui import page_number, queue_message
from .db import Application, BotSession, ImportTask, Job, Outbox, User, now, serialize_writes
from .services import submit
from .sources import SourceError, import_source, source_kind
from .talent_api import save_pdf


def wipe(db, task, status='cancelled'):
    task.url, task.text, task.pdf, task.status = '', '', None, status
    for row in db.scalars(select(Outbox).where(Outbox.import_id == task.id)):
        row.body = {}
        if row.status in ('pending', 'failed'):
            row.status = 'cancelled'


def cancel_import(db, session):
    task = db.get(ImportTask, session.state.get('import_id')) if session.state.get('import_id') else None
    if task and task.user_id == session.user_id:
        wipe(db, task)


def queue_import(db, user, session, url):
    source_kind(url)  # Reject arbitrary URLs before storing or contacting anything.
    count = db.scalar(select(func.count()).select_from(ImportTask).where(
        ImportTask.user_id == user.id, ImportTask.created_at > now() - timedelta(hours=1)))
    pending = db.scalar(select(func.count()).select_from(ImportTask).where(ImportTask.status.in_(['pending', 'working', 'ready'])))
    if count >= 5 or pending >= 100:
        raise SourceError('Лимит импорта: 5 ссылок в час на пользователя. Вставьте текст вручную или попробуйте позже.')
    task = ImportTask(user_id=user.id, job_id=session.state['job_id'], url=url, expires_at=now() + timedelta(hours=24))
    db.add(task)
    db.flush()
    session.state = {'step': 'import_waiting', 'job_id': task.job_id, 'import_id': task.id}
    return task


def deliver_import(factory, fetch=import_source):
    with factory() as db:
        serialize_writes(db)
        # TTL also applies after restarts, including pending outbox previews.
        for old in db.scalars(select(ImportTask).where(ImportTask.expires_at <= now())):
            wipe(db, old)
            session = db.get(BotSession, old.user_id)
            if session and session.state.get('import_id') == old.id:
                session.state = {'step': 'resume', 'job_id': old.job_id}
            db.delete(old)
        task = db.scalar(select(ImportTask).where(ImportTask.expires_at > now(), or_(
            ImportTask.status == 'pending', (ImportTask.status == 'working') & (ImportTask.lease_until < now()))).order_by(ImportTask.created_at).limit(1))
        if not task:
            db.commit()
            return False
        task.status, task.lease_until = 'working', now() + timedelta(minutes=2)
        identifier, url = task.id, task.url
        db.commit()
    # No open write transaction during untrusted network access / PDF extraction.
    try:
        result, error = fetch(url), None
    except SourceError as exc:
        result, error = None, str(exc)
    except Exception:
        result, error = None, 'Источник временно недоступен. Вставьте текст или повторите позже.'
    with factory() as db:
        serialize_writes(db)
        task = db.get(ImportTask, identifier)
        if not task or task.status != 'working':
            return True
        session = db.get(BotSession, task.user_id)
        user = db.get(User, task.user_id)
        job = db.get(Job, task.job_id)
        if (not session or session.state.get('import_id') != task.id or not job or not job.active
                or task.expires_at.replace(tzinfo=timezone.utc) <= now()):
            wipe(db, task)
        elif error:
            wipe(db, task, 'failed')
            session.state = {'step': 'resume', 'job_id': task.job_id}
            queue_message(db, user, error, [('Отмена', '/cancel')])
        else:
            task.text, task.pdf, task.provider, task.status, task.url = result.text, result.pdf, result.provider, 'ready', ''
            session.state = {'step': 'import_ready', 'job_id': task.job_id, 'import_id': task.id}
            queue_message(db, user, 'Текст получен. ' + result.warning + '\n\n' + task.text[:1800] +
                          '\n\nДо подтверждения отклик не отправлен. Черновик хранится не дольше 24 часов.',
                          [('Прочитать полностью', '/import-preview ' + task.id),
                           ('Использовать для отклика', '/import-confirm ' + task.id), ('Отмена', '/cancel')],
                          state=dict(session.state), import_id=task.id)
        db.commit()
    return True


def handle_import(db, user, session, text, reply, ask_question):
    command, _, rest = text.partition(' ')
    if command not in ('/import-preview', '/import-confirm'):
        return False
    parts = rest.split()
    task = db.get(ImportTask, parts[0]) if parts else None
    if (not task or task.user_id != user.id or task.status != 'ready'
            or session.state.get('import_id') != task.id or task.expires_at.replace(tzinfo=timezone.utc) <= now()):
        reply('Черновик недоступен или устарел. Откройте вакансию и отправьте ссылку заново.')
        return True
    if command == '/import-preview':
        count = max(1, (len(task.text) + 2999) // 3000)
        page = min(page_number(parts[1] if len(parts) > 1 else 0), count - 1)
        buttons = [('Использовать для отклика', '/import-confirm ' + task.id), ('Отмена', '/cancel')]
        if page:
            buttons.append(('⬅️ Назад', f'/import-preview {task.id} {page - 1}'))
        if page + 1 < count:
            buttons.append(('Далее ➡️', f'/import-preview {task.id} {page + 1}'))
        queue_message(db, user, f'Текст · {page + 1}/{count}\n\n' + task.text[page * 3000:(page + 1) * 3000],
                      buttons, state=dict(session.state), import_id=task.id)
        return True
    try:
        existing = db.scalar(select(Application).where(Application.job_id == task.job_id, Application.user_id == user.id))
        row = submit(db, user, db.get(Job, task.job_id), task.text)
        if task.pdf and not existing:
            save_pdf(db, row, task.pdf, task.provider)
        wipe(db, task, 'used')
        if row.status == 'clarifying':
            ask_question(row, 'Отклик сохранён.\n\n')
        else:
            session.state = {}
            reply('Отклик сохранён.', [('Мой отклик', '/application ' + row.id)], application_id=row.id)
    except HTTPException as exc:
        wipe(db, task)
        session.state = {}
        reply(str(exc.detail))
    return True
