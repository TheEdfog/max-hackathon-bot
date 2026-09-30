"""MAX private-dialog logic; no network calls in the event transaction."""
import hashlib
import json
from fastapi import HTTPException
from sqlalchemy import select
from .chat_ui import PAGE_SIZE, consume_action, page_number, queue_message
from .db import AIReview, Application, BotEvent, BotSession, Job, Outbox, SandboxSwitch, User, serialize_writes
from .ai_review import (consent_digest, consent_text as ai_consent_text, decline_review,
                        employer_policy, provider_for, request_review, withdraw_review)
from .employer_bot import handle_employer
from .demo_bot import handle_demo
from .outbox import start_worker
from .services import answer, confirm, submit, withdraw_application
from .sandbox import PERSONAS, actor
from .pdf_extract import MAX_BYTES

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
    attachment = None
    attachments = body.get('attachments')
    if attachments is not None:
        if not isinstance(attachments, list) or len(attachments) != 1 or not isinstance(attachments[0], dict):
            attachment = {'kind': 'unsupported'}
        else:
            file = attachments[0]
            payload = file.get('payload') or {}
            filename, size, url = file.get('filename'), file.get('size'), payload.get('url') if isinstance(payload, dict) else None
            valid_filename = (file.get('type') == 'file' and isinstance(filename, str)
                              and len(filename) <= 255 and filename.lower().endswith('.pdf')
                              and not any(ord(char) < 32 for char in filename))
            valid_size = isinstance(size, int) and not isinstance(size, bool) and 1 <= size <= MAX_BYTES
            valid_url = isinstance(url, str) and len(url) <= 2048
            attachment = ({'kind': 'pdf', 'filename': filename, 'size': size, 'url': url}
                          if valid_filename and valid_size and valid_url else {'kind': 'unsupported'})
    key = (body.get('mid') if kind == 'message_created' else callback.get('callback_id')) if kind != 'bot_started' else json.dumps([event.get('timestamp'), event.get('payload')], ensure_ascii=False)
    if not isinstance(key, str) or not 1 <= len(key) <= 256:
        return None
    digest = hashlib.sha256(f'{kind}:{uid}:{key}'.encode()).hexdigest()
    return kind, identity, str(uid), content.strip(), callback, digest, attachment


