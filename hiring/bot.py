"""MAX private-dialog logic; no network calls in the event transaction."""
import hashlib
import json
from fastapi import HTTPException
from sqlalchemy import select
from .chat_ui import PAGE_SIZE, consume_action, page_number, queue_message
from .db import Application, BotEvent, BotSession, Job, Outbox, User, serialize_writes
from .employer_bot import handle_employer
from .outbox import start_worker
from .services import answer, confirm, submit, withdraw_application

STATUS = {'clarifying': 'ждём уточнений', 'ready': 'у работодателя', 'invited': 'приглашение',
          'confirmed': 'интерес подтверждён', 'withdrawn': 'отозван'}


def valid_event(event):
    if not isinstance(event, dict) or event.get('update_type') not in ('bot_started', 'message_created', 'message_callback'):
        return None
    kind = event['update_type']
    msg, callback = event.get('message') or {}, event.get('callback') or {}
    if not isinstance(msg, dict) or not isinstance(callback, dict):
        return None
    body, recipient = msg.get('body', {}), msg.get('recipient') or {}
    if not isinstance(body, dict) or not isinstance(recipient, dict) or recipient.get('chat_type', 'dialog') != 'dialog':
        return None
    identity = event.get('user') if kind == 'bot_started' else (callback.get('user') if kind == 'message_callback' else msg.get('sender'))
    if not isinstance(identity, dict) or identity.get('is_bot'):
        return None
    uid = identity.get('user_id', identity.get('id'))
    if not isinstance(uid, int) or isinstance(uid, bool) or not 0 < uid < 2**63:
        return None
    content = body.get('text') or ''
    if not isinstance(content, str) or len(content) > 20000:
        return None
    key = (body.get('mid') if kind == 'message_created' else callback.get('callback_id')) if kind != 'bot_started' else json.dumps([event.get('timestamp'), event.get('payload')], ensure_ascii=False)
    if not isinstance(key, str) or not 1 <= len(key) <= 256:
        return None
    digest = hashlib.sha256(f'{kind}:{uid}:{key}'.encode()).hexdigest()
    return kind, identity, str(uid), content.strip(), callback, digest


