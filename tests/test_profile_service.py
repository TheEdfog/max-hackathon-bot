from types import SimpleNamespace

from apps.api.schemas.profile import ProfileFormData
from apps.web.services.profile_service import (
    _profile_skills_from_form,
    get_profile_key_skills,
    merge_profile_context_skills,
)


def test_profile_save_uses_only_explicit_skill_fields():
    form_data = ProfileFormData(
        full_name="Иван Соловьев",
        target_position="Backend-разработчик",
        email="ivan@example.com",
        phone="+79990000000",
        city="Москва",
        work_format="удаленно",
        summary_text="Backend-разработчик, провожу юнит тесты и участвую в CodeReview.",
        soft_skills_text="коммуникация",
        skills_text="Python, FastApi",
    )

    skills = _profile_skills_from_form(form_data)

    assert "python" in skills
    assert "fastapi" in skills
    assert "unit testing" not in skills
    assert "code review" not in skills
    assert "communication" in skills


def test_profile_soft_skill_phrase_is_not_shown_as_key_skill():
    profile = SimpleNamespace(
        skills=[
            SimpleNamespace(skill_norm="python"),
            SimpleNamespace(skill_norm="находить решение к проблемам"),
        ]
    )

    skills = get_profile_key_skills(profile)

    assert [skill.skill_norm for skill in skills] == ["python"]


def test_profile_custom_soft_skill_phrase_is_normalized():
    form_data = ProfileFormData(
        full_name="Иван Соловьев",
        target_position="Backend-разработчик",
        email="ivan@example.com",
        phone="+79990000000",
        city="Москва",
        work_format="удаленно",
        soft_skills_text="находить решение к проблемам",
        skills_text="Python",
    )

    skills = _profile_skills_from_form(form_data)

    assert "problem solving" in skills
    assert "находить решение к проблемам" not in skills


def test_profile_context_skills_are_extracted_only_by_explicit_action():
    skills_text, added = merge_profile_context_skills(
        "Python",
        "Backend-разработчик, провожу юнит тесты, участвую в CodeReview и умею находить решение к проблемам.",
    )

    assert "python" in skills_text
    assert "unit testing" in skills_text
    assert "code review" in skills_text
    assert "problem solving" not in skills_text
    assert added == ["unit testing", "code review"]
