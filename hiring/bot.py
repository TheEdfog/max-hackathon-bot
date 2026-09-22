"""MAX event processing and durable outbound messages; single worker per deployment."""
import hashlib
import json
import threading
from datetime import timedelta
import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from .db import Application, BotEvent, BotSession, Job, Outbox, User, now
from .services import answer, confirm, submit
from .employer_bot import handle_employer
from .max_client import tls_context


def handle_update(db, event, config):
    kind = event.get("update_type")
    if kind not in ("bot_started", "message_created"):
        return
    msg = event.get("message") or {}
    if not isinstance(msg, dict) or not isinstance(msg.get("body", {}), dict):
        return
    if kind == "message_created" and (msg.get("recipient") or {}).get("chat_type", "dialog") != "dialog":
        return  # Never collect applications or disclose candidate data in group chats.
    identity = event.get("user") if kind == "bot_started" else msg.get("sender")
    if not isinstance(identity, dict) or identity.get("is_bot"):
        return
    max_id = identity.get("user_id", identity.get("id"))
    if not isinstance(max_id, int) or isinstance(max_id, bool) or max_id <= 0:
        return
    event_key = ("message:" + str(msg["body"]["mid"])) if msg.get("body", {}).get("mid") else json.dumps(event, sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha256(event_key.encode()).hexdigest()
    if db.get(BotEvent, digest):
        return
    db.add(BotEvent(id=digest))
    db.flush()
    user = db.scalar(select(User).where(User.max_id == str(max_id)))
    if not user:
        user = User(max_id=str(max_id), name=str(identity.get("name") or identity.get("first_name") or "Кандидат")[:160], role="candidate")
        db.add(user)
        db.flush()
    session = db.get(BotSession, user.id)
    if not session:
        session = BotSession(user_id=user.id, state={})
        db.add(session)
    state = dict(session.state)
    text = str(msg.get("body", {}).get("text") or "").strip()

    def reply(value, link=None):
        body = {"text": value[:3900]}
        if link:
            body["attachments"] = [{"type": "inline_keyboard", "payload": {"buttons": [[{"type": "link", "text": "Открыть РезюмИТ Найм", "url": link}]]}}]
        db.add(Outbox(max_id=str(max_id), body=body))

    if text == "/cancel":
        session.state = {}
        reply("Текущий шаг отменён. /help — меню. Сохранённые вакансии и отклики не изменились.")
        return
    if text in ("/privacy", "/help", "/start") or kind == "bot_started" and not event.get("payload"):
        if text == "/privacy":
            reply("Откликаясь, вы передаёте имя, MAX ID, текст опыта и ответы компании из вакансии для рассмотрения отклика. Тексты хранятся на сервере бота; во внешнюю языковую модель не передаются. Решение о найме принимает работодатель. Не отправляйте паспорт, здоровье и другие чувствительные сведения. Для отзыва: /withdraw ID_ОТКЛИКА (ID виден в /status). Сейчас это тестовая версия: используйте вымышленные данные.")
        else:
            reply("РезюмИТ Найм · помощник первичного отбора\n\nРаботодателю: /employer КОД, затем /newjob и /jobs.\nКандидату: откройте ссылку вакансии, которую прислал работодатель.\n/status — ваши отклики\n/privacy — обработка данных\n/cancel — отменить текущий шаг\n\nВсё работает в этом чате. Тестовая версия: используйте вымышленные резюме.")
        return
    if handle_employer(db, user, session, text, config, reply):
        return

    payload = str(event.get("payload") or "") if kind == "bot_started" else (text.split(" ", 1)[1] if text.startswith("/start ") else "")
    if payload.startswith("apply_"):
        job = db.get(Job, payload[6:])
        if not job or not job.active:
            reply("Эта вакансия недоступна. Попросите работодателя прислать актуальную ссылку.")
        elif user.role != "candidate":
            reply("Вы вошли как работодатель. Для проверки отклика используйте отдельный аккаунт кандидата.")
        else:
            session.state = {"step": "consent", "job_id": job.id}
            reply(f"{job.title} · {job.company}\n{job.terms}\n\n{job.description[:1500]}\n\nОтклик, имя и ваши ответы увидит работодатель {job.company}. Автоматического решения о найме нет. Это тестовая версия — используйте вымышленные данные.\n\nОтправьте «Согласен», если согласны передать ему данные для рассмотрения отклика. Подробнее: /privacy\nДля отмены: /cancel")
    elif text == "/cancel":
        session.state = {}
        reply("Диалог отменён. Уже отправленные отклики доступны в личном кабинете.")
    elif text.startswith("/confirm "):
        app = db.get(Application, text.split(" ", 1)[1].strip())
        if not app or app.user_id != user.id:
            reply("Отклик не найден.")
        else:
            try:
                confirm(db, app)
                reply("Интерес к интервью подтверждён. Работодатель получит уведомление.")
            except HTTPException as exc:
                reply(str(exc.detail))
    elif text == "/status":
        statuses = {"clarifying": "ждём ваших уточнений", "ready": "у работодателя", "invited": "приглашение на интервью", "confirmed": "интерес подтверждён", "withdrawn": "отозван"}
        apps = list(db.scalars(select(Application).where(Application.user_id == user.id)))
        for app in apps[:20]:
            reply(f"{db.get(Job, app.job_id).title}: {statuses.get(app.status, app.status)}\nID: {app.id}\n" + (f"Приглашение: {app.invitation}\nПодтвердить: /confirm {app.id}\n" if app.status == "invited" else "") + f"Отозвать: /withdraw {app.id}")
        if not apps:
            reply("Пока нет откликов. Перейдите по ссылке вакансии от работодателя.")
    elif text.startswith("/withdraw "):
        app = db.get(Application, text.split(" ", 1)[1].strip())
        if not app or app.user_id != user.id:
            reply("Отклик не найден.")
        else:
            session.state = {"step": "withdraw_confirm", "application_id": app.id}
            reply("Чтобы отозвать отклик и удалить из него резюме и ответы, напишите «Отозвать». /cancel — сохранить отклик.")
    elif state.get("step") == "withdraw_confirm":
        if text.lower() != "отозвать":
            reply("Напишите «Отозвать» или /cancel.")
        else:
            from .db import Audit
            app = db.get(Application, state["application_id"])
            if app and app.user_id == user.id:
                app.resume, app.answers, app.questions, app.invitation, app.status = "", {}, [], "", "withdrawn"
                db.add(Audit(application_id=app.id, action="withdrawn"))
            session.state = {}
            reply("Отклик отозван. Резюме и ответы удалены из базы откликов. Уже доставленные сообщения в MAX этим действием не удаляются.")
    elif state.get("step") == "consent":
        if text.lower() not in ("согласен", "согласна", "да"):
            reply("Для отклика нужно согласие. Отправьте «Согласен» или /cancel.")
        else:
            session.state = {**state, "step": "resume"}
            reply("Пришлите текст резюме или коротко опишите опыт, проекты и навыки одним сообщением (от 40 символов). Не включайте паспортные данные. PDF можно загрузить в мини-приложении.")
    elif state.get("step") == "resume":
        if not 40 <= len(text) <= 20000:
            reply("Пришлите текст от 40 до 20 000 символов. Если отправили файл, скопируйте из него текст или откройте мини-приложение.")
        else:
            job = db.get(Job, state["job_id"])
            if not job or not job.active:
                session.state = {}
                reply("Приём откликов завершён.")
            else:
                try:
                    app = submit(db, user, job, text)
                except HTTPException as exc:
                    session.state = {}
                    reply(str(exc.detail))
                    return
                pending = [q for q in app.questions if not app.answers.get(q["id"])]
                if pending and app.status == "clarifying":
                    session.state = {"step": "answer", "application_id": app.id}
                    reply("Отклик сохранён. Уточним несколько деталей.\n\n" + pending[0]["text"])
                else:
                    session.state = {}
                    reply("Отклик уже сохранён и доступен работодателю. Проверить статус: /status")
    elif state.get("step") == "answer":
        app = db.get(Application, state.get("application_id"))
        if not app or app.status != "clarifying":
            session.state = {}
            reply("Уточнения завершены. Проверить статус: /status")
        elif not 2 <= len(text) <= 2500:
            reply("Ответ должен содержать от 2 до 2500 символов. Если опыта нет, напишите «нет опыта».")
        else:
            pending = [q for q in app.questions if not app.answers.get(q["id"])]
            if pending:
                answer(db, app, {pending[0]["id"]: text})
            pending = [q for q in app.questions if not app.answers.get(q["id"])]
            if pending:
                reply(pending[0]["text"])
            else:
                session.state = {}
                reply("Спасибо! Ответы сохранены, работодатель получил ваш отклик. Проверить статус: /status")
    else:
        reply("Откройте ссылку вакансии от работодателя, чтобы отправить отклик.\n\n/employer КОД — режим работодателя\n/status — ваши отклики\n/help — помощь")


def process_event(factory, event, config):
    with factory() as db:
        try:
            handle_update(db, event, config)
            db.commit()
        except IntegrityError:
            db.rollback()


def start_worker(factory, config):
    stop = threading.Event()

    def run():
        while not stop.wait(0.7):
            if not config.bot_token:
                continue
            try:
                with factory() as db:
                    row = db.scalar(select(Outbox).where(Outbox.status == "pending", Outbox.available_at <= now()).order_by(Outbox.available_at).limit(1))
                    if not row:
                        continue
                    row.attempts += 1
                    try:
                        response = httpx.post(config.max_api_url + "/messages", params={"user_id": row.max_id}, headers={"Authorization": config.bot_token}, json=row.body, timeout=12, verify=tls_context(config.max_api_url))
                        success = response.is_success
                    except httpx.HTTPError:
                        success = False
                    row.status = "sent" if success else ("failed" if row.attempts >= 6 else "pending")
                    row.available_at = now() + timedelta(seconds=min(300, 2 ** row.attempts))
                    db.commit()
            except Exception:
                # Preserve the pending message for retry; never log its body or token.
                stop.wait(2)
    thread = threading.Thread(target=run, daemon=True, name="max-outbox")
    thread.start()
    return stop, thread
