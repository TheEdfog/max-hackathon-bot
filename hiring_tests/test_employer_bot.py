from sqlalchemy import select, func

from hiring.bot import process_event
from hiring.db import Application, Job, Outbox, User
from test_product import client


def send(client, uid, text, mid):
    process_event(client.app.state.factory, {"update_type": "message_created", "message": {
        "sender": {"user_id": uid, "name": f"Тест {uid}"}, "recipient": {"chat_type": "dialog"},
        "body": {"mid": str(mid), "text": text}}}, client.app.state.config)


def test_employer_and_candidate_entirely_in_max(client):
    client.app.state.config.employer_code = "private-test-code"
    client.app.state.config.bot_name = "test_bot"
    send(client, 100, "/employer private-test-code", 1)
    send(client, 100, "Тестовое бюро", 2)
    send(client, 100, "/newjob", 3)
    send(client, 100, "Python разработчик", 4)
    send(client, 100, "Разрабатываем внутренние инструменты. Обязательно Python и PostgreSQL.", 5)
    with client.app.state.factory() as db:
        assert db.scalar(select(func.count()).select_from(Job)) == 0
    # Explicit employer review is required before publication.
    send(client, 100, "Python\nPostgreSQL\n+Docker", 6)
    send(client, 100, "Публиковать", 7)
    with client.app.state.factory() as db:
        job = db.scalar(select(Job))
        jid = job.id
        assert len(job.requirements) == 3
        assert job.company == "Тестовое бюро"
    send(client, 200, "/start apply_" + jid, 8)
    send(client, 200, "Согласен", 9)
    send(client, 200, "Я разработал на Python сервис для учёта книг: написал тесты и документацию.", 10)
    send(client, 200, "PostgreSQL использовал для хранения книг, создавал схему и запросы.", 11)
    send(client, 200, "Нет опыта с Docker", 12)
    with client.app.state.factory() as db:
        row = db.scalar(select(Application))
        aid = row.id
        assert row.status == "ready"
    send(client, 100, "/jobs", 13)
    send(client, 100, "/candidates " + jid, 14)
    send(client, 100, "/view " + aid, 15)
    send(client, 100, f"/invite {aid} Приглашаем познакомиться завтра в 15:00, ответьте здесь.", 16)
    send(client, 200, "/status", 17)
    send(client, 200, "/confirm " + aid, 18)
    with client.app.state.factory() as db:
        assert db.get(Application, aid).status == "confirmed"
        texts = [r.body["text"] for r in db.scalars(select(Outbox))]
        assert any("https://max.ru/test_bot?start=apply_" in t for t in texts)
        assert any("опыта" in t and "Docker" in t for t in texts)
    send(client, 200, "/withdraw " + aid, 19)
    send(client, 200, "Отозвать", 20)
    with client.app.state.factory() as db:
        assert db.get(Application, aid).resume == ""


def test_no_group_processing_and_no_role_escalation(client):
    client.app.state.config.employer_code = "secret-code"
    send(client, 500, "/employer неверный", 1)
    send(client, 500, "Компания", 2)
    with client.app.state.factory() as db:
        assert db.scalar(select(User)).role == "candidate"
    process_event(client.app.state.factory, {"update_type": "message_created", "message": {
        "sender": {"user_id": 700, "name": "Из группы"}, "recipient": {"chat_type": "chat"},
        "body": {"mid": "group", "text": "/start"}}}, client.app.state.config)
    with client.app.state.factory() as db:
        assert db.scalar(select(User).where(User.max_id == "700")) is None


def test_employer_cannot_read_other_company_in_bot(client):
    client.app.state.config.employer_code = "secret"
    for uid in (100, 200):
        send(client, uid, "/employer secret", str(uid) + "a")
        send(client, uid, "Компания " + str(uid), str(uid) + "b")
    send(client, 100, "/newjob", 1)
    send(client, 100, "Разработчик", 2)
    send(client, 100, "Нужен разработчик на Python. Будем создавать внутренние сервисы.", 3)
    send(client, 100, "Публиковать", 4)
    with client.app.state.factory() as db:
        jid = db.scalar(select(Job)).id
    send(client, 200, "/close " + jid, 5)
    send(client, 200, "/candidates " + jid, 6)
    with client.app.state.factory() as db:
        assert db.get(Job, jid).active is True
        assert all("Вакансия не найдена" in r.body["text"] for r in db.scalars(select(Outbox).where(Outbox.max_id == "200").order_by(Outbox.available_at.desc()).limit(2)))
