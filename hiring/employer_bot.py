"""Employer workflow, entirely inside a private MAX dialogue."""
import hmac
from datetime import timezone, timedelta
from fastapi import HTTPException
from sqlalchemy import func, select
from core.utils import normalize_skill
from .db import Application, BotAttempt, Job, User, now
from .chat_ui import PAGE_SIZE, page_number
from .matching import evidence, extract
from .services import invite, owned_job
from .screening import PRESETS

STATUS = {"clarifying": "уточняет опыт", "ready": "готов к просмотру", "invited": "приглашён", "confirmed": "подтвердил интерес", "withdrawn": "отозван"}
EVIDENCE = {"mentioned": "указано в резюме", "answered": "уточнено в ответе", "negative": "сообщил об отсутствии опыта", "conflict": "противоречие", "review": "нужно прочитать ответ", "unknown": "нет сведений"}
EVIDENCE['inferred'] = 'косвенный признак по связанной технологии; уточните опыт'


def requirement_summary(requirements):
    return "\n".join(f"{i + 1}. {r['label']} - {'обязательно' if r['type'] == 'must' else 'желательно'}" for i, r in enumerate(requirements))


def review_buttons():
    return [('Публиковать', 'Публиковать'), ('Вопросы об ожиданиях', '/screening'), ('Тест для вакансии', '/job-test'), ('Отмена', '/cancel')]