def handle_update(db, event, config):
    parsed = valid_event(event)
    if not parsed:
        return
    kind, identity, max_id, text, callback, digest = parsed
    if db.get(BotEvent, digest):
        return
    db.add(BotEvent(id=digest))
    db.flush()
    user = db.scalar(select(User).where(User.max_id == max_id))
    if not user:
        name = identity.get('name') or identity.get('first_name') or 'Кандидат'
        user = User(max_id=max_id, name=name[:160] if isinstance(name, str) else 'Кандидат', role='candidate')
        db.add(user)
        db.flush()
    session = db.get(BotSession, user.id)
    if not session:
        session = BotSession(user_id=user.id, state={})
        db.add(session)

    def reply(value, buttons=None, application_id=None, bind=False):
        queue_message(db, user, value, buttons or [('Меню', '/help'), ('Отмена', '/cancel')],
                      application_id, dict(session.state) if bind else None)

    if kind == 'message_callback':
        db.add(Outbox(max_id=max_id, callback_id=callback['callback_id'], body={}))
        text = consume_action(db, user, session, callback.get('payload'))
        if text is None:
            reply('Кнопка устарела или уже использована. Откройте меню и повторите действие.')
            return
    state = dict(session.state)
    command = text.split(maxsplit=1)[0] if text else ''
    if command == '/cancel':
        session.state = {}
        reply('Текущий шаг отменён. Сохранённые вакансии и отклики не изменились.')
        return
    if text in ('/privacy', '/help', '/start') or kind == 'bot_started' and not event.get('payload'):
        if text == '/privacy':
            reply('Тестовая версия: используйте вымышленные данные. Компания из вакансии увидит имя, опыт и ответы. MAX ID нужен для уведомлений. Сведения хранятся на сервере бота, во внешнюю языковую модель не передаются. Решение принимает человек. Не присылайте паспорт и чувствительные сведения. Отзыв через «Мои отклики» очищает тексты в базе откликов и серверной очереди. Уже доставленные сообщения MAX и резервные копии этим не удаляются. Сроки и очистка тестового стенда описаны в регламенте проекта.', [('Мои отклики', '/status'), ('Меню', '/help')])
        elif user.role == 'employer':
            reply(f'РезюмИТ Найм · {user.company}\nСоздайте вакансию, проверьте требования и отправьте кандидатам ссылку. Решение о приглашении принимаете вы.', [('Новая вакансия', '/newjob'), ('Мои вакансии', '/jobs'), ('Обработка данных', '/privacy'), ('Отмена шага', '/cancel')])
        else:
            reply('РезюмИТ Найм · помощник первичного отбора\nКандидату: откройте ссылку вакансии от работодателя.\nРаботодателю: войдите по коду.\nТестовая версия — используйте вымышленные сведения.', [('Я работодатель', '/employer'), ('Мои отклики', '/status'), ('Обработка данных', '/privacy'), ('Отмена шага', '/cancel')])
        return
    if handle_employer(db, user, session, text, config, reply):
        return
    payload = event.get('payload') if kind == 'bot_started' else (text.partition(' ')[2] if command == '/start' else '')
    if isinstance(payload, str) and payload.startswith('apply_'):
        job = db.get(Job, payload[6:])
        if not job or not job.active:
            reply('Эта вакансия недоступна. Попросите работодателя прислать актуальную ссылку.')
        else:
            existing = db.scalar(select(Application).where(Application.user_id == user.id, Application.job_id == job.id))
            if existing:
                reply('Отклик уже сохранён. Откройте его, чтобы продолжить.', [('Мой отклик', '/application ' + existing.id)])
            else:
                session.state = {'step': 'consent', 'job_id': job.id}
                reply(f'{job.title} · {job.company}\n{job.terms}\n\n{job.description[:1500]}\n\nРаботодатель получит ваше имя, опыт и ответы для рассмотрения отклика. Автоматического решения о найме нет. Для теста используйте вымышленные сведения.', [('Согласен, продолжить', 'Согласен'), ('Обработка данных', '/privacy'), ('Отмена', '/cancel')], bind=True)
    elif command == '/status':
        page = page_number(text.partition(' ')[2])
        apps = list(db.scalars(select(Application).where(Application.user_id == user.id).order_by(Application.created_at.desc(), Application.id).offset(page * PAGE_SIZE).limit(PAGE_SIZE + 1)))
        buttons = [(f'{db.get(Job, a.job_id).title} · {STATUS[a.status]}', '/application ' + a.id) for a in apps[:PAGE_SIZE]]
        if page:
            buttons.append(('← Назад', f'/status {page - 1}'))
        if len(apps) > PAGE_SIZE:
            buttons.append(('Далее →', f'/status {page + 1}'))
        reply(f'Мои отклики · страница {page + 1}' if apps else 'Откликов здесь пока нет. Откройте ссылку вакансии.', buttons + [('Меню', '/help')])
    elif command in ('/application', '/confirm', '/withdraw', '/continue'):
        app = db.get(Application, text.partition(' ')[2].strip())
        if not app or app.user_id != user.id:
            reply('Отклик не найден.')
            return
        if command == '/withdraw':
            if app.status == 'withdrawn':
                reply('Отклик уже отозван.', [('Мои отклики', '/status')])
                return
            session.state = {'step': 'withdraw_confirm', 'application_id': app.id}
            reply('Отозвать отклик и очистить резюме и ответы на сервере? Уже доставленные сообщения MAX останутся.', [('Отозвать', 'Отозвать'), ('Сохранить отклик', '/cancel')], application_id=app.id, bind=True)
        elif command == '/confirm':
            try:
                confirm(db, app)
                reply('Интерес подтверждён. Уведомление поставлено в очередь.', [('Мои отклики', '/status')], application_id=app.id)
            except HTTPException as exc:
                reply(str(exc.detail))
        elif command == '/continue' and app.status == 'clarifying':
            pending = [q for q in app.questions if not app.answers.get(q['id'])]
            if pending:
                session.state = {'step': 'answer', 'application_id': app.id}
                reply(pending[0]['text'], application_id=app.id)
        else:
            buttons = []
            if app.status == 'clarifying':
                buttons.append(('Продолжить уточнения', '/continue ' + app.id))
            if app.status == 'invited':
                buttons.append(('Подтвердить интерес', '/confirm ' + app.id))
            if app.status != 'withdrawn':
                buttons.append(('Отозвать отклик', '/withdraw ' + app.id))
            reply(f"{db.get(Job, app.job_id).title}\n{STATUS[app.status]}\n{app.invitation}", buttons + [('Мои отклики', '/status')], application_id=app.id)
    elif state.get('step') == 'withdraw_confirm':
        if text.lower() != 'отозвать':
            reply('Выберите «Отозвать» или отмените действие.')
        else:
            app = db.get(Application, state['application_id'])
            if app and app.user_id == user.id:
                withdraw_application(db, app)
            session.state = {}
            reply('Отклик отозван. Тексты очищены из базы откликов и очереди; ожидающие отправки отменены. Уже доставленные сообщения MAX и резервные копии этим не удаляются.', [('Мои отклики', '/status')])
    elif state.get('step') == 'consent':
        if text.lower() not in ('согласен', 'согласна', 'да'):
            reply('Для отправки отклика нужно согласие.', [('Согласен, продолжить', 'Согласен'), ('Отмена', '/cancel')], bind=True)
        else:
            session.state = {**state, 'step': 'resume'}
            reply('Пришлите текст резюме или опишите опыт и проекты одним сообщением: 40–20 000 символов. Файлы и сканы пока не принимаются — скопируйте текст из документа. Не включайте паспортные данные.')
    elif state.get('step') == 'resume':
        if not 40 <= len(text) <= 20000 or text.startswith('/'):
            reply('Нужен текст от 40 до 20 000 символов. Если отправили файл, скопируйте из него текст.')
        else:
            job = db.get(Job, state['job_id'])
            if not job or not job.active:
                session.state = {}
                reply('Приём откликов завершён.')
            else:
                try:
                    app = submit(db, user, job, text)
                except HTTPException as exc:
                    session.state = {}
                    reply(str(exc.detail))
                    return
                pending = [q for q in app.questions if not app.answers.get(q['id'])]
                if pending and app.status == 'clarifying':
                    session.state = {'step': 'answer', 'application_id': app.id}
                    reply('Отклик сохранён. Уточним детали.\n\n' + pending[0]['text'], application_id=app.id)
                else:
                    session.state = {}
                    reply('Отклик сохранён и доступен работодателю.', [('Мой отклик', '/application ' + app.id)], application_id=app.id)
    elif state.get('step') == 'answer':
        app = db.get(Application, state.get('application_id'))
        if not app or app.status != 'clarifying':
            session.state = {}
            reply('Уточнения завершены.', [('Мои отклики', '/status')])
        elif not 2 <= len(text) <= 2500 or text.startswith('/'):
            reply('Ответ: 2–2500 символов. Если опыта нет, так и напишите.')
        else:
            pending = [q for q in app.questions if not app.answers.get(q['id'])]
            if pending:
                answer(db, app, {pending[0]['id']: text})
            pending = [q for q in app.questions if not app.answers.get(q['id'])]
            if pending:
                reply(pending[0]['text'], application_id=app.id)
            else:
                session.state = {}
                reply('Спасибо! Ответы сохранены, уведомление работодателю поставлено в очередь.', [('Мои отклики', '/status')], application_id=app.id)
    else:
        reply('Откройте ссылку вакансии от работодателя или выберите действие в меню.')


def process_event(factory, event, config):
    if not valid_event(event):
        return
    with factory() as db:
        serialize_writes(db)
        handle_update(db, event, config)
        # Unexpected integrity/storage errors fail the webhook so MAX can retry.
        db.commit()
