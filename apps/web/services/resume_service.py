"""
Генерация итогового resume_json без галлюцинаций.
"""
from __future__ import annotations

import re
import time
from typing import Any

from sqlalchemy.orm import Session

from apps.api.db.models import GeneratedResume, Profile, Vacancy
from apps.api.repositories.resume_repository import create_generated_resume
from apps.web.services.llm_service import LlmServiceError, generate_resume_with_llm, revise_resume_with_llm
from apps.web.services.match_service import LOW_MATCH_WARNING_THRESHOLD
from apps.web.services.pdf_service import render_latex_resume
from core.config import settings
from core.utils import display_skill_name, is_soft_skill, normalize_skill


class ResumeServiceError(Exception):
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


def _split_inline_items(value: str | None, *, limit: int | None = None) -> list[str]:
    if not value:
        return []
    items = [chunk.strip() for chunk in value.replace("\n", ",").replace(";", ",").split(",") if chunk.strip()]
    return items[:limit] if limit is not None else items


def _text_points(value: str | None, *, limit: int | None = None) -> list[str]:
    lines = _split_lines(value)
    if len(lines) == 1 and len(lines[0]) > 180:
        split_lines = [chunk.strip() for chunk in re.split(r"(?<=[.!?])\s+", lines[0]) if chunk.strip()]
        if len(split_lines) > 1:
            lines = split_lines
    return lines[:limit] if limit is not None else lines


def _period_from_entry(entry: dict[str, Any]) -> str:
    period = entry.get("period")
    if period:
        return str(period)
    return " - ".join(str(part).strip() for part in [entry.get("start"), entry.get("end")] if part)


def _structured_experience_entries(profile: Profile) -> list[Any]:
    result: list[dict[str, Any]] = []
    for entry in getattr(profile, "experience_entries", None) or []:
        if not isinstance(entry, dict):
            continue
        item = {
            "company": entry.get("company"),
            "city": entry.get("city") or getattr(profile, "city", None),
            "period": _period_from_entry(entry),
            "position": entry.get("position"),
            "tasks": _text_points(entry.get("tasks"), limit=6),
            "achievements": _text_points(entry.get("achievements"), limit=3),
        }
        if any(item.get(key) for key in ("company", "position", "tasks", "achievements")):
            result.append(item)
    return result or _split_lines(getattr(profile, "experience_text", None), limit=12)


def _structured_project_entries(profile: Profile) -> list[Any]:
    result: list[dict[str, Any]] = []
    for entry in getattr(profile, "project_entries", None) or []:
        if not isinstance(entry, dict):
            continue
        item = {
            "name": entry.get("name"),
            "stack": _split_inline_items(entry.get("stack")),
            "role": entry.get("role"),
            "description": entry.get("description"),
            "result": entry.get("result"),
        }
        if any(item.get(key) for key in ("name", "description", "result", "stack")):
            result.append(item)
    return result or _split_lines(getattr(profile, "projects_text", None), limit=10)


def _education_items(profile: Profile) -> list[str]:
    result: list[str] = []
    for entry in getattr(profile, "education_entries", None) or []:
        if not isinstance(entry, dict):
            continue
        period = _period_from_entry(entry)
        title = ", ".join(str(part).strip() for part in [entry.get("program"), entry.get("org")] if part)
        details = entry.get("details")
        text = " — ".join(str(part).strip() for part in [title, period, details] if part)
        if text:
            result.append(text)
    return result or _split_lines(getattr(profile, "education_text", None), limit=6)


def _course_items(profile: Profile) -> list[str]:
    result: list[str] = []
    for entry in getattr(profile, "course_entries", None) or []:
        if not isinstance(entry, dict):
            continue
        title = ", ".join(str(part).strip() for part in [entry.get("name"), entry.get("provider")] if part)
        details = " — ".join(str(part).strip() for part in [entry.get("year"), entry.get("details")] if part)
        text = " — ".join(part for part in [title, details] if part)
        if text:
            result.append(text)
    return result or _split_lines(getattr(profile, "certificates_text", None), limit=8)