def handle_update(db, event, config):
    parsed = valid_event(event)
    if not parsed:
        return
    kind, identity, max_id, text, callback, digest, attachment = parsed
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

    def continue_application(app, prefix='Отклик сохранён.'):
        pending = [q for q in app.questions if not app.answers.get(q['id'])]
        if pending and app.status == 'clarifying':
            session.state = {'step': 'answer', 'application_id': app.id}
            ask_question(app, prefix + '\n\n')
        else:
            session.state = {}
            reply(prefix + ' Отклик доступен работодателю.', [('Мой отклик', '/application ' + app.id)], application_id=app.id)

    def show_ai_consent(app, job, settings, notice=''):
        text = ai_consent_text(job, settings)
        session.state = {'step': 'ai_consent', 'application_id': app.id,
                         'provider_id': settings['id'], 'base_url': settings['base_url'],
                         'model': settings['model'], 'notice_version': settings['notice_version'],
                         'consent_hash': consent_digest(text)}
        reply((notice + '\n\n' if notice else '') + text,
              [('Разрешаю ИИ-анализ', f'/ai-consent yes {app.id}'),
               ('Продолжить без ИИ', f'/ai-consent no {app.id}')],
              application_id=app.id, bind=True)

    def offer_ai_consent(app):
        if not config.ai_enabled or user.role != 'candidate' or user.demo or config.sandbox:
            return False
        job = db.get(Job, app.job_id)
        employer = db.get(User, job.owner_id) if job else None
        settings = provider_for(config, employer.company if employer else '')
        previous = db.get(AIReview, app.id)
        if not settings or previous and previous.status in ('pending', 'working', 'completed'):
            return False
        show_ai_consent(app, job, settings)
        return True

    def after_submit(app):
        if offer_ai_consent(app):
            return True
        continue_application(app)
        return False

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
            reply('Работодатель получает сведения, которые вы отправили для отклика. Резюме не передаётся модели без отдельного согласия в отдельном сообщении. Если согласие показано, там указаны модель, оператор и ссылка на полную информацию; без согласия отклик продолжается обычным способом. Решение о найме принимает человек. Не отправляйте паспортные и чувствительные сведения. Отзыв отклика командой /withdraw очищает его резюме, ответы и ИИ-сводку из рабочей базы; уже доставленные сообщения MAX и резервные копии могут сохраниться.', [('Мои отклики', '/status'), ('Меню', '/help')])
        elif user.role == 'employer':
            reply(f'РезюмИТ Найм · {user.company}\nСоздайте вакансию, проверьте требования и отправьте кандидатам ссылку. Решение о приглашении принимаете вы.', [('Новая вакансия', '/newjob'), ('Мои вакансии', '/jobs'), ('Сводка откликов', '/metrics'), ('Библиотека тестов', '/tests'), ('Учебный сценарий', '/demo'), ('Обработка данных', '/privacy'), ('Отмена шага', '/cancel')])
        else:
            reply('РезюмИТ Найм · помощник первичного отбора\nКандидату: откройте ссылку вакансии от работодателя.\nРаботодателю: войдите по коду.\nТестовая версия - используйте вымышленные сведения.', [('Я работодатель', '/employer'), ('Мои отклики', '/status'), ('Учебный сценарий', '/demo'), ('Обработка данных', '/privacy'), ('Отмена шага', '/cancel')])
        return
    known_commands = {'/employer', '/newjob', '/jobs', '/job', '/candidates', '/close', '/open', '/view', '/invite', '/resume', '/evidence', '/ai-review', '/metrics', '/start', '/status', '/application', '/confirm', '/withdraw', '/ai-withdraw', '/continue', '/screening', '/screening-on', '/screening-off', '/screening-answers', '/ai-consent'}
    known_commands.update({'/tests', '/test-template', '/newtest', '/archive-test', '/job-test', '/use-test', '/test-answers'})
    known_commands.update({'/import-preview', '/import-confirm', '/import-edit', '/import-add'})
    if command.startswith('/') and command not in known_commands:
        reply('Команда не распознана. Откройте меню или продолжите текущий шаг обычным сообщением.')
        return
    from .imports import handle_import
    if handle_import(db, user, session, text, reply, ask_question, after_submit):
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
                from .imports import cancel_import
                cancel_import(db, session)
                session.state = {'step': 'consent', 'job_id': job.id}
                employer = db.get(User, job.owner_id)
                notice = employer_policy(config, employer.company if employer else '')
                policy = f'\n\nПолная информация об обработке данных: {notice["notice_url"]}' if notice else ''
                reply(f'{job.title} · {job.company}\n{job.terms}\n\n{job.description[:1500]}\n\nДля отклика работодатель получит ваше имя, резюме и ответы по вакансии. Решение принимает человек. ИИ-анализ, если доступен, запрашивается отдельно и необязателен. Не отправляйте паспортные и чувствительные сведения.{policy}', [('Согласен, продолжить', 'Согласен'), ('Обработка данных', '/privacy'), ('Отмена', '/cancel')], bind=True)
    elif command == '/ai-consent':
        parts = text.split()
        if len(parts) == 2:
            app = db.get(Application, parts[1])
            if not app or app.user_id != user.id or app.status == 'withdrawn':
                reply('Отклик не найден.')
                return
            job = db.get(Job, app.job_id)
            employer = db.get(User, job.owner_id) if job else None
            settings = provider_for(config, employer.company if employer else '')
            previous = db.get(AIReview, app.id)
            if not settings:
                reply('ИИ-анализ сейчас не настроен. Отклик остаётся доступен работодателю.', [('Мой отклик', '/application ' + app.id)])
            elif previous and previous.status in ('pending', 'working', 'completed'):
                reply('Решение по ИИ-анализу уже сохранено.', [('Мой отклик', '/application ' + app.id)])
            else:
                show_ai_consent(app, job, settings)
        elif (len(parts) == 3 and parts[1] in ('yes', 'no')
              and state.get('step') == 'ai_consent' and state.get('application_id') == parts[2]):
            app = db.get(Application, parts[2])
            if not app or app.user_id != user.id or app.status == 'withdrawn':
                session.state = {}
                reply('Отклик не найден.')
                return
            job = db.get(Job, app.job_id)
            employer = db.get(User, job.owner_id) if job else None
            settings = provider_for(config, employer.company if employer else '')
            if not settings:
                session.state = {}
                reply('Настройка ИИ изменилась. Резюме не отправлено, отклик сохранён.', [('Мой отклик', '/application ' + app.id)])
                return
            text = ai_consent_text(job, settings)
            if (state.get('provider_id') != settings['id']
                    or state.get('base_url') != settings['base_url']
                    or state.get('model') != settings['model']
                    or state.get('notice_version') != settings['notice_version']
                    or state.get('consent_hash') != consent_digest(text)):
                show_ai_consent(app, job, settings, 'Настройки обработки обновились. Проверьте условия ещё раз.')
                return
            if parts[1] == 'yes':
                request_review(db, app, user, job, settings, text)
                session.state = {}
                db.flush()
                reply('Согласие записано отдельно. ИИ подготовит черновую сводку; рекрутер получит только результат, а решение останется за человеком.', [('Мой отклик', '/application ' + app.id)], application_id=app.id)
                continue_application(app, 'Согласие записано.')
            else:
                decline_review(db, app, job, settings, text)
                session.state = {}
                continue_application(app, 'Хорошо, резюме останется без ИИ-анализа.')
        else:
            reply('Чтобы выбрать, откройте свой отклик и нажмите отдельную кнопку согласия.', [('Мои отклики', '/status')])
    elif command == '/ai-withdraw':
        app_id = text.partition(' ')[2].strip()
        app = db.get(Application, app_id)
        ai = db.get(AIReview, app_id) if app else None
        if not app or app.user_id != user.id or not ai or ai.decision != 'granted' or ai.status == 'revoked':
            reply('Активного согласия на ИИ-анализ нет.', [('Мои отклики', '/status')])
        else:
            session.state = {'step': 'ai_withdraw_confirm', 'application_id': app.id}
            reply('Отозвать только согласие на ИИ-анализ? Отклик останется у работодателя. Если запрос уже отправлен модели, отменить уже состоявшуюся обработку может быть невозможно.',
                  [('Отозвать согласие', 'Отозвать ИИ'), ('Оставить как есть', '/cancel')], application_id=app.id, bind=True)
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
            ai = db.get(AIReview, app.id)
            if ai and ai.decision == 'granted' and ai.status != 'revoked':
                buttons.append(('Отозвать согласие на ИИ', '/ai-withdraw ' + app.id))
            if app.status != 'withdrawn' and (not ai or ai.status in ('declined', 'revoked', 'failed', 'expired')):
                job = db.get(Job, app.job_id)
                employer = db.get(User, job.owner_id) if job else None
                if provider_for(config, employer.company if employer else ''):
                    buttons.append(('ИИ-анализ (необязательно)', '/ai-consent ' + app.id))
            if app.status != 'withdrawn':
                buttons.append(('Отозвать отклик', '/withdraw ' + app.id))
            ai_status = f'\nИИ-анализ: {ai.status}' if ai else ''
            reply(f"{db.get(Job, app.job_id).title}\n{STATUS[app.status]}{ai_status}\n{app.invitation}", buttons + [('Мои отклики', '/status')], application_id=app.id)
    elif state.get('step') == 'ai_withdraw_confirm':
        if text.lower() != 'отозвать ии':
            session.state = {}
            reply('Отклик оставлен без изменений.', [('Мой отклик', '/application ' + state['application_id'])])
        else:
            app = db.get(Application, state['application_id'])
            if app and app.user_id == user.id:
                withdraw_review(db, app)
            session.state = {}
            reply('Согласие на ИИ-анализ отозвано. Результат удалён; отклик и резюме сохранены.', [('Мой отклик', '/application ' + state['application_id'])])
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
            reply('Отправьте текст резюме или прикрепите PDF-файл кнопкой со скрепкой (до 5 МБ). Ссылки на файлы не поддерживаются. Текст покажем для проверки; отклик отправится только после подтверждения. Сканы не читаются. Не включайте паспортные данные.')
    elif state.get('step') == 'resume':
        if attachment:
            if attachment['kind'] != 'pdf':
                reply('Пришлите один PDF-файл до 5 МБ. Поддерживается текстовый PDF до 10 страниц; сканы и другие форматы пока не читаются.')
                return
            from .imports import queue_max_attachment
            from .sources import SourceError
            try:
                queue_max_attachment(db, user, session, attachment['url'],
                                     attachment['filename'], attachment['size'])
                reply('Получил PDF. Проверю файл и покажу распознанный текст. Отклик не будет отправлен без вашего подтверждения.', [('Отмена', '/cancel')])
            except SourceError as exc:
                reply(str(exc))
        elif text.startswith(('https://', 'http://')):
            reply('Ссылки на файлы не поддерживаются. Прикрепите PDF кнопкой со скрепкой или отправьте текст резюме.')
        elif not 40 <= len(text) <= 20000 or text.startswith('/'):
            reply('Нужен текст резюме от 40 до 20 000 символов или PDF-файл до 5 МБ, прикреплённый кнопкой со скрепкой.')
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
                after_submit(app)
    elif state.get('step') in ('import_waiting', 'import_ready'):
        if state.get('step') == 'import_ready':
            reply('Проверьте текст, дополните или замените его, затем подтвердите отправку.',
                  [('Открыть черновик', '/import-preview ' + state['import_id']), ('Отмена', '/cancel')], bind=True)
        else:
            reply('Дождитесь результата импорта. Отклик ещё не отправлен.', [('Отмена', '/cancel')])
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
