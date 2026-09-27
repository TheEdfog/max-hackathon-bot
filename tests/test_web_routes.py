from __future__ import annotations

from types import SimpleNamespace

import pytest

from apps.web.services.pdf_service import PdfServiceError


def test_dashboard_redirects_guest_to_login(client):
    response = client.get("/dashboard", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"].startswith("/login?message=")
    assert "message_type=warning" in response.headers["location"]


def test_vacancies_page_requires_auth(client):
    response = client.get("/vacancies", follow_redirects=False)

    assert response.status_code == 401
    assert response.json()["detail"] == "Требуется авторизация"


def test_vacancy_extract_redirects_to_analysis_without_reextract(auth_client, monkeypatch):
    vacancy = SimpleNamespace(id=3, requirements=[SimpleNamespace(skill_norm="python")], raw_text="text")
    activated = []

    monkeypatch.setattr("apps.web.routes.vacancies.get_user_vacancy_or_raise", lambda db, user_id, vacancy_id: vacancy)
    monkeypatch.setattr(
        "apps.web.routes.vacancies.activate_vacancy_for_user",
        lambda db, user_id, vacancy_id: activated.append(vacancy_id),
    )
    monkeypatch.setattr(
        "apps.web.routes.vacancies.extract_and_save_requirements",
        lambda *args, **kwargs: pytest.fail("Повторное извлечение не должно вызываться"),
    )

    response = auth_client.post("/vacancies/3/extract", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"].startswith("/result?vacancy_id=3&message=")
    assert "message_type=info" in response.headers["location"]
    assert activated == [3]


def test_vacancy_extract_runs_when_requirements_are_missing(auth_client, monkeypatch):
    vacancy = SimpleNamespace(id=5, requirements=[], raw_text="text")
    extracted = SimpleNamespace(id=5, requirements=[SimpleNamespace(skill_norm="python")], raw_text="text")
    calls = {"extract": 0, "activate": 0}

    monkeypatch.setattr("apps.web.routes.vacancies.get_user_vacancy_or_raise", lambda db, user_id, vacancy_id: vacancy)

    def _extract(db, user_id, vacancy_id):
        calls["extract"] += 1
        return extracted

    monkeypatch.setattr("apps.web.routes.vacancies.extract_and_save_requirements", _extract)
    monkeypatch.setattr(
        "apps.web.routes.vacancies.activate_vacancy_for_user",
        lambda db, user_id, vacancy_id: calls.__setitem__("activate", calls["activate"] + 1),
    )

    response = auth_client.post("/vacancies/5/extract", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"].startswith("/result?vacancy_id=5&message=")
    assert "message_type=success" in response.headers["location"]
    assert calls == {"extract": 1, "activate": 1}


def test_result_page_with_selected_vacancy_does_not_run_analysis_until_user_starts_it(auth_client, monkeypatch):
    vacancy = SimpleNamespace(id=7, title="Python Backend-разработчик", raw_text="Описание вакансии", requirements=[])

    monkeypatch.setattr("apps.web.routes.results.list_user_vacancies", lambda db, user_id: [vacancy])
    monkeypatch.setattr("apps.web.routes.results.load_vacancy_raw_text", lambda vacancy_obj: vacancy_obj.raw_text)
    monkeypatch.setattr("apps.web.routes.results.get_user_profile", lambda db, user_id: SimpleNamespace(id=1))
    monkeypatch.setattr("apps.web.routes.results.get_user_vacancy_or_raise", lambda db, user_id, vacancy_id: vacancy)
    monkeypatch.setattr(
        "apps.web.routes.results._prepare_result_page_data",
        lambda *args, **kwargs: pytest.fail("Анализ не должен запускаться без run=1"),
    )

    response = auth_client.get("/result?vacancy_id=7")

    assert response.status_code == 200
    assert "Вакансия выбрана." in response.text
    assert "Python Backend-разработчик" in response.text
    assert "Итоговый match" not in response.text


def test_profile_create_allows_missing_soft_required_fields(auth_client, monkeypatch):
    captured = {}

    def _create_user_profile(db, user_id, form_data):
        captured["form_data"] = form_data
        return SimpleNamespace(id=21)

    monkeypatch.setattr("apps.web.routes.profiles_dynamic.create_user_profile", _create_user_profile)

    response = auth_client.post(
        "/profiles/create",
        data={
            "full_name": "Иван Тестов",
            "target_position": "Backend-разработчик",
            "email": "ivan@example.com",
            "phone": "+7 900 000-00-00",
            "city": "",
            "work_format": "",
            "summary_text": "",
            "skills_text": "",
            "project_name": "Pet API",
            "project_stack": "Python, FastAPI",
            "project_role": "Разработчик",
            "project_description": "Учебный API-сервис",
            "project_result": "Подготовил рабочий прототип",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"].startswith("/profiles/21?message=")
    assert captured["form_data"].city is None
    assert captured["form_data"].work_format is None
    assert captured["form_data"].summary_text is None
    assert captured["form_data"].skills_text is None


def test_export_pdf_redirects_back_with_warning_when_latex_fails(auth_client, monkeypatch):
    resume = SimpleNamespace(id=11, vacancy_id=4, resume_json={}, pdf_path=None)

    monkeypatch.setattr(
        "apps.web.routes.results.get_generated_resume_by_id_for_user",
        lambda db, user_id, resume_id: resume,
    )
    monkeypatch.setattr(
        "apps.web.routes.results.compile_pdf",
        lambda resume_obj: (_ for _ in ()).throw(PdfServiceError("LaTeX не смог собрать PDF.")),
    )

    response = auth_client.get("/result/resume/11/export-pdf", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"].startswith("/result?vacancy_id=4&resume_id=11&run=1&message=")
    assert "message_type=warning" in response.headers["location"]


def test_vacancy_create_requires_title_for_manual_text(auth_client):
    response = auth_client.post(
        "/vacancies/create",
        data={
            "title": "",
            "source_url": "",
            "raw_text": "Требования: Python, FastAPI",
        },
    )

    assert response.status_code == 400
    assert "Если вы вставляете текст вакансии вручную, укажите её название." in response.text


def test_vacancy_create_requires_text_for_non_hh_url(auth_client):
    response = auth_client.post(
        "/vacancies/create",
        data={
            "title": "",
            "source_url": "https://example.com/jobs/1",
            "raw_text": "",
        },
    )

    assert response.status_code == 400
    assert "Для ссылок не с hh.ru вставьте текст вакансии в поле ниже." in response.text


def test_vacancy_create_allows_hh_url_without_manual_title(auth_client, monkeypatch):
    created_vacancy = SimpleNamespace(id=17, raw_text="text", requirements=[SimpleNamespace(skill_norm="python")])

    monkeypatch.setattr(
        "apps.web.routes.vacancies.create_user_vacancy",
        lambda db, user_id, form_data, contact_email: created_vacancy,
    )
    monkeypatch.setattr(
        "apps.web.routes.vacancies.extract_and_save_requirements",
        lambda db, user_id, vacancy_id: created_vacancy,
    )
    monkeypatch.setattr(
        "apps.web.routes.vacancies.is_auto_extract_placeholder",
        lambda raw_text: False,
    )

    response = auth_client.post(
        "/vacancies/create",
        data={
            "title": "",
            "source_url": "https://hh.ru/vacancy/123456",
            "raw_text": "",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"].startswith("/vacancies?message=")


def test_revise_resume_with_blank_instruction_redirects_back_with_warning(auth_client, monkeypatch):
    resume = SimpleNamespace(id=12, vacancy_id=5)

    monkeypatch.setattr(
        "apps.web.routes.results.get_generated_resume_by_id_for_user",
        lambda db, user_id, resume_id: resume,
    )
    monkeypatch.setattr(
        "apps.web.routes.results.revise_and_store_resume",
        lambda *args, **kwargs: pytest.fail("Пустая правка не должна запускать обновление резюме"),
    )

    response = auth_client.post("/result/resume/12/revise", data={"instruction": "   "}, follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"].startswith("/result?vacancy_id=5&resume_id=12&run=1&message=")
    assert "message_type=warning" in response.headers["location"]


def test_revise_cover_letter_with_blank_instruction_redirects_back_with_warning(auth_client, monkeypatch):
    letter = SimpleNamespace(id=4, vacancy_id=9)

    monkeypatch.setattr(
        "apps.web.routes.results.get_cover_letter_by_id_for_user",
        lambda db, user_id, letter_id: letter,
    )
    monkeypatch.setattr(
        "apps.web.routes.results.revise_and_store_cover_letter",
        lambda *args, **kwargs: pytest.fail("Пустая правка не должна запускать обновление письма"),
    )

    response = auth_client.post("/result/cover-letter/4/revise", data={"instruction": ""}, follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"].startswith("/result?vacancy_id=9&run=1&message=")
    assert "message_type=warning" in response.headers["location"]


def test_logout_clears_auth_cookie(client):
    response = client.post("/logout", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"].startswith("/login?message=")
    assert "set-cookie" in response.headers
    assert "Max-Age=0" in response.headers["set-cookie"] or "expires=" in response.headers["set-cookie"].lower()
