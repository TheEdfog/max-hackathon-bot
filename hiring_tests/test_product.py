import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from hiring.config import Config
from hiring.db import Application, BotEvent, Outbox, User
from hiring.main import create_app
from hiring.matching import evidence, questions
from hiring.security import max_identity


@pytest.fixture
def client(tmp_path):
    config = Config(database_url=f"sqlite:///{tmp_path / 'test.db'}", secret="test-secret-" * 5,
                    bot_token="test-bot-token", webhook_secret="test-webhook-secret", worker=False)
    with TestClient(create_app(config)) as value:
        yield value


def register(client, name, role="candidate"):
    response = client.post("/api/auth/register", json={"email": f"{name}@example.com", "password": "test-password-123",
                           "name": name, "role": role, "company": "Тестовая компания"})
    assert response.status_code == 201, response.text
    return {"Authorization": "Bearer " + response.json()["token"]}


def job(client, headers):
    result = client.post("/api/jobs", headers=headers, json={"title": "Python разработчик",
        "description": "Разрабатываем внутренний сервис. Требуются Python и PostgreSQL.",
        "requirements": [{"id": "python", "skill": "python", "label": "Python", "type": "must"},
                         {"id": "pg", "skill": "postgresql", "label": "PostgreSQL", "type": "must"}]})
    assert result.status_code == 201, result.text
    return result.json()["id"]


def signed_data(fields=None):
    values = {"auth_date": str(int(time.time())), "user": json.dumps({"id": 123456, "first_name": "Тест"}), **(fields or {})}
    key = hmac.new(b"WebAppData", b"test-bot-token", hashlib.sha256).digest()
    values["hash"] = hmac.new(key, "\n".join(f"{k}={v}" for k, v in sorted(values.items())).encode(), hashlib.sha256).hexdigest()
    return urlencode(values)


def test_full_hiring_cycle(client):
    employer = register(client, "employer", "employer")
    candidate = register(client, "candidate")
    jid = job(client, employer)
    body = {"name": "Тестовый кандидат", "resume": "Разработал на Python сервис учёта книг, написал тесты и документацию.", "consent": True}
    no_consent = client.post(f"/api/jobs/{jid}/apply", headers=candidate, json={**body, "consent": False})
    assert no_consent.status_code == 422
    result = client.post(f"/api/jobs/{jid}/apply", headers=candidate, json=body)
    assert result.status_code == 201, result.text
    app = result.json()
    aid = app["id"]
    assert app["status"] == "clarifying"
    assert [q["id"] for q in app["questions"]] == ["pg"]
    assert client.post(f"/api/jobs/{jid}/apply", headers=candidate, json=body).json()["id"] == aid
    assert client.post(f"/api/applications/{aid}/invite", headers=employer, json={"message": "Приглашаем на встречу"}).status_code == 409
    assert client.post(f"/api/applications/{aid}/answers", headers=candidate, json={"answers": {"fake": "Ответ"}}).status_code == 422
    app = client.post(f"/api/applications/{aid}/answers", headers=candidate,
                      json={"answers": {"pg": "Использовал PostgreSQL в учебном проекте: создал схему и запросы."}}).json()
    assert app["status"] == "ready"
    assert app["assessment"]["covered"] == 2
    rows = client.get(f"/api/jobs/{jid}/applications", headers=employer).json()
    assert len(rows) == 1 and rows[0]["answers"]["pg"]
    assert client.post(f"/api/applications/{aid}/invite", headers=employer, json={"message": "Приглашаем обсудить вакансию завтра в 15:00."}).json()["status"] == "invited"
    assert client.post(f"/api/applications/{aid}/confirm", headers=candidate).json()["status"] == "confirmed"
    assert client.post(f"/api/applications/{aid}/confirm", headers=candidate).json()["status"] == "confirmed"
    assert client.get("/api/metrics", headers=employer).json()["confirmed"] == 1
    assert client.delete(f"/api/applications/{aid}", headers=candidate).status_code == 204
    assert client.get(f"/api/jobs/{jid}/applications", headers=employer).json() == []
    row = client.get("/api/applications", headers=candidate).json()[0]
    assert row["status"] == "withdrawn" and row["resume"] == "" and row["answers"] == {}
    assert client.post(f"/api/jobs/{jid}/apply", headers=candidate, json=body).status_code == 409


def test_authorization_boundaries(client):
    employer = register(client, "owner", "employer")
    stranger = register(client, "stranger", "employer")
    candidate = register(client, "person")
    jid = job(client, employer)
    for path in (f"/api/jobs/{jid}", f"/api/jobs/{jid}/applications"):
        assert client.get(path, headers=stranger).status_code == 404
        assert client.get(path).status_code == 401
        assert client.get(path, headers=candidate).status_code == 403
    assert client.patch(f"/api/jobs/{jid}", headers=stranger, json={"active": False}).status_code == 404
    assert client.get(f"/api/public/jobs/{jid}").status_code == 200
    assert "applications" not in client.get(f"/api/public/jobs/{jid}").json()
    app = client.post(f"/api/jobs/{jid}/apply", headers=candidate, json={"name": "Person", "resume": "Python PostgreSQL: написал работающий сервис и тесты для его компонентов.", "consent": True}).json()
    assert client.post(f"/api/applications/{app['id']}/invite", headers=stranger, json={"message": "Попытка чужого приглашения"}).status_code == 404
    assert client.post(f"/api/applications/{app['id']}/confirm", headers=stranger).status_code == 404
    assert client.delete(f"/api/applications/{app['id']}", headers=stranger).status_code == 404


