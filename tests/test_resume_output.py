import asyncio
from types import SimpleNamespace

from apps.web.routes.results import result_for_vacancy
from apps.web.services.pdf_service import render_latex_resume
from apps.web.services.resume_service import _structured_experience_entries


def test_structured_experience_uses_entry_city_or_profile_city():
    profile = SimpleNamespace(
        city="Москва",
        experience_entries=[
            {
                "company": "Тестовая компания",
                "position": "Backend-разработчик",
                "city": "",
                "start": "2025-03",
                "end": "2025-07",
                "tasks": "Разрабатывал API на FastAPI",
                "achievements": "Ускорил обработку запросов",
            }
        ],
        experience_text=None,
    )

    items = _structured_experience_entries(profile)

    assert items[0]["city"] == "Москва"
    assert items[0]["period"] == "2025-03 - 2025-07"


def test_render_latex_resume_shows_city_human_readable_period_and_underlined_links():
    resume_json = {
        "contact": {
            "full_name": "Иван Соловьев",
            "email": "ivan@example.com",
            "phone": "+7 999 000-00-00",
            "city": "Москва",
            "work_format": "удаленно",
            "github_url": "https://github.com/ivan-soloviev",
            "linkedin_url": "https://linkedin.com/in/ivan-soloviev",
        },
        "target_position": "Backend-разработчик",
        "summary": ["Разрабатываю backend-сервисы для веб-приложений."],
        "skills": {"relevant": ["python", "fastapi"], "other": [], "soft": []},
        "experience": [
            {
                "company": "Тестовая компания",
                "position": "Backend-разработчик",
                "city": "Москва",
                "period": "2025-03 - 2025-07",
                "tasks": ["Разрабатывал API на FastAPI"],
                "achievements": ["Ускорил обработку запросов"],
            }
        ],
        "projects": [],
        "education": [],
        "certificates": [],
        "languages": [],
    }

    latex = render_latex_resume(resume_json)

    assert "Москва" in latex
    assert "Март 2025" in latex
    assert "Июль 2025" in latex
    assert "\\fontsize{12pt}{14.2pt}\\selectfont" in latex
    assert "\\fontsize{12pt}{14pt}\\selectfont" in latex
    assert "\\fontsize{13pt}{15pt}\\selectfont\\bfseries\\itshape Backend-разработчик" in latex
    assert "\\bfseries\\itshape Backend-разработчик" in latex
    assert "\\bfseries\\itshape Тестовая компания" in latex
    assert "\\bfseries\\itshape Москва" in latex
    assert "Backend-разработчик, Тестовая компания" not in latex
    assert "ResumeLinkBlue" not in latex
    assert "urlcolor=" not in latex
    assert "\\newcommand{\\cvlinktext}[1]{\\underline{#1}}" in latex
    assert "\\href{\\detokenize{mailto:ivan@example.com}}{\\cvlinktext{ivan@example.com}}" in latex
    assert "\\href{\\detokenize{https://github.com/ivan-soloviev}}{\\cvlinktext{github.com/ivan-soloviev}}" in latex
    assert "\\href{\\detokenize{https://linkedin.com/in/ivan-soloviev}}{\\cvlinktext{linkedin.com/in/ivan-soloviev}}" in latex


def test_result_for_vacancy_opens_analysis_page_without_forced_run():
    response = asyncio.run(result_for_vacancy(7))

    assert response.status_code == 302
    assert response.headers["location"] == "/result?vacancy_id=7"
