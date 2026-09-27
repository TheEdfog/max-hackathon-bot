from apps.web.services.hh_service import extract_hh_vacancy_from_url, is_hh_vacancy_url


class _FakeResponse:
    status_code = 200

    def json(self):
        return {
            "name": "Python Backend-разработчик",
            "employer": {"name": "Тестовая компания"},
            "area": {"name": "Москва"},
            "experience": {"name": "Нет опыта"},
            "employment": {"name": "Полная занятость"},
            "schedule": {"name": "Удалённая работа"},
            "key_skills": [{"name": "Python"}, {"name": "FastAPI"}],
            "professional_roles": [{"name": "Программист, разработчик"}],
            "description": "<p>Разработка API</p><ul><li>Работа с PostgreSQL</li></ul>",
            "alternate_url": "https://hh.ru/vacancy/123456",
        }


def test_hh_url_detection_accepts_only_hh_vacancies():
    assert is_hh_vacancy_url("https://hh.ru/vacancy/123456?query=python")
    assert is_hh_vacancy_url("https://spb.hh.ru/vacancy/123456")
    assert not is_hh_vacancy_url("https://example.com/vacancy/123456")
    assert not is_hh_vacancy_url("https://hh.ru/search/vacancy?text=python")


def test_extract_hh_vacancy_formats_api_payload(monkeypatch):
    def fake_get(vacancy_id, headers):
        assert vacancy_id == "123456"
        assert "RezumIT/1.0" in headers["User-Agent"]
        return _FakeResponse()

    monkeypatch.setattr("apps.web.services.hh_service._get_hh_vacancy_response", fake_get)

    vacancy = extract_hh_vacancy_from_url(
        "https://hh.ru/vacancy/123456?query=python",
        contact_email="user@example.com",
    )

    assert vacancy.title == "Python Backend-разработчик"
    assert "Ключевые навыки: Python, FastAPI" in vacancy.raw_text
    assert "Описание вакансии:" in vacancy.raw_text
    assert "Работа с PostgreSQL" in vacancy.raw_text