def test_closed_job(client):
    employer = register(client, "owner", "employer")
    candidate = register(client, "person")
    jid = job(client, employer)
    assert client.patch(f"/api/jobs/{jid}", headers=employer, json={"active": False}).status_code == 200
    assert client.post(f"/api/jobs/{jid}/apply", headers=candidate, json={"name": "Person", "resume": "Python PostgreSQL: написал работающий сервис и тесты для компонентов.", "consent": True}).status_code == 409


@pytest.mark.parametrize("values", [{"auth_date": "1"}, {"user": "[]"}, {"user": '{"id":true}'}, {"user": '{"id":1,"name":[]}'}, {"user": '{"id":-1}'}])
def test_invalid_max_launch(client, values):
    # An empty name list falls back to the safe default, so use a non-empty invalid value.
    if values.get("user") == '{"id":1,"name":[]}':
        values = {"user": '{"id":1,"name":["invalid"]}'}
    assert client.post("/api/auth/max", json={"init_data": signed_data(values)}).status_code == 401


def test_max_login_and_forgery(client):
    value = signed_data()
    assert max_identity(value, "test-bot-token") == ("123456", "Тест")
    response = client.post("/api/auth/max", json={"init_data": value})
    assert response.status_code == 200
    assert client.post("/api/auth/max", json={"init_data": value.replace("123456", "654321")}).status_code == 401
    assert client.post("/api/auth/max", json={"init_data": value + "&auth_date=1"}).status_code == 401


def test_max_bot_persistent_dialog_and_deduplication(client):
    employer = register(client, "owner", "employer")
    jid = job(client, employer)
    headers = {"X-Max-Bot-Api-Secret": "test-webhook-secret"}
    start = {"update_type": "bot_started", "timestamp": 100, "user": {"user_id": 555, "name": "Кандидат"}, "payload": "apply_" + jid}
    assert client.post("/api/max/webhook", json=start).status_code == 403
    for _ in range(2):
        assert client.post("/api/max/webhook", headers=headers, json=start).status_code == 200
    factory = client.app.state.factory
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Outbox)) == 1
    for mid, content in enumerate(["Согласен", "Я создал проект на Python: библиотечный каталог с тестами и документацией.", "PostgreSQL использовал в проекте каталога: писал SELECT и миграции."]):
        event = {"update_type": "message_created", "message": {"sender": {"user_id": 555, "name": "Кандидат"}, "body": {"mid": str(mid), "text": content}}}
        assert client.post("/api/max/webhook", headers=headers, json=event).status_code == 200
        assert client.post("/api/max/webhook", headers=headers, json=event).status_code == 200
    with factory() as db:
        user = db.scalar(select(User).where(User.max_id == "555"))
        row = db.scalar(select(Application).where(Application.user_id == user.id))
        assert row.status == "ready" and row.answers["pg"]
        assert db.scalar(select(func.count()).select_from(BotEvent)) == 4
    # No messages are actually sent by the test (worker=False).


def test_local_extraction_demo_and_pdf_errors(client):
    demo = client.post("/api/auth/demo").json()
    headers = {"Authorization": "Bearer " + demo["token"]}
    assert demo["user"]["demo"] is True
    assert client.get("/api/metrics", headers=headers).json()["applications"] == 3
    result = client.post("/api/requirements/extract", headers=headers, json={"text": "Требуется Python разработчик. Обязательно PostgreSQL. Будет плюсом Docker."})
    assert result.status_code == 200 and len(result.json()["requirements"]) >= 2
    assert client.post("/api/resume/extract", headers=headers, files={"file": ("cv.pdf", b"not pdf", "application/pdf")}).status_code == 422
    assert client.post("/api/resume/extract", headers=headers, files={"file": ("cv.pdf", b"%PDF-invalid", "application/pdf")}).status_code == 422
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/openapi.json").json()["info"]["title"] == "РезюмИТ Найм"


def test_production_configuration_fails_closed():
    with pytest.raises(ValueError):
        Config(production=True, secret="", employer_code="", public_url="http://localhost").validate()


@pytest.mark.parametrize("resume,answer,state", [
    ("Python использовал в проекте", "", "mentioned"),
    ("Не работал с Python", "", "negative"),
    ("Python использовал. Не работал с Python.", "", "conflict"),
    ("Работал с Java", "", "unknown"),
    ("Java", "Python использовал, а Docker не использовал", "answered"),
    ("Java", "нет опыта", "negative"),
    ("Java", "Да, есть такой опыт", "review"),
])
def test_evidence_is_explainable(resume, answer, state):
    reqs = [{"id": "py", "skill": "python", "label": "Python", "type": "must"}]
    result = evidence(resume, {"py": answer}, reqs)
    assert result["requirements"][0]["state"] == state
    assert "score" not in result


def test_questions_bounded_and_required_first():
    reqs = [{"id": f"s{i}", "skill": f"skill{i}", "label": f"Skill {i}", "type": "nice" if i < 2 else "must"} for i in range(8)]
    assert [q["id"] for q in questions("Опыт не указан", reqs)] == ["s2", "s3", "s4"]
