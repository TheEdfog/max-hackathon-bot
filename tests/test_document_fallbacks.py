from __future__ import annotations

from types import SimpleNamespace

import pytest

from apps.web.services import cover_letter_service, resume_service
from apps.web.services.cover_letter_service import (
    CoverLetterServiceError,
    generate_cover_letter_json,
    revise_and_store_cover_letter,
)
from apps.web.services.llm_service import LlmServiceError
from apps.web.services.pdf_service import PdfServiceError, compile_pdf
from apps.web.services.resume_service import generate_resume_json


def _fake_profile():
    return SimpleNamespace(
        id=1,
        full_name="Иван Соловьев",
        target_position="Backend-разработчик",
        email="ivan@example.com",
        phone="+7 999 000-00-00",
        city="Москва",
        work_format="удаленно",
        salary_expectation=None,
        github_url=None,
        linkedin_url=None,
        summary_text="Backend-разработчик с опытом API и PostgreSQL.",
        experience_text="Разрабатывал backend-сервисы.",
        projects_text="Учебный проект на FastAPI.",
        education_text="МИСИС, ИТ.",
        certificates_text=None,
        languages_text="Русский, Английский B1",
        soft_skills_text="Коммуникация",
        experience_entries=[],
        project_entries=[],
        education_entries=[],
        course_entries=[],
        activity_entries=[],
        skills=[SimpleNamespace(skill_norm="python"), SimpleNamespace(skill_norm="fastapi")],
    )


def _fake_vacancy():
    requirement = SimpleNamespace(skill_norm="python", type=SimpleNamespace(value="must"))
    return SimpleNamespace(
        id=10,
        title="Python Backend-разработчик",
        source_url="https://example.com/vacancy/10",
        raw_text="Требования: Python, FastAPI, PostgreSQL.",
        requirements=[requirement],
    )


def _fake_match_result():
    return {
        "total_pct": 82.5,
        "must_pct": 80.0,
        "nice_pct": 90.0,
        "matched": [{"skill_norm": "python", "display_name": "Python"}],
        "partial": [{"skill_norm": "fastapi", "display_name": "FastAPI"}],
    }


def _fake_recommendations():
    return {"resume_edit": [], "learn_and_practice": []}


def test_resume_generation_uses_fallback_when_llm_key_is_missing(monkeypatch):
    monkeypatch.setattr(resume_service.settings, "deepseek_api_key", "")

    resume_json = generate_resume_json(
        _fake_profile(),
        _fake_vacancy(),
        _fake_match_result(),
        _fake_recommendations(),
    )

    assert resume_json["ats_notes"]["llm_status"] == "fallback_no_key"
    assert resume_json["target_position"] == "Python Backend-разработчик"
    assert "python" in resume_json["skills"]["relevant"]


def test_resume_generation_retries_before_fallback(monkeypatch):
    monkeypatch.setattr(resume_service.settings, "deepseek_api_key", "test-key")

    attempts = {"count": 0}

    def flaky_llm(payload):
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise LlmServiceError("Интеллектуальный модуль временно недоступен.")
        return {
            "contact": {"full_name": "Иван Соловьев", "email": "ivan@example.com", "phone": "+7 999 000-00-00"},
            "target_position": "Python Backend-разработчик",
            "summary": ["Краткое резюме"],
            "skills": {"relevant": ["python"], "other": [], "soft": []},
            "experience": [],
            "projects": [],
            "education": [],
            "certificates": [],
            "languages": [],
            "ats_notes": {},
        }

    monkeypatch.setattr("apps.web.services.resume_service.generate_resume_with_llm", flaky_llm)
    monkeypatch.setattr("apps.web.services.resume_service.time.sleep", lambda _: None)

    resume_json = generate_resume_json(
        _fake_profile(),
        _fake_vacancy(),
        _fake_match_result(),
        _fake_recommendations(),
    )

    assert attempts["count"] == 3
    assert resume_json["ats_notes"]["llm_status"] == "deepseek"


