"""MAX private-dialog logic; no network calls in the event transaction."""
import hashlib
import json
from fastapi import HTTPException
from sqlalchemy import select
from .chat_ui import PAGE_SIZE, consume_action, page_number, queue_message
from .db import Application, BotEvent, BotSession, Job, Outbox, SandboxSwitch, User, serialize_writes
from .employer_bot import handle_employer
from .demo_bot import handle_demo
from .outbox import start_worker
from .services import answer, confirm, submit, withdraw_application
from .sandbox import PERSONAS, actor

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
    if config.sandbox and max_id not in config.sandbox_users:
        return
    if db.get(BotEvent, digest):
        return
    db.add(BotEvent(id=digest))
    db.flush()
    if text == '/whoami':
        db.add(Outbox(max_id=max_id, body={'text': 'Ваш MAX ID для настройки локального теста: ' + max_id}))
        return
    if config.sandbox:
        switch = db.get(SandboxSwitch, max_id)
        if not switch:
            if kind != 'message_created' or text != '/test':
                return  # No replay of old production messages into a new sandbox.
            switch = SandboxSwitch(max_id=max_id, persona='e')
            db.add(switch)
        user, session = actor(db, max_id, switch.persona)
    else:
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

    def ask_question(app, prefix=''):
        pending = [q for q in app.questions if not app.answers.get(q['id'])]
        session.state = {'step': 'answer', 'application_id': app.id, 'question_id': pending[0]['id']}
        number = len(app.questions) - len(pending) + 1
        choices = [] if pending[0].get('kind') in ('screening', 'assessment') else [('Опыта нет', 'Опыта нет.')]
        reply(f'{prefix}Уточнение {number} из {len(app.questions)}\n\n{pending[0]["text"]}\n\nМожно ответить текстом или выбрать кнопку. Пропуск не означает отсутствия опыта.',
              choices + [('Пропустить вопрос', 'Пропускаю уточнение, сведений недостаточно.'), ('Продолжить позже', '/pause'), ('Условия вакансии', '/vacancy ' + app.job_id)],
              application_id=app.id, bind=True)

    if kind == 'message_callback':
        # MAX rejects an empty acknowledgement with proto.payload (HTTP 400).
        # A neutral notification also covers stale/foreign-role buttons without
        # claiming the underlying action succeeded or changing the old message.
        db.add(Outbox(max_id=max_id, callback_id=callback['callback_id'],
                      body={'notification': 'Обрабатываю нажатие…'}))
        text = consume_action(db, user, session, callback.get('payload'))
        if text is None:
            reply('Кнопка устарела или уже использована. Откройте меню и повторите действие.')
            return
    state = dict(session.state)
    command = text.split(maxsplit=1)[0] if text else ''
    if config.sandbox and command == '/test':
        selected = text.partition(' ')[2].strip()
        if selected:
            if selected not in PERSONAS:
                reply('Неизвестная тестовая роль.')
                return
            switch.persona = selected
            user, session = actor(db, max_id, selected)
        role_buttons = [(label, '/test ' + key) for key, (label, _, _) in PERSONAS.items() if key != switch.persona]
        reply('Изолированный тест: один MAX-аккаунт, разные пользователи бэкенда.\n'
              'Используйте только вымышленные данные. Вакансии и отклики сохраняются в отдельной базе.\n'
              'Черновики каждой роли сохраняются. Старые кнопки другой роли не сработают.\n\n'
              'Текущий шаг: ' + str(session.state.get('step', 'меню')),
              [('Меню текущей роли', '/help')] + role_buttons)
        return
    if config.sandbox and command == '/employer':
        reply('В тесте выберите готовую роль работодателя через «Тестовые роли». Настоящая регистрация проверяется вне тестового режима.')
        return
    if handle_demo(session, text, reply):
        return
    state = dict(session.state)
    if command == '/pause':
        app = db.get(Application, state.get('application_id')) if state.get('application_id') else None
        if user.role == 'candidate' and app and app.user_id == user.id and app.status == 'clarifying':
            session.state = {}
            reply('Пауза. Ответы сохранены. Возвращайтесь в удобное время - повторять их не нужно.',
                  [('Продолжить', '/continue ' + app.id), ('Мои отклики', '/status')], application_id=app.id)
        else:
            reply('Для продолжения отклика откройте «Мои отклики».', [('Мои отклики', '/status')])
        return
    if command == '/vacancy':
        job = db.get(Job, text.partition(' ')[2].strip())
        if not job:
            reply('Вакансия не найдена.')
        else:
            reply(f'{job.title} · {job.company}\n\n{job.description[:2200]}\n\nУсловия: {job.terms or "Уточните у работодателя"}\n\nЭто исходное описание работодателя. Текущий вопрос и ответы сохранены.',
                  [('Мои отклики', '/status')])
        return
    if command == '/cancel':
        from .imports import cancel_import
        cancel_import(db, session)
        session.state = {}
        reply('Текущий шаг отменён. Сохранённые вакансии и отклики не изменились.')
        return
    if text in ('/privacy', '/help', '/start') or kind == 'bot_started' and not event.get('payload'):
        if text == '/privacy':
            reply('Тестовая версия: используйте вымышленные данные. Компания из вакансии увидит имя, опыт и ответы. MAX ID нужен для уведомлений. Сведения хранятся на сервере бота, во внешнюю языковую модель не передаются. Решение принимает человек. Не присылайте паспорт и чувствительные сведения. Отзыв через «Мои отклики» очищает тексты в базе откликов и серверной очереди. Уже доставленные сообщения MAX и резервные копии этим не удаляются. Сроки и очистка тестового стенда описаны в регламенте проекта.', [('Мои отклики', '/status'), ('Меню', '/help')])
        elif user.role == 'employer':
            reply(f'РезюмИТ Найм · {user.company}\nСоздайте вакансию, проверьте требования и отправьте кандидатам ссылку. Решение о приглашении принимаете вы.', [('Новая вакансия', '/newjob'), ('Мои вакансии', '/jobs'), ('Сводка откликов', '/metrics'), ('Библиотека тестов', '/tests'), ('Учебный сценарий', '/demo'), ('Обработка данных', '/privacy'), ('Отмена шага', '/cancel')])
        else:
            reply('РезюмИТ Найм · помощник первичного отбора\nКандидату: откройте ссылку вакансии от работодателя.\nРаботодателю: войдите по коду.\nТестовая версия - используйте вымышленные сведения.', [('Я работодатель', '/employer'), ('Мои отклики', '/status'), ('Учебный сценарий', '/demo'), ('Обработка данных', '/privacy'), ('Отмена шага', '/cancel')])
        return
    known_commands = {'/employer', '/newjob', '/jobs', '/job', '/candidates', '/close', '/open', '/view', '/invite', '/resume', '/evidence', '/metrics', '/start', '/status', '/application', '/confirm', '/withdraw', '/continue', '/screening', '/screening-on', '/screening-off', '/screening-answers'}
    known_commands.update({'/tests', '/test-template', '/newtest', '/archive-test', '/job-test', '/use-test', '/test-answers'})
    known_commands.update({'/import-preview', '/import-confirm'})
    if command.startswith('/') and command not in known_commands:
        reply('Команда не распознана. Откройте меню или продолжите текущий шаг обычным сообщением.')
        return
    from .imports import handle_import
    if handle_import(db, user, session, text, reply, ask_question):
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
            buttons.append(('⬅️ Назад', f'/status {page - 1}'))
        if len(apps) > PAGE_SIZE:
            buttons.append(('Далее ➡️', f'/status {page + 1}'))
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
                ask_question(app, 'Продолжаем с сохранённого шага.\n\n')
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
            reply('Пришлите текст резюме (40-20 000 символов) или публичную ссылку на текстовый PDF в Яндекс Диске / Google Drive. Можно прислать GitHub-профиль или репозиторий. Ссылки сначала покажем для проверки. Сканы и закрытые файлы не поддерживаются. Не включайте паспортные данные.')
    elif state.get('step') == 'resume':
        if text.startswith(('https://', 'http://')) and '\n' not in text and ' ' not in text:
            from .imports import queue_import
            from .sources import SourceError
            try:
                queue_import(db, user, session, text)
                reply('Читаю публичный источник. Затем покажу текст для проверки.', [('Отмена', '/cancel')])
            except SourceError as exc:
                reply(str(exc))
        elif not 40 <= len(text) <= 20000 or text.startswith('/'):
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
                    ask_question(app, 'Отклик сохранён.\n\n')
                else:
                    session.state = {}
                    reply('Отклик сохранён и доступен работодателю.', [('Мой отклик', '/application ' + app.id)], application_id=app.id)
    elif state.get('step') in ('import_waiting', 'import_ready'):
        reply('Дождитесь результата и проверьте текст. Чтобы вставить свой текст, отмените импорт и снова откройте вакансию.', [('Отмена', '/cancel')])
    elif state.get('step') == 'answer':
        app = db.get(Application, state.get('application_id'))
        if not app or app.status != 'clarifying':
            session.state = {}
            reply('Уточнения завершены.', [('Мои отклики', '/status')])
        elif not 2 <= len(text) <= 2500 or text.startswith('/'):
            reply('Ответ: 2-2500 символов. Если опыта нет, так и напишите.')
        else:
            pending = [q for q in app.questions if not app.answers.get(q['id'])]
            if pending:
                answer(db, app, {pending[0]['id']: text})
            pending = [q for q in app.questions if not app.answers.get(q['id'])]
            if pending:
                ask_question(app, 'Ответ сохранён.\n\n')
            else:
                session.state = {}
                reply('Спасибо! Ответы сохранены, уведомление работодателю поставлено в очередь.', [('Мои отклики', '/status')], application_id=app.id)
    else:
        reply('Откройте ссылку вакансии от работодателя или выберите действие в меню.')


def process_event(factory, event, config):
    if not valid_event(event):
        return
    if config.sandbox:
        config.validate()
    with factory() as db:
        if config.sandbox and not (db.bind.url.database or '').endswith('.sandbox.db'):
            raise ValueError('Refusing to route sandbox events to normal storage')
        serialize_writes(db)
        handle_update(db, event, config)
        # Unexpected integrity/storage errors fail the webhook so MAX can retry.
        db.commit()
