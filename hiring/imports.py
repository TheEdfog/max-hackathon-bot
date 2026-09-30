"""Durable MAX imports. Fetches happen outside the event/SQLite write transaction."""
from datetime import timedelta, timezone
from fastapi import HTTPException
from sqlalchemy import func, or_, select
from .chat_ui import page_number, queue_message
from .db import Application, BotSession, ImportTask, Job, Outbox, User, now, serialize_writes
from .services import submit
from .sources import SourceError, import_max_attachment, validate_max_pdf
from .talent_api import save_pdf


def clear_previews(db, task):
    for row in db.scalars(select(Outbox).where(Outbox.import_id == task.id)):
        row.body = {}
        if row.status in ('pending', 'failed'):
            row.status = 'cancelled'


def wipe(db, task, status='cancelled'):
    task.url, task.source_size, task.text, task.pdf, task.status = '', 0, '', None, status
    clear_previews(db, task)


def preview_buttons(task):
    return [('Прочитать полностью', '/import-preview ' + task.id),
            ('Добавить о своём опыте', '/import-add ' + task.id),
            ('Заменить текст', '/import-edit ' + task.id),
            ('Использовать для отклика', '/import-confirm ' + task.id), ('Отмена', '/cancel')]


def show_preview(db, user, session, task, notice):
    queue_message(db, user, notice + '\n\n' + task.text[:1800] +
                  '\n\nДо подтверждения отклик не отправлен. Черновик хранится не дольше 24 часов.',
                  preview_buttons(task), state=dict(session.state), import_id=task.id)


def cancel_import(db, session):
    task = db.get(ImportTask, session.state.get('import_id')) if session.state.get('import_id') else None
    if task and task.user_id == session.user_id:
        wipe(db, task)


def _queue(db, user, session, url, provider='', source_size=0):
    count = db.scalar(select(func.count()).select_from(ImportTask).where(
        ImportTask.user_id == user.id, ImportTask.created_at > now() - timedelta(hours=1)))
    pending = db.scalar(select(func.count()).select_from(ImportTask).where(ImportTask.status.in_(['pending', 'working', 'ready'])))
    if count >= 5 or pending >= 100:
        raise SourceError('Лимит импорта: 5 PDF-файлов в час на пользователя. Вставьте текст или попробуйте позже.')
    task = ImportTask(user_id=user.id, job_id=session.state['job_id'], url=url,
                      source_size=source_size, provider=provider,
                      expires_at=now() + timedelta(hours=24))
    db.add(task)
    db.flush()
    session.state = {'step': 'import_waiting', 'job_id': task.job_id, 'import_id': task.id}
    return task


def queue_max_attachment(db, user, session, url, filename, size):
    validate_max_pdf(filename, size, url)
    return _queue(db, user, session, url, provider='max_pending', source_size=size)


def deliver_import(factory, fetch_attachment=import_max_attachment):
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
        identifier, url, provider, source_size = task.id, task.url, task.provider, task.source_size
        db.commit()
    # No open write transaction during untrusted network access / PDF extraction.
    try:
        if provider != 'max_pending':
            raise SourceError('Импорт по ссылкам отключён. Прикрепите PDF кнопкой MAX.')
        result = fetch_attachment(url, source_size)
        error = None
    except SourceError as exc:
        result, error = None, str(exc)
    except Exception:
        result, error = None, 'Не удалось обработать PDF из MAX. Прикрепите файл ещё раз или вставьте текст.'
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
            task.text, task.pdf, task.provider, task.status, task.url, task.source_size = result.text, result.pdf, result.provider, 'ready', '', 0
            session.state = {'step': 'import_ready', 'job_id': task.job_id, 'import_id': task.id, 'import_revision': 0}
            show_preview(db, user, session, task, 'Текст получен. ' + result.warning)
        db.commit()
    return True


