from fastapi import HTTPException
from sqlalchemy import select
from .db import Application, Audit, BotSession, Job, Outbox, User
from .chat_ui import queue_message
from .matching import evidence, questions
from .sandbox import destination


def notify(db, user, text, application_id=None, buttons=None):
    if user.max_id and not user.demo:
        queue_message(db, user, text, buttons, application_id)


def owned_job(db, job_id, user):
    job = db.get(Job, job_id)
    if not job or job.owner_id != user.id:
        raise HTTPException(404, "Вакансия не найдена")
    return job


def application_view(db, app):
    user, job = db.get(User, app.user_id), db.get(Job, app.job_id)
    return {"id": app.id, "job_id": app.job_id, "name": user.name, "job_title": job.title,
            "company": job.company, "resume": app.resume, "answers": app.answers,
            "questions": app.questions, "status": app.status, "invitation": app.invitation,
            "created_at": app.created_at.isoformat(), "consent_at": app.consent_at.isoformat(),
            "assessment": evidence(app.resume, app.answers, job.requirements),
            "timeline": [{"action": x.action, "at": x.created_at.isoformat()} for x in db.scalars(select(Audit).where(Audit.application_id == app.id).order_by(Audit.created_at))]}


def submit(db, user, job, resume):
    if not job.active:
        raise HTTPException(409, "Приём откликов завершён")
    if user.role != "candidate":
        raise HTTPException(403, "Для отклика используйте аккаунт кандидата")
    existing = db.scalar(select(Application).where(Application.job_id == job.id, Application.user_id == user.id))
    if existing:
        if existing.status == "withdrawn":
            raise HTTPException(409, "Отклик был отозван. Для повторного отклика свяжитесь с работодателем.")
        return existing
    app = Application(job_id=job.id, user_id=user.id, resume=resume, questions=questions(resume, job.requirements))
    app.status = "clarifying" if app.questions else "ready"
    db.add(app)
    db.flush()
    db.add(Audit(application_id=app.id, action="applied"))
    notify(db, db.get(User, job.owner_id), f"Новый отклик на «{job.title}». Кандидат заполняет сведения." if app.questions else f"Новый отклик на «{job.title}» готов к просмотру.", app.id, [('Открыть отклик', '/view ' + app.id)])
    return app


def answer(db, app, answers):
    if app.status != "clarifying":
        return app
    allowed = {q["id"] for q in app.questions}
    if set(answers) - allowed:
        raise HTTPException(422, "Неизвестный вопрос")
    previous = dict(app.answers)
    app.answers = {**previous, **answers}
    if app.answers != previous:
        db.add(Audit(application_id=app.id, action="answered"))
    if all(app.answers.get(key) for key in allowed):
        app.status = "ready"
        job = db.get(Job, app.job_id)
        notify(db, db.get(User, job.owner_id), f"Кандидат ответил на уточнения по вакансии «{job.title}». Отклик готов к рассмотрению.", app.id, [('Открыть отклик', '/view ' + app.id)])
    return app


def invite(db, app, message):
    if app.status in ("invited", "confirmed"):
        return app
    if app.status != "ready":
        raise HTTPException(409, "Сначала дождитесь ответов кандидата")
    app.status, app.invitation = "invited", message
    db.add(Audit(application_id=app.id, action="invited"))
    job = db.get(Job, app.job_id)
    notify(db, db.get(User, app.user_id), f"Вас приглашают обсудить вакансию «{job.title}» в {job.company}.\n\n{message}", app.id, [('Подтвердить интерес', '/confirm ' + app.id), ('Мои отклики', '/status')])
    return app


def confirm(db, app):
    if app.status == "confirmed":
        return app
    if app.status != "invited":
        raise HTTPException(409, "Для этого отклика пока нет приглашения")
    app.status = "confirmed"
    db.add(Audit(application_id=app.id, action="confirmed"))
    job = db.get(Job, app.job_id)
    notify(db, db.get(User, job.owner_id), f"Кандидат подтвердил интерес к интервью по вакансии «{job.title}».", app.id, [('Открыть отклик', '/view ' + app.id)])
    return app


def withdraw_application(db, app):
    """Shared API/bot operation. Caller owns app and holds serialized write transaction."""
    if app.status == 'withdrawn':
        return app
    owner = db.get(User, db.get(Job, app.job_id).owner_id)
    candidate = db.get(User, app.user_id)
    recipients = [destination(u) for u in (owner, candidate) if u.max_id]
    for row in db.scalars(select(Outbox).where(
            (Outbox.application_id == app.id) |
            # Pre-v2 messages have no association: conservatively clear legacy recipient copies.
            ((Outbox.application_id.is_(None)) & Outbox.max_id.in_(recipients)))):
        row.body = {}
        if row.status in ('pending', 'failed'):
            row.status = 'cancelled'
    session = db.get(BotSession, candidate.id)
    if session and session.state.get('application_id') == app.id:
        session.state = {}
    app.resume, app.answers, app.questions, app.invitation, app.status = '', {}, [], '', 'withdrawn'
    db.add(Audit(application_id=app.id, action='withdrawn'))
    return app