def test_resume_generation_forces_exact_vacancy_title_from_llm(monkeypatch):
    monkeypatch.setattr(resume_service.settings, "deepseek_api_key", "test-key")

    def wrong_title_llm(payload):
        return {
            "contact": {"full_name": "Иван Соловьев", "email": "ivan@example.com", "phone": "+7 999 000-00-00"},
            "target_position": "Backend-разработчик",
            "summary": ["Краткое резюме"],
            "skills": {"relevant": ["python"], "other": [], "soft": []},
            "experience": [],
            "projects": [],
            "education": [],
            "certificates": [],
            "languages": [],
            "ats_notes": {},
        }

    monkeypatch.setattr("apps.web.services.resume_service.generate_resume_with_llm", wrong_title_llm)

    resume_json = generate_resume_json(
        _fake_profile(),
        SimpleNamespace(**{**_fake_vacancy().__dict__, "title": "Менеджер по первичным продажам в ИТ / SDR"}),
        _fake_match_result(),
        _fake_recommendations(),
    )

    assert resume_json["target_position"] == "Менеджер по первичным продажам в ИТ / SDR"


def test_low_match_resume_drops_irrelevant_profile_sections(monkeypatch):
    monkeypatch.setattr(resume_service.settings, "deepseek_api_key", "test-key")

    def noisy_llm(payload):
        return {
            "contact": {"full_name": "Иван Соловьев", "email": "ivan@example.com", "phone": "+7 999 000-00-00"},
            "target_position": "Backend-разработчик",
            "summary": ["Backend-разработчик с опытом C# и .NET."],
            "skills": {
                "relevant": ["Информационные технологии", "C#", ".NET"],
                "other": ["FastAPI", "PostgreSQL", "Docker"],
                "soft": ["Коммуникация"],
            },
            "experience": [
                {
                    "company": "Учебная лаборатория",
                    "position": "Backend-разработчик",
                    "tasks": ["Разрабатывал API на C#"],
                    "achievements": ["Собрал backend-прототип"],
                }
            ],
            "projects": [{"name": "TaskFlow API", "stack": ["C#", ".NET"], "description": "Backend API"}],
            "education": ["МИСИС, ИТ"],
            "certificates": ["C# и .NET Core"],
            "languages": ["Русский"],
            "ats_notes": {},
        }

    low_match = {
        "total_pct": 6.1,
        "must_pct": 8.2,
        "nice_pct": 0.0,
        "matched": [{"skill_norm": "информационные технологии", "display_name": "Информационные технологии", "category": "other"}],
        "partial": [],
    }
    vacancy = SimpleNamespace(**{**_fake_vacancy().__dict__, "title": "Менеджер по первичным продажам в ИТ / SDR"})
    monkeypatch.setattr("apps.web.services.resume_service.generate_resume_with_llm", noisy_llm)

    resume_json = generate_resume_json(_fake_profile(), vacancy, low_match, _fake_recommendations())

    assert resume_json["target_position"] == "Менеджер по первичным продажам в ИТ / SDR"
    assert resume_json["skills"]["relevant"] == ["Информационные технологии"]
    assert resume_json["skills"]["other"] == []
    assert resume_json["experience"] == []
    assert resume_json["projects"] == []
    assert resume_json["certificates"] == []
    assert resume_json["summary"] == []


def test_cover_letter_generation_marks_fallback_error_when_llm_fails(monkeypatch):
    monkeypatch.setattr(cover_letter_service.settings, "deepseek_api_key", "test-key")
    monkeypatch.setattr(
        "apps.web.services.cover_letter_service.generate_cover_letter_with_llm",
        lambda payload: (_ for _ in ()).throw(LlmServiceError("Интеллектуальный модуль временно недоступен.")),
    )

    letter_json = generate_cover_letter_json(
        _fake_profile(),
        _fake_vacancy(),
        _fake_match_result(),
        _fake_recommendations(),
    )

    assert letter_json["notes"]["llm_status"] == "fallback_error"
    assert "Интеллектуальный модуль временно недоступен." in letter_json["notes"]["llm_error"]


def test_cover_letter_revision_requires_llm_module(monkeypatch):
    monkeypatch.setattr(cover_letter_service.settings, "deepseek_api_key", "")
    letter = SimpleNamespace(letter_json={"paragraphs": ["Текст"]}, profile_id=1, vacancy_id=10)

    with pytest.raises(CoverLetterServiceError, match="Правки естественным языком временно недоступны"):
        revise_and_store_cover_letter(
            db=SimpleNamespace(),
            user_id=1,
            letter=letter,
            instruction="Сделай письмо короче",
        )


def test_compile_pdf_reports_missing_latex_engine(monkeypatch):
    monkeypatch.setattr("apps.web.services.pdf_service._resolve_latex_engine", lambda: "definitely-missing-latex")

    with pytest.raises(PdfServiceError, match="LaTeX-движок"):
        compile_pdf(SimpleNamespace(id=1, resume_json={}, latex_source=""))