def handle_import(db, user, session, text, reply, ask_question, after_submit=None):
    command, _, rest = text.partition(' ')
    editing = session.state.get('step') == 'import_editing' and not text.startswith('/')
    if not editing and command not in ('/import-preview', '/import-confirm', '/import-edit', '/import-add'):
        return False
    parts = rest.split()
    identifier = session.state.get('import_id') if editing else (parts[0] if parts else None)
    task = db.get(ImportTask, identifier) if identifier else None
    if (not task or task.user_id != user.id or task.status != 'ready'
            or session.state.get('import_id') != task.id or task.expires_at.replace(tzinfo=timezone.utc) <= now()):
        reply('Черновик недоступен или устарел. Откройте вакансию и отправьте резюме заново.')
        return True
    if editing:
        value = text.strip()
        addition = session.state.get('import_mode') == 'add'
        revised = task.text + '\n\nДополнение кандидата:\n' + value if addition else value
        if len(value) < (2 if addition else 40) or len(revised) > 20000:
            reply('Дополнение: от 2 символов; полный текст: 40-20 000 символов. Вместе с исходным текстом должно быть не больше 20 000.',
                  [('Вернуться без изменений', '/import-preview ' + task.id), ('Отмена', '/cancel')], bind=True)
            return True
        clear_previews(db, task)
        task.text = revised
        if not addition:
            # A replacement may remove personal data. Never retain the original
            # PDF for the employer after the candidate has replaced its text.
            task.pdf = None
        session.state = {'step': 'import_ready', 'job_id': task.job_id, 'import_id': task.id,
                         'import_revision': session.state.get('import_revision', 0) + 1}
        show_preview(db, user, session, task, 'Текст обновлён. ' +
                     ('Исходный PDF остаётся приложением.' if task.pdf else 'Отклик будет отправлен без PDF-приложения.'))
        return True
    if command in ('/import-edit', '/import-add'):
        session.state = {**session.state, 'step': 'import_editing',
                         'import_mode': 'add' if command == '/import-add' else 'replace'}
        reply(('Пришлите дополнение одним сообщением: что именно сделали вы, какие задачи решали. Исходный текст останется; PDF, если был, тоже.'
               if command == '/import-add' else
               'Пришлите полный исправленный текст одним сообщением (40-20 000 символов). Он заменит импортированный текст. Исходный PDF не будет передан работодателю.'),
              [('Вернуться без изменений', '/import-preview ' + task.id), ('Отмена', '/cancel')], bind=True)
        return True
    if command == '/import-preview':
        session.state = {k: v for k, v in {**session.state, 'step': 'import_ready'}.items() if k != 'import_mode'}
        count = max(1, (len(task.text) + 2999) // 3000)
        page = min(page_number(parts[1] if len(parts) > 1 else 0), count - 1)
        buttons = preview_buttons(task)[1:]
        if page:
            buttons.append(('⬅️ Назад', f'/import-preview {task.id} {page - 1}'))
        if page + 1 < count:
            buttons.append(('Далее ➡️', f'/import-preview {task.id} {page + 1}'))
        queue_message(db, user, f'Текст · {page + 1}/{count}\n\n' + task.text[page * 3000:(page + 1) * 3000],
                      buttons, state=dict(session.state), import_id=task.id)
        return True
    if session.state.get('step') != 'import_ready':
        reply('Сначала сохраните изменения или вернитесь к исходному тексту.',
              [('Вернуться без изменений', '/import-preview ' + task.id), ('Отмена', '/cancel')], bind=True)
        return True
    try:
        existing = db.scalar(select(Application).where(Application.job_id == task.job_id, Application.user_id == user.id))
        row = submit(db, user, db.get(Job, task.job_id), task.text)
        if task.pdf and not existing:
            save_pdf(db, row, task.pdf, task.provider)
        wipe(db, task, 'used')
        if after_submit:
            after_submit(row)
        elif row.status == 'clarifying':
            ask_question(row, 'Отклик сохранён.\n\n')
        else:
            session.state = {}
            reply('Отклик сохранён.', [('Мой отклик', '/application ' + row.id)], application_id=row.id)
    except HTTPException as exc:
        wipe(db, task)
        session.state = {}
        reply(str(exc.detail))
    return True
