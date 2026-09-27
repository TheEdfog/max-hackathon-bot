from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from apps.api.db.models import GeneratedCoverLetter, Profile, Vacancy
from apps.api.repositories.cover_letter_repository import upsert_generated_cover_letter
from apps.web.services.llm_service import (
    LlmServiceError,
    generate_cover_letter_with_llm,
    revise_cover_letter_with_llm,
)
from apps.web.services.pdf_service import render_latex_cover_letter
from core.config import settings


class CoverLetterServiceError(Exception):
    pass


def _split_lines(value: str | None, *, limit: int | None = None) -> list[str]:
    if not value:
        return []

    result: list[str] = []
    for raw_line in value.replace("\r", "\n").split("\n"):
        line = raw_line.strip().strip("-•*").strip()
        if line:
            result.append(line)
        if limit is not None and len(result) >= limit:
            break
    return result


def _profile_payload(profile: Profile) -> dict[str, Any]:
    return {
        "full_name": profile.full_name,
        "target_position": profile.target_position,
        "email": profile.email,
        "phone": profile.phone,
        "city": getattr(profile, "city", None),
        "work_format": getattr(profile, "work_format", None),
        "salary_expectation": getattr(profile, "salary_expectation", None),
        "github_url": profile.github_url,
        "linkedin_url": profile.linkedin_url,
        "summary_text": profile.summary_text,
        "experience_text": getattr(profile, "experience_text", None),
        "projects_text": getattr(profile, "projects_text", None),
        "education_text": getattr(profile, "education_text", None),
        "certificates_text": getattr(profile, "certificates_text", None),
        "languages_text": getattr(profile, "languages_text", None),
        "soft_skills_text": getattr(profile, "soft_skills_text", None),
        "experience_entries": getattr(profile, "experience_entries", None) or [],
        "project_entries": getattr(profile, "project_entries", None) or [],
        "education_entries": getattr(profile, "education_entries", None) or [],
        "course_entries": getattr(profile, "course_entries", None) or [],
        "activity_entries": getattr(profile, "activity_entries", None) or [],
        "skills": [skill.skill_norm for skill in profile.skills],
    }


def _profile_source_text(profile: Profile) -> str:
    parts = [
        f"ФИО: {profile.full_name}",
        f"Целевая должность: {profile.target_position}",
        f"Город: {getattr(profile, 'city', None) or ''}",
        f"Формат работы: {getattr(profile, 'work_format', None) or ''}",
        f"О себе: {profile.summary_text or ''}",
        f"Soft skills: {getattr(profile, 'soft_skills_text', None) or ''}",
        f"Hard skills и компетенции: {', '.join(skill.skill_norm for skill in profile.skills)}",
        f"Опыт: {getattr(profile, 'experience_text', None) or ''}",
        f"Проекты: {getattr(profile, 'projects_text', None) or ''}",
        f"Образование: {getattr(profile, 'education_text', None) or ''}",
        f"Курсы и сертификаты: {getattr(profile, 'certificates_text', None) or ''}",
        f"Языки: {getattr(profile, 'languages_text', None) or ''}",
    ]
    return "\n\n".join(part for part in parts if part.strip())