def handle_employer(db, user, session, text, config, reply):
    state = dict(session.state)
    command = text.split(maxsplit=1)[0] if text else ""
    if state.get('step') == 'employer_code' and not text.startswith('/'):
        text = '/employer ' + text
        command = '/employer'
    if command == "/employer":
        if user.role == "employer":
            reply(f"Вы работодатель: {user.company}.", [('Новая вакансия', '/newjob'), ('Мои вакансии', '/jobs')])
        elif db.scalar(select(Application).where(Application.user_id == user.id)):
            reply("У вас уже есть отклики кандидата. Для работодателя нужен отдельный MAX-аккаунт.")
        elif not text.partition(' ')[2].strip():
            session.state = {'step': 'employer_code'}
            reply('Введите код доступа работодателя. Его выдаёт владелец бота; кандидатам код не нужен.', [('Отмена', '/cancel')])
        else:
            attempt = db.get(BotAttempt, user.id)
            if not attempt:
                attempt = BotAttempt(user_id=user.id, attempts=0, started_at=now())
                db.add(attempt)
            if attempt.started_at.replace(tzinfo=timezone.utc) < now() - timedelta(minutes=10):
                attempt.started_at, attempt.attempts = now(), 0
            if attempt.attempts >= 5:
                reply('Слишком много попыток. Попробуйте через 10 минут.')
            elif not config.employer_code or not hmac.compare_digest(text.partition(' ')[2].strip().encode(), config.employer_code.encode()):
                attempt.attempts += 1
                session.state = {'step': 'employer_code'}
                reply('Код не подошёл. Уточните код у владельца бота.', [('Отмена', '/cancel')])
            else:
                session.state = {"step": "employer_company"}
                reply("Как называется ваша компания? Пришлите название одним сообщением.")
        return True
    if state.get("step") == "employer_company":
        if not 2 <= len(text) <= 160 or text.startswith("/"):
            reply("Нужно название компании от 2 до 160 символов. /cancel - отмена.")
        else:
            user.role, user.company = "employer", text
            session.state = {}
            reply(f"Готово, {user.company}! Решения по кандидатам принимаете вы, бот лишь собирает сведения.", [('Новая вакансия', '/newjob'), ('Мои вакансии', '/jobs')])
        return True
    if user.role != "employer":
        return False
    from .assessment_bot import handle_tests
    if handle_tests(db, user, session, text, reply):
        return True
    if command in ('/jobs', '/job', '/candidates', '/view', '/resume', '/evidence', '/metrics') and state.get('step') == 'invite_message':
        session.state = {}
    if command in ('/screening', '/screening-on', '/screening-off'):
        if state.get('step') != 'job_review':
            reply('Сначала создайте вакансию и перейдите к проверке требований.')
        else:
            if command != '/screening':
                selected = [q['id'] for q in PRESETS] if command == '/screening-on' else []
                session.state = {**state, 'screening_questions': selected}
            enabled = bool(session.state.get('screening_questions'))
            reply('Дополнительные вопросы: ' + ('включены' if enabled else 'выключены') +
                  '.\nИнтерес к задачам, ожидания по оплате и формату, срок выхода. '
                  'Ответы не оцениваются автоматически; каждый вопрос можно пропустить. '
                  'До 3 вопросов о навыках + 3 об ожиданиях. Тест вакансии добавляется отдельно, до 3 заданий.',
                  [('Без дополнительных вопросов' if enabled else 'Добавить 3 вопроса', '/screening-off' if enabled else '/screening-on')] + review_buttons(), bind=True)
    elif command == '/screening-answers':
        parts = text.split()
        row = db.get(Application, parts[1]) if len(parts) > 1 else None
        if not row or row.status == 'withdrawn' or db.get(Job, row.job_id).owner_id != user.id:
            reply('Отклик не найден.')
        else:
            items = [q for q in row.questions if q.get('kind') == 'screening']
            index = min(page_number(parts[2] if len(parts) > 2 else 0), max(0, len(items) - 1))
            buttons = [('К карточке', '/view ' + row.id)]
            if index:
                buttons.append(('⬅️ Вопрос', f'/screening-answers {row.id} {index - 1}'))
            if index + 1 < len(items):
                buttons.append(('Вопрос ➡️', f'/screening-answers {row.id} {index + 1}'))
            value = f"{items[index]['label']}\n{row.answers.get(items[index]['id'], 'Пока нет ответа')}" if items else 'Дополнительные вопросы не задавались.'
            reply('Ожидания кандидата - без автоматической оценки\n\n' + value, buttons, application_id=row.id)
    elif command == "/newjob":
        session.state = {"step": "job_title"}
        reply("Создадим вакансию. Как называется должность? Например: Junior Python-разработчик.\n/cancel - отмена.")
    elif command == '/metrics':
        counts = dict(db.execute(select(Application.status, func.count()).join(Job).where(
            Job.owner_id == user.id, Application.status != 'withdrawn').group_by(Application.status)).all())
        jobs = db.scalar(select(func.count()).select_from(Job).where(Job.owner_id == user.id, Job.active == True))
        reply(f"Сводка · {user.company}\n\nОткрытых вакансий: {jobs}\nОткликов: {sum(counts.values())}\nУточняют сведения: {counts.get('clarifying', 0)}\nЖдут вашего решения: {counts.get('ready', 0)}\nПриглашены, ждём ответа: {counts.get('invited', 0)}\nПодтвердили интерес: {counts.get('confirmed', 0)}\n\nЭто фактические статусы, не оценка качества кандидатов. Отозванные отклики не учитываются.",
              [('Мои вакансии', '/jobs'), ('Новая вакансия', '/newjob'), ('Меню', '/help')])
    elif command == "/jobs":
        page = page_number(text.partition(' ')[2])
        rows = list(db.scalars(select(Job).where(Job.owner_id == user.id).order_by(Job.created_at.desc(), Job.id).offset(page * PAGE_SIZE).limit(PAGE_SIZE + 1)))
        if not rows:
            reply("Здесь пока нет вакансий.", [('Новая вакансия', '/newjob'), ('В начало списка', '/jobs')])
        else:
            buttons = [(f"{'●' if j.active else '○'} {j.title}", '/job ' + j.id) for j in rows[:PAGE_SIZE]]
            if page:
                buttons.append(('⬅️ Назад', f'/jobs {page - 1}'))
            if len(rows) > PAGE_SIZE:
                buttons.append(('Далее ➡️', f'/jobs {page + 1}'))
            buttons.append(('Новая вакансия', '/newjob'))
            reply(f'Мои вакансии · страница {page + 1}. Выберите вакансию.', buttons)
    elif command == '/job':
        try:
            job = owned_job(db, text.partition(' ')[2], user)
            reply(f"{job.title} · {'Открыта' if job.active else 'Закрыта'}\n\n{job.description[:1600]}\n\nСсылка кандидату:\nhttps://max.ru/{config.bot_name}?start=apply_{job.id}",
                  [('Кандидаты', '/candidates ' + job.id), ('Закрыть приём' if job.active else 'Открыть приём', ('/close ' if job.active else '/open ') + job.id), ('Мои вакансии', '/jobs')])
        except HTTPException:
            reply('Вакансия не найдена.', [('Мои вакансии', '/jobs')])
    elif command in ("/candidates", "/close", "/open"):
        try:
            parts = text.split()
            job = owned_job(db, parts[1] if len(parts) > 1 else '', user)
            if command != "/candidates":
                job.active = command == "/open"
                reply("Приём откликов открыт." if job.active else "Приём откликов закрыт. Сохранённые отклики остались доступны.", [('К вакансии', '/job ' + job.id)])
            else:
                page = page_number(parts[2] if len(parts) > 2 else 0)
                rows = list(db.scalars(select(Application).where(Application.job_id == job.id, Application.status != "withdrawn").order_by(Application.created_at, Application.id).offset(page * PAGE_SIZE).limit(PAGE_SIZE + 1)))
                buttons = [(f'{db.get(User, row.user_id).name} · {STATUS[row.status]}', '/view ' + row.id) for row in rows[:PAGE_SIZE]]
                if page:
                    buttons.append(('⬅️ Назад', f'/candidates {job.id} {page - 1}'))
                if len(rows) > PAGE_SIZE:
                    buttons.append(('Далее ➡️', f'/candidates {job.id} {page + 1}'))
                buttons.append(('К вакансии', '/job ' + job.id))
                reply(f'{job.title}\nОтклики · страница {page + 1}.\n' + ('Выберите кандидата. Порядок - по времени отклика, не рейтинг.' if rows else 'На этой странице откликов нет.'), buttons)
        except HTTPException:
            reply("Вакансия не найдена. Список: /jobs")
    elif command in ("/view", "/invite", '/resume', '/evidence'):
        parts = text.split(maxsplit=2)
        row = db.get(Application, parts[1]) if len(parts) >= 2 else None
        try:
            if not row or row.status == "withdrawn":
                raise HTTPException(404)
            job = owned_job(db, row.job_id, user)
            if command == "/invite":
                if len(parts) < 3 or not 10 <= len(parts[2]) <= 1500:
                    if row.status != 'ready':
                        reply('Приглашение доступно после уточнений; повторно отправлять его не нужно.', [('К карточке', '/view ' + row.id)])
                    else:
                        session.state = {'step': 'invite_message', 'application_id': row.id}
                        reply('Введите приглашение (10-1500 символов): предложите время и способ связи. Следующее сообщение будет отправлено кандидату.', [('Отмена', '/cancel')], application_id=row.id)
                else:
                    invite(db, row, parts[2])
                    reply("Приглашение сохранено и поставлено в очередь доставки кандидату в MAX.")
            elif command == '/resume':
                page = page_number(parts[2] if len(parts) > 2 else 0)
                chunk = row.resume[page * 2800:(page + 1) * 2800]
                buttons = [('К карточке', '/view ' + row.id)]
                if page:
                    buttons.append(('⬅️ Назад', f'/resume {row.id} {page - 1}'))
                if (page + 1) * 2800 < len(row.resume):
                    buttons.append(('Далее ➡️', f'/resume {row.id} {page + 1}'))
                reply(f'Резюме · часть {page + 1}\n{chunk or "Конец резюме."}', buttons, application_id=row.id)
            elif command == '/evidence':
                index = page_number(parts[2] if len(parts) > 2 else 0)
                items = evidence(row.resume, row.answers, job.requirements)['requirements']
                if index >= len(items):
                    reply('Требование не найдено.', [('К карточке', '/view ' + row.id)])
                else:
                    r = items[index]
                    quote = '\n'.join(r['snippets'])[:900] or 'Нет упоминания в резюме.'
                    buttons = [('К карточке', '/view ' + row.id)]
                    if index:
                        buttons.append(('⬅️ Требование', f'/evidence {row.id} {index - 1}'))
                    if index + 1 < len(items):
                        buttons.append(('Требование ➡️', f'/evidence {row.id} {index + 1}'))
                    reply(f"{r['label']} · {EVIDENCE[r['state']]}\n\nЦитата:\n{quote}\n\nОтвет:\n{r['answer'] or 'Уточнение не запрашивалось или ещё не получено.'}", buttons, application_id=row.id)
            else:
                summary = '\n'.join(f"• {r['label']}: {EVIDENCE[r['state']]}" for r in evidence(row.resume, row.answers, job.requirements)['requirements'])
                buttons = [('Цитаты и ответы', '/evidence ' + row.id), ('Полное резюме', '/resume ' + row.id)]
                if any(q.get('kind') == 'screening' for q in row.questions):
                    buttons.append(('Ожидания кандидата', '/screening-answers ' + row.id))
                if any(q.get('kind') == 'assessment' for q in row.questions):
                    buttons.append(('Ответы на тест', '/test-answers ' + row.id))
                if row.status == 'ready':
                    buttons.append(('Пригласить', '/invite ' + row.id))
                buttons.append(('К списку', '/candidates ' + job.id))
                reply(f"{db.get(User, row.user_id).name} · {job.title}\n{STATUS[row.status]}\n\n{summary}\n\nЭто сведения кандидата, а не проверенная квалификация. Решение принимаете вы.", buttons, application_id=row.id)
        except HTTPException as exc:
            reply(str(exc.detail) if exc.status_code != 404 else "Отклик не найден.")
    elif state.get('step') == 'invite_message':
        row = db.get(Application, state.get('application_id'))
        try:
            if not row:
                raise HTTPException(404, 'Отклик не найден.')
            owned_job(db, row.job_id, user)
            if not 10 <= len(text) <= 1500 or text.startswith('/'):
                reply('Введите приглашение от 10 до 1500 символов или отмените.', [('Отмена', '/cancel')])
            else:
                invite(db, row, text)
                session.state = {}
                reply('Приглашение поставлено в очередь доставки.', [('К карточке', '/view ' + row.id)], application_id=row.id)
        except HTTPException as exc:
            session.state = {}
            reply(str(exc.detail), [('Мои вакансии', '/jobs')])
    elif state.get("step") == "job_title":
        if not 3 <= len(text) <= 160:
            reply("Название: от 3 до 160 символов.")
        else:
            session.state = {"step": "job_description", "title": text}
            reply("Пришлите описание вакансии: задачи, обязательные и желательные профессиональные навыки, условия. От 30 до 20 000 символов.")
    elif state.get("step") == "job_description":
        if not 30 <= len(text) <= 20000:
            reply("Описание: от 30 до 20 000 символов.")
        else:
            requirements = extract(text)
            session.state = {**state, "step": "job_review", "description": text, "requirements": requirements}
            reply("Проверьте требования, найденные локальным словарём:\n" + (requirement_summary(requirements) or "Навыки не распознаны.") + "\n\nИли пришлите исправленный список: каждое требование с новой строки; необязательное начните со знака +. До 15 требований. Не включайте возраст, пол и другие личные признаки.", review_buttons(), bind=True)
    elif state.get("step") == "job_review":
        if text.lower() == "публиковать":
            if not state.get("requirements"):
                reply("Сначала добавьте хотя бы одно профессиональное требование.")
            else:
                job = Job(owner_id=user.id, company=user.company, title=state["title"], description=state["description"], requirements=state["requirements"], screening_questions=state.get('screening_questions', []), test_questions=state.get('test_questions', []))
                db.add(job)
                db.flush()
                session.state = {}
                next_step = 'В песочнице выберите роль «Кандидат», затем откройте эту ссылку.' if config.sandbox else 'Для теста попросите коллегу открыть ссылку со своего MAX-аккаунта.'
                reply(f"Вакансия опубликована: {job.title}\n\nОтправьте кандидатам:\nhttps://max.ru/{config.bot_name}?start=apply_{job.id}\n\n{next_step}", [('Кандидаты', '/candidates ' + job.id), ('Мои вакансии', '/jobs')])
        else:
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            if not 1 <= len(lines) <= 15 or any(not 1 <= len(s.lstrip('+').strip()) <= 100 for s in lines):
                reply("Пришлите от 1 до 15 требований, каждое с новой строки, не более 100 символов.")
            elif len({normalize_skill(s.lstrip('+').strip()) for s in lines}) != len(lines):
                reply("Требования не должны повторяться.")
            else:
                requirements = [{"id": f"r{i}", "skill": normalize_skill(s.lstrip('+').strip()), "label": s.lstrip('+').strip(), "type": "nice" if s.startswith('+') else "must", "source": "Подтверждено работодателем в MAX"} for i, s in enumerate(lines)]
                session.state = {**state, "requirements": requirements}
                reply("Обновлено:\n" + requirement_summary(requirements) + "\n\nПодтвердите или пришлите новый список.", review_buttons(), bind=True)
    else:
        reply(f"{user.company} · Меню работодателя", [('Новая вакансия', '/newjob'), ('Вакансии и отклики', '/jobs'), ('Отменить текущий шаг', '/cancel')])
    return True