def _additional_items(profile: Profile) -> list[str]:
    result = _split_lines(getattr(profile, "languages_text", None), limit=6)
    for entry in getattr(profile, "activity_entries", None) or []:
        if not isinstance(entry, dict):
            continue
        title = ", ".join(str(part).strip() for part in [entry.get("name"), entry.get("type")] if part)
        details = " — ".join(str(part).strip() for part in [entry.get("year"), entry.get("result")] if part)
        text = " — ".join(part for part in [title, details] if part)
        if text:
            result.append(text)
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


def _target_position(profile: Profile, vacancy: Vacancy) -> str:
    return (vacancy.title or profile.target_position or "").strip()


def _is_low_match(match_result: dict[str, Any]) -> bool:
    try:
        return float(match_result.get("total_pct", 0.0)) < LOW_MATCH_WARNING_THRESHOLD
    except (TypeError, ValueError):
        return False


def _unique_skills(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        skill = normalize_skill(str(item))
        if skill and skill not in seen:
            result.append(skill)
            seen.add(skill)
    return result


def _matched_requirement_items(match_result: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item
        for item in match_result.get("matched", []) + match_result.get("partial", [])
        if isinstance(item, dict) and item.get("skill_norm")
    ]


def _relevant_skill_norms(match_result: dict[str, Any], *, include_soft: bool) -> list[str]:
    skills: list[str] = []
    for item in _matched_requirement_items(match_result):
        skill = str(item.get("skill_norm") or "")
        if include_soft or not is_soft_skill(skill):
            skills.append(skill)
    return _unique_skills(skills)


def _strong_relevance_terms(match_result: dict[str, Any]) -> set[str]:
    weak_categories = {"other", "soft_skill"}
    terms: set[str] = set()
    for item in _matched_requirement_items(match_result):
        skill = normalize_skill(str(item.get("skill_norm") or ""))
        category = str(item.get("category") or "").strip().lower()
        if not skill or category in weak_categories:
            continue
        terms.add(skill)
        display_name = str(item.get("display_name") or "").strip()
        if display_name:
            terms.add(display_name.lower().replace("ё", "е"))
    return terms


def _entry_text(entry: Any) -> str:
    if isinstance(entry, dict):
        parts: list[str] = []
        for value in entry.values():
            if isinstance(value, list):
                parts.extend(str(item) for item in value if item)
            elif value:
                parts.append(str(value))
        return " ".join(parts).lower().replace("ё", "е")
    return str(entry or "").lower().replace("ё", "е")


def _contains_relevance_term(entry: Any, terms: set[str]) -> bool:
    text = _entry_text(entry)
    return any(term and term in text for term in terms)


def _filter_entries_by_relevance(entries: Any, terms: set[str]) -> list[Any]:
    if not terms:
        return []
    return [entry for entry in entries or [] if _contains_relevance_term(entry, terms)]


def _filter_skill_values(values: Any, allowed_skills: set[str]) -> list[str]:
    if not allowed_skills:
        return []

    result: list[str] = []
    seen: set[str] = set()
    raw_values = values if isinstance(values, list) else [values]
    for raw_value in raw_values:
        for chunk in str(raw_value or "").replace(";", ",").split(","):
            skill = normalize_skill(chunk)
            if skill in allowed_skills and skill not in seen:
                display_name = display_skill_name(skill)
                if display_name == skill:
                    display_name = chunk.strip()
                result.append(display_name)
                seen.add(skill)
    return result


def _sanitize_resume_json_for_context(
    resume_json: dict[str, Any],
    *,
    profile: Profile,
    vacancy: Vacancy,
    match_result: dict[str, Any],
) -> dict[str, Any]:
    resume_json["target_position"] = _target_position(profile, vacancy)

    if not _is_low_match(match_result):
        return resume_json

    allowed_non_soft = set(_relevant_skill_norms(match_result, include_soft=False))
    allowed_soft = {skill for skill in _relevant_skill_norms(match_result, include_soft=True) if is_soft_skill(skill)}
    strong_terms = _strong_relevance_terms(match_result)

    skills = resume_json.get("skills")
    if not isinstance(skills, dict):
        skills = {}
    resume_json["skills"] = {
        "relevant": _filter_skill_values(skills.get("relevant"), allowed_non_soft),
        "other": [],
        "soft": _filter_skill_values(skills.get("soft"), allowed_soft)[:3],
    }

    resume_json["experience"] = _filter_entries_by_relevance(resume_json.get("experience"), strong_terms)
    resume_json["projects"] = _filter_entries_by_relevance(resume_json.get("projects"), strong_terms)
    resume_json["certificates"] = _filter_entries_by_relevance(resume_json.get("certificates"), strong_terms)

    if not strong_terms:
        resume_json["summary"] = []

    notes = resume_json.setdefault("ats_notes", {})
    if isinstance(notes, dict):
        notes["low_match_content_policy"] = (
            "При низком match оставлены только явно релевантные подтвержденные данные; "
            "нерелевантные разделы могут быть пустыми."
        )
    return resume_json


def build_resume_json(
    profile: Profile,
    vacancy: Vacancy,
    match_result: dict[str, Any],
    recommendations: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    matched_skills = [item["skill_norm"] for item in match_result["matched"] if not is_soft_skill(item["skill_norm"])]
    partial_skills = [item["skill_norm"] for item in match_result["partial"] if not is_soft_skill(item["skill_norm"])]
    relevant_skills = []
    for skill in matched_skills + partial_skills:
        if skill not in relevant_skills:
            relevant_skills.append(skill)
    soft_skills = [item["skill_norm"] for item in match_result["matched"] + match_result["partial"] if is_soft_skill(item["skill_norm"])]
    if not soft_skills:
        soft_skills = _split_inline_items(getattr(profile, "soft_skills_text", None), limit=3)

    summary = _split_lines(profile.summary_text)
    if not summary:
        summary = [
            f"{profile.full_name} претендует на позицию {_target_position(profile, vacancy)}.",
            "Резюме сформировано только на основе данных, заполненных пользователем.",
        ]
    resume_json = {
        "contact": {
            "full_name": profile.full_name,
            "email": profile.email,
            "phone": profile.phone,
            "city": getattr(profile, "city", None),
            "work_format": getattr(profile, "work_format", None),
            "github_url": profile.github_url,
            "linkedin_url": profile.linkedin_url,
        },
        "target_position": _target_position(profile, vacancy),
        "summary": summary[:4],
        "skills": {
            "relevant": relevant_skills[:30],
            "other": [],
            "soft": soft_skills[:3],
        },
        "experience": _structured_experience_entries(profile),
        "projects": _structured_project_entries(profile),
        "education": _education_items(profile),
        "certificates": _course_items(profile),
        "languages": _additional_items(profile),
        "ats_notes": {
            "match_total_pct": match_result["total_pct"],
            "must_pct": match_result["must_pct"],
            "nice_pct": match_result["nice_pct"],
            "resume_edit_recommendations": recommendations["resume_edit"][:5],
            "generation_rule": "Не добавлялись факты, навыки и метрики, отсутствующие в профиле пользователя.",
        },
    }
    return _sanitize_resume_json_for_context(resume_json, profile=profile, vacancy=vacancy, match_result=match_result)


def generate_resume_json(
    profile: Profile,
    vacancy: Vacancy,
    match_result: dict[str, Any],
    recommendations: dict[str, list[dict[str, Any]]],
    metrics_mode: str = "strict",
) -> dict[str, Any]:
    metrics_mode = "assist" if metrics_mode == "assist" else "strict"
    fallback_resume = build_resume_json(profile, vacancy, match_result, recommendations)
    fallback_resume.setdefault("ats_notes", {})["metrics_mode"] = metrics_mode
    if not settings.deepseek_api_key:
        return _mark_generation_status(fallback_resume, status="fallback_no_key")

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
        "fallback_resume": fallback_resume,
        "generation_options": {
            "metrics_mode": metrics_mode,
            "low_match": _is_low_match(match_result),
            "target_position_rule": "use_exact_vacancy_title",
        },
    }

    last_error: str | None = None
    llm_resume = None
    for attempt in range(3):
        try:
            llm_resume = generate_resume_with_llm(payload)
            break
        except LlmServiceError as exc:
            last_error = str(exc)
            if attempt == 2:
                return _mark_generation_status(fallback_resume, status="fallback_error", error=last_error)
            time.sleep(1.5 * (attempt + 1))

    if not isinstance(llm_resume, dict):
        return _mark_generation_status(fallback_resume, status="fallback_invalid_response")

    _merge_missing_resume_fields(llm_resume, fallback_resume)
    llm_resume = _sanitize_resume_json_for_context(llm_resume, profile=profile, vacancy=vacancy, match_result=match_result)
    return _mark_generation_status(llm_resume, status="deepseek")


def _mark_generation_status(resume_json: dict[str, Any], *, status: str, error: str | None = None) -> dict[str, Any]:
    ats_notes = resume_json.setdefault("ats_notes", {})
    if not isinstance(ats_notes, dict):
        ats_notes = {}
        resume_json["ats_notes"] = ats_notes

    ats_notes["llm_status"] = status
    if error:
        ats_notes["llm_error"] = error
    return resume_json


def _merge_missing_resume_fields(resume_json: dict[str, Any], fallback_resume: dict[str, Any]) -> None:
    for key in (
        "contact",
        "target_position",
        "ats_notes",
    ):
        if key not in resume_json or resume_json[key] in (None, "", {}):
            resume_json[key] = fallback_resume.get(key)

    for key in (
        "summary",
        "skills",
        "experience",
        "projects",
        "education",
        "certificates",
        "languages",
    ):
        if key not in resume_json or resume_json[key] is None:
            resume_json[key] = fallback_resume.get(key)


def generate_and_store_resume(
    db: Session,
    *,
    user_id: int,
    profile: Profile,
    vacancy: Vacancy,
    match_result: dict[str, Any],
    recommendations: dict[str, list[dict[str, Any]]],
    metrics_mode: str = "strict",
) -> GeneratedResume:
    resume_json = generate_resume_json(profile, vacancy, match_result, recommendations, metrics_mode=metrics_mode)
    ats_notes = resume_json.get("ats_notes", {}) if isinstance(resume_json, dict) else {}
    llm_status = ats_notes.get("llm_status") if isinstance(ats_notes, dict) else None
    if llm_status != "deepseek":
        error_message = ats_notes.get("llm_error") if isinstance(ats_notes, dict) else None
        if llm_status == "fallback_no_key":
            raise ResumeServiceError("Генерация резюме временно недоступна: не настроен модуль генерации.")
        if error_message:
            raise ResumeServiceError(
                "РезюмИТ не смог подготовить резюме через интеллектуальный модуль. "
                "Попробуйте повторить генерацию через минуту."
            )
        raise ResumeServiceError(
            "РезюмИТ не смог подготовить резюме в нужном формате. Попробуйте повторить генерацию."
        )

    latex_source = render_latex_resume(resume_json)
    return create_generated_resume(
        db,
        user_id=user_id,
        profile_id=profile.id,
        vacancy_id=vacancy.id,
        resume_json=resume_json,
        latex_source=latex_source,
    )


def revise_and_store_resume(
    db: Session,
    *,
    user_id: int,
    resume: GeneratedResume,
    instruction: str,
) -> GeneratedResume:
    if not instruction.strip():
        raise ResumeServiceError("Напишите, что именно нужно изменить в резюме.")
    if not settings.deepseek_api_key:
        raise ResumeServiceError("Правки естественным языком временно недоступны: не настроен модуль генерации.")

    try:
        revised_json = revise_resume_with_llm(resume.resume_json, instruction.strip())
    except LlmServiceError as exc:
        raise ResumeServiceError(str(exc)) from exc

    latex_source = render_latex_resume(revised_json)
    return create_generated_resume(
        db,
        user_id=user_id,
        profile_id=resume.profile_id,
        vacancy_id=resume.vacancy_id,
        resume_json=revised_json,
        latex_source=latex_source,
    )