def build_cover_letter_json(
    profile: Profile,
    vacancy: Vacancy,
    match_result: dict[str, Any],
    recommendations: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    matched = [item.get("display_name") or item.get("skill_norm") for item in match_result.get("matched", [])]
    partial = [item.get("display_name") or item.get("skill_norm") for item in match_result.get("partial", [])]
    relevant_skills = [skill for skill in matched + partial if skill][:8]
    summary = _split_lines(profile.summary_text, limit=2)
    vacancy_title = vacancy.title or profile.target_position

    intro = (
        f"Здравствуйте! Хочу откликнуться на позицию {vacancy_title}. "
        f"Мне интересна эта роль, потому что она близка к моему профилю {profile.target_position}."
    )
    if summary:
        value_paragraph = " ".join(summary)
    elif relevant_skills:
        value_paragraph = "Мой релевантный стек и компетенции: " + ", ".join(relevant_skills) + "."
    else:
        value_paragraph = "Готов подробнее обсудить, как мой опыт и учебные проекты могут быть полезны для этой роли."

    if relevant_skills:
        fit_paragraph = (
            "По результатам анализа наиболее близкие к вакансии навыки: "
            + ", ".join(relevant_skills)
            + ". В письме и резюме я оставляю только подтвержденные факты из профиля."
        )
    else:
        fit_paragraph = (
            "Я внимательно сопоставил требования вакансии со своим профилем и готов честно обсуждать как сильные стороны, "
            "так и зоны развития."
        )

    gaps = recommendations.get("learn_and_practice", [])[:2]
    if gaps:
        motivation = (
            "Если по отдельным требованиям нужен дополнительный практический опыт, я готов развивать эти зоны и закреплять их "
            "на учебных или pet-проектах."
        )
    else:
        motivation = "Буду рад обсудить, какие задачи стоят перед командой и как я смогу быстро включиться в работу."

    return {
        "candidate_name": profile.full_name,
        "vacancy_title": vacancy_title,
        "subject": f"Отклик на вакансию {vacancy_title}",
        "greeting": "Здравствуйте!",
        "paragraphs": [intro, value_paragraph, fit_paragraph, motivation],
        "closing": "Спасибо за внимание. Буду рад ответить на вопросы и обсудить следующий шаг.",
        "notes": {
            "match_total_pct": match_result.get("total_pct"),
            "must_pct": match_result.get("must_pct"),
            "nice_pct": match_result.get("nice_pct"),
            "llm_status": "fallback_no_key",
            "generation_rule": "Письмо собрано только из фактов профиля пользователя и текста вакансии.",
            "word_count_target": "250-400",
        },
    }


def generate_cover_letter_json(
    profile: Profile,
    vacancy: Vacancy,
    match_result: dict[str, Any],
    recommendations: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    fallback_letter = build_cover_letter_json(profile, vacancy, match_result, recommendations)
    if not settings.deepseek_api_key:
        return fallback_letter

    payload = {
        "profile": _profile_payload(profile),
        "profile_text": _profile_source_text(profile),
        "vacancy": {
            "title": vacancy.title,
            "source_url": vacancy.source_url,
            "raw_text": vacancy.raw_text[:8000],
            "requirements": [
                {"skill_norm": requirement.skill_norm, "type": requirement.type.value}
                for requirement in vacancy.requirements
            ],
        },
        "match_result": match_result,
        "recommendations": recommendations,
        "fallback_cover_letter": fallback_letter,
    }

    try:
        llm_letter = generate_cover_letter_with_llm(payload)
    except LlmServiceError as exc:
        fallback_letter.setdefault("notes", {})["llm_status"] = "fallback_error"
        fallback_letter.setdefault("notes", {})["llm_error"] = str(exc)
        return fallback_letter

    if not isinstance(llm_letter, dict):
        fallback_letter.setdefault("notes", {})["llm_status"] = "fallback_invalid_response"
        return fallback_letter

    _merge_missing_letter_fields(llm_letter, fallback_letter)
    notes = llm_letter.setdefault("notes", {})
    if isinstance(notes, dict):
        notes["llm_status"] = "deepseek"
    return llm_letter


def _merge_missing_letter_fields(letter_json: dict[str, Any], fallback_letter: dict[str, Any]) -> None:
    for key in ("candidate_name", "vacancy_title", "subject", "greeting", "paragraphs", "closing", "notes"):
        if key not in letter_json or letter_json[key] in (None, "", [], {}):
            letter_json[key] = fallback_letter.get(key)


def generate_and_store_cover_letter(
    db: Session,
    *,
    user_id: int,
    profile: Profile,
    vacancy: Vacancy,
    match_result: dict[str, Any],
    recommendations: dict[str, list[dict[str, Any]]],
) -> GeneratedCoverLetter:
    letter_json = generate_cover_letter_json(profile, vacancy, match_result, recommendations)
    latex_source = render_latex_cover_letter(letter_json)
    return upsert_generated_cover_letter(
        db,
        user_id=user_id,
        profile_id=profile.id,
        vacancy_id=vacancy.id,
        letter_json=letter_json,
        latex_source=latex_source,
    )


def revise_and_store_cover_letter(
    db: Session,
    *,
    user_id: int,
    letter: GeneratedCoverLetter,
    instruction: str,
) -> GeneratedCoverLetter:
    if not instruction.strip():
        raise CoverLetterServiceError("Напишите, что именно нужно изменить в сопроводительном письме.")
    if not settings.deepseek_api_key:
        raise CoverLetterServiceError("Правки естественным языком временно недоступны: не настроен модуль генерации.")

    try:
        revised_json = revise_cover_letter_with_llm(letter.letter_json, instruction.strip())
    except LlmServiceError as exc:
        raise CoverLetterServiceError(str(exc)) from exc

    _merge_missing_letter_fields(revised_json, letter.letter_json)
    latex_source = render_latex_cover_letter(revised_json)
    return upsert_generated_cover_letter(
        db,
        user_id=user_id,
        profile_id=letter.profile_id,
        vacancy_id=letter.vacancy_id,
        letter_json=revised_json,
        latex_source=latex_source,
    )
