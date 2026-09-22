"""Employer workflow, entirely inside a private MAX dialogue."""
import hmac
from fastapi import HTTPException
from sqlalchemy import select
from .db import Application, Job
from .matching import evidence, extract
from .services import invite, owned_job

STATUS = {"clarifying": "уточняет опыт", "ready": "готов к просмотру", "invited": "приглашён", "confirmed": "подтвердил интерес", "withdrawn": "отозван"}
EVIDENCE = {"mentioned": "указано в резюме", "answered": "уточнено в ответе", "negative": "сообщил об отсутствии опыта", "conflict": "противоречие", "review": "нужно прочитать ответ", "unknown": "нет сведений"}


def requirement_summary(requirements):
    return "\n".join(f"{i + 1}. {r['label']} — {'обязательно' if r['type'] == 'must' else 'желательно'}" for i, r in enumerate(requirements))


def handle_employer(db, user, session, text, config, reply):
    state = dict(session.state)
    command = text.split(maxsplit=1)[0] if text else ""
    if command == "/employer":
        if user.role == "employer":
            reply(f"Вы работодатель: {user.company}.\n/newjob — создать вакансию\n/jobs — мои вакансии")
        elif db.scalar(select(Application).where(Application.user_id == user.id)):
            reply("У вас уже есть отклики кандидата. Для работодателя нужен отдельный MAX-аккаунт.")
        elif not config.employer_code or not hmac.compare_digest(text.partition(" ")[2].strip().encode(), config.employer_code.encode()):
            reply("Пришлите /employer КОД — код доступа выдаёт владелец бота.\nКандидату код не нужен: используйте ссылку вакансии.")
        else:
            session.state = {"step": "employer_company"}
            reply("Как называется ваша компания? Пришлите название одним сообщением.")
        return True
    if state.get("step") == "employer_company":
        if not 2 <= len(text) <= 160 or text.startswith("/"):
            reply("Нужно название компании от 2 до 160 символов. /cancel — отмена.")
        else:
            user.role, user.company = "employer", text
            session.state = {}
            reply(f"Готово, {user.company}!\n/newjob — создать вакансию\n/jobs — мои вакансии\nРешения по кандидатам принимаете вы, бот лишь собирает сведения.")
        return True
    if user.role != "employer":
        return False
    if command == "/newjob":
        session.state = {"step": "job_title"}
        reply("Создадим вакансию. Как называется должность? Например: Junior Python-разработчик.\n/cancel — отмена.")
    elif command == "/jobs":
        rows = list(db.scalars(select(Job).where(Job.owner_id == user.id).order_by(Job.created_at.desc()).limit(20)))
        if not rows:
            reply("Вакансий пока нет. Создать: /newjob")
        for job in rows:
            reply(f"{job.title} · {'Открыта' if job.active else 'Закрыта'}\nКандидаты: /candidates {job.id}\n{'Закрыть: /close' if job.active else 'Открыть: /open'} {job.id}\nСсылка кандидату: https://max.ru/{config.bot_name}?start=apply_{job.id}")
    elif command in ("/candidates", "/close", "/open"):
        try:
            job = owned_job(db, text.partition(" ")[2].strip(), user)
            if command != "/candidates":
                job.active = command == "/open"
                reply("Приём откликов открыт." if job.active else "Приём откликов закрыт. Сохранённые отклики остались доступны.")
            else:
                rows = list(db.scalars(select(Application).where(Application.job_id == job.id, Application.status != "withdrawn").order_by(Application.created_at).limit(20)))
                reply(f"{job.title}\nОтклики: {len(rows)} (показываем до 20).\nЧисла ниже — только наличие упоминаний, не рейтинг кандидата.")
                from .db import User
                for row in rows:
                    result = evidence(row.resume, row.answers, job.requirements)
                    reply(f"{db.get(User, row.user_id).name}\n{STATUS[row.status]}\nУпомянуто требований: {result['covered']}/{result['total']}\nПосмотреть: /view {row.id}")
        except HTTPException:
            reply("Вакансия не найдена. Список: /jobs")
    elif command in ("/view", "/invite"):
        parts = text.split(maxsplit=2)
        row = db.get(Application, parts[1]) if len(parts) >= 2 else None
        try:
            if not row or row.status == "withdrawn":
                raise HTTPException(404)
            job = owned_job(db, row.job_id, user)
            if command == "/invite":
                if len(parts) < 3 or not 10 <= len(parts[2]) <= 1500:
                    reply(f"Пришлите /invite {row.id} Ваше сообщение кандидату (от 10 до 1500 символов). Укажите способ и время связи.")
                else:
                    invite(db, row, parts[2])
                    reply("Приглашение сохранено и поставлено в очередь доставки кандидату в MAX.")
            else:
                from .db import User
                reply(f"{db.get(User, row.user_id).name} · {job.title}\nСтатус: {STATUS[row.status]}\n\nРезюме (первые 3000 символов):\n{row.resume[:3000]}")
                for r in evidence(row.resume, row.answers, job.requirements)["requirements"]:
                    quotes = "\n".join(r["snippets"])
                    reply(f"{r['label']}: {EVIDENCE[r['state']]}\n{quotes}\n" + (f"Ответ: {r['answer']}" if r["answer"] else ""))
                reply(f"Решение остаётся за вами. Для приглашения:\n/invite {row.id} Ваше сообщение\nСовпадение слов не подтверждает квалификацию.")
        except HTTPException as exc:
            reply(str(exc.detail) if exc.status_code != 404 else "Отклик не найден.")
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
            reply("Проверьте требования, найденные локальным словарём:\n" + (requirement_summary(requirements) or "Навыки не распознаны.") + "\n\nПубликовать — подтвердить и получить ссылку.\nИли пришлите исправленный список: каждое требование с новой строки; необязательное начните со знака +. До 15 требований. Не включайте возраст, пол и другие личные признаки.")
    elif state.get("step") == "job_review":
        if text.lower() == "публиковать":
            if not state.get("requirements"):
                reply("Сначала добавьте хотя бы одно профессиональное требование.")
            else:
                job = Job(owner_id=user.id, company=user.company, title=state["title"], description=state["description"], requirements=state["requirements"])
                db.add(job)
                db.flush()
                session.state = {}
                reply(f"Вакансия опубликована: {job.title}\n\nОтправьте кандидатам:\nhttps://max.ru/{config.bot_name}?start=apply_{job.id}\n\nСписок откликов: /candidates {job.id}\nДля теста попросите коллегу открыть ссылку со своего MAX-аккаунта.")
        else:
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            if not 1 <= len(lines) <= 15 or any(not 1 <= len(s.lstrip('+').strip()) <= 100 for s in lines):
                reply("Пришлите от 1 до 15 требований, каждое с новой строки, не более 100 символов.")
            elif len({s.lstrip('+').strip().lower() for s in lines}) != len(lines):
                reply("Требования не должны повторяться.")
            else:
                requirements = [{"id": f"r{i}", "skill": s.lstrip('+').strip().lower(), "label": s.lstrip('+').strip(), "type": "nice" if s.startswith('+') else "must", "source": "Подтверждено работодателем в MAX"} for i, s in enumerate(lines)]
                session.state = {**state, "requirements": requirements}
                reply("Обновлено:\n" + requirement_summary(requirements) + "\n\nНапишите «Публиковать» или пришлите новый список.")
    else:
        reply(f"{user.company} · Меню работодателя\n/newjob — новая вакансия\n/jobs — вакансии и отклики\n/cancel — отменить текущий шаг\n/help — помощь")
    return True
