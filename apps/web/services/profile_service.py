from __future__ import annotations

import stat

from sqlalchemy.orm import Session

from apps.api.db.models import Profile, ProfileSkill
from apps.api.repositories.profile_repository import (
    create_profile,
    delete_profile_for_user,
    get_profile_for_user,
    get_profile_by_id_for_user,
    list_profiles_for_user,
    set_active_profile_for_user,
    update_profile,
)
from apps.api.schemas.profile import ProfileFormData
from apps.web.services.vacancy_parser_service import extract_requirements_locally
from core.config import settings
from core.utils import SOFT_SKILL_LABELS, display_skill_name, normalize_skill, parse_skills_input


class ProfileError(Exception):
    pass


def _profile_skills_from_form(form_data: ProfileFormData) -> list[str]:
    skills = parse_skills_input(form_data.skills_text)
    seen = set(skills)
    for soft_skill in parse_skills_input(form_data.soft_skills_text):
        if soft_skill and soft_skill not in seen:
            skills.append(soft_skill)
            seen.add(soft_skill)
    return skills


def extract_profile_context_skills(context_text: str) -> list[str]:
    skills: list[str] = []
    seen: set[str] = set()
    for item in extract_requirements_locally(context_text):
        skill_norm = normalize_skill(str(item.get("skill_norm") or ""))
        if not skill_norm or skill_norm in SOFT_SKILL_LABELS or skill_norm in seen:
            continue
        skills.append(skill_norm)
        seen.add(skill_norm)
    return skills


def merge_profile_context_skills(skills_text: str | None, context_text: str) -> tuple[str, list[str]]:
    skills = parse_skills_input(skills_text)
    seen = set(skills)
    added: list[str] = []

    for skill_norm in extract_profile_context_skills(context_text):
        if skill_norm in seen:
            continue
        skills.append(skill_norm)
        seen.add(skill_norm)
        added.append(skill_norm)

    return ", ".join(skills), added


def list_user_profiles(db: Session, user_id: int) -> list[Profile]:
    return list_profiles_for_user(db, user_id)


def get_user_profile(db: Session, user_id: int) -> Profile | None:
    return get_profile_for_user(db, user_id=user_id)


def get_user_single_profile_or_raise(db: Session, user_id: int) -> Profile:
    profile = get_user_profile(db, user_id=user_id)
    if profile is None:
        raise ProfileError("Профиль пока не заполнен")
    return profile


def get_user_profile_or_raise(db: Session, user_id: int, profile_id: int) -> Profile:
    profile = get_profile_by_id_for_user(db, user_id=user_id, profile_id=profile_id)
    if profile is None:
        raise ProfileError("Профиль не найден")
    return profile


def get_profile_key_skills(profile: Profile | None, *, limit: int | None = None) -> list[ProfileSkill]:
    if profile is None:
        return []

    skills = [
        skill
        for skill in profile.skills
        if normalize_skill(skill.skill_norm) not in SOFT_SKILL_LABELS
    ]
    if limit is None:
        return skills
    return skills[:limit]


def create_user_profile(db: Session, user_id: int, form_data: ProfileFormData) -> Profile:
    existing_profile = get_user_profile(db, user_id=user_id)
    if existing_profile is not None:
        return update_user_profile(db, user_id=user_id, profile_id=existing_profile.id, form_data=form_data)

    skills = _profile_skills_from_form(form_data)

    profile = create_profile(
        db=db,
        user_id=user_id,
        full_name=form_data.full_name,
        target_position=form_data.target_position,
        email=form_data.email,
        phone=form_data.phone,
        city=form_data.city,
        work_format=form_data.work_format,
        salary_expectation=form_data.salary_expectation,
        github_url=form_data.github_url,
        linkedin_url=form_data.linkedin_url,
        summary_text=form_data.summary_text,
        experience_text=form_data.experience_text,
        projects_text=form_data.projects_text,
        education_text=form_data.education_text,
        certificates_text=form_data.certificates_text,
        languages_text=form_data.languages_text,
        soft_skills_text=form_data.soft_skills_text,
        experience_entries=form_data.experience_entries,
        project_entries=form_data.project_entries,
        education_entries=form_data.education_entries,
        course_entries=form_data.course_entries,
        activity_entries=form_data.activity_entries,
        skills=skills,
    )

    set_active_profile_for_user(db, user_id=user_id, profile_id=profile.id)
    return profile


def update_user_profile(db: Session, user_id: int, profile_id: int, form_data: ProfileFormData) -> Profile:
    profile = get_user_profile_or_raise(db, user_id=user_id, profile_id=profile_id)
    skills = _profile_skills_from_form(form_data)

    updated_profile = update_profile(
        db=db,
        profile=profile,
        full_name=form_data.full_name,
        target_position=form_data.target_position,
        email=form_data.email,
        phone=form_data.phone,
        city=form_data.city,
        work_format=form_data.work_format,
        salary_expectation=form_data.salary_expectation,
        github_url=form_data.github_url,
        linkedin_url=form_data.linkedin_url,
        summary_text=form_data.summary_text,
        experience_text=form_data.experience_text,
        projects_text=form_data.projects_text,
        education_text=form_data.education_text,
        certificates_text=form_data.certificates_text,
        languages_text=form_data.languages_text,
        soft_skills_text=form_data.soft_skills_text,
        experience_entries=form_data.experience_entries,
        project_entries=form_data.project_entries,
        education_entries=form_data.education_entries,
        course_entries=form_data.course_entries,
        activity_entries=form_data.activity_entries,
        skills=skills,
    )

    return updated_profile


def add_soft_skills_to_profile(
    db: Session,
    *,
    user_id: int,
    profile_id: int,
    soft_skills: list[tuple[str, str]],
) -> Profile:
    profile = get_user_profile_or_raise(db, user_id=user_id, profile_id=profile_id)
    existing_skill_norms = {normalize_skill(skill.skill_norm) for skill in profile.skills}
    existing_text_items = _split_comma_text(profile.soft_skills_text)
    existing_text_norms = {normalize_skill(item) for item in existing_text_items}

    for raw_value, raw_label in soft_skills:
        skill_norm = normalize_skill(raw_value)
        if not skill_norm:
            continue

        label = (raw_label or display_skill_name(skill_norm)).strip()
        if not label:
            label = skill_norm

        if skill_norm not in existing_text_norms:
            existing_text_items.append(label)
            existing_text_norms.add(skill_norm)

        if skill_norm not in existing_skill_norms:
            db.add(ProfileSkill(profile_id=profile.id, skill_norm=skill_norm[:100]))
            existing_skill_norms.add(skill_norm)

    profile.soft_skills_text = ", ".join(existing_text_items) if existing_text_items else None
    db.commit()
    db.refresh(profile)
    return profile


def activate_profile_for_user(db: Session, user_id: int, profile_id: int) -> None:
    profile = get_user_profile_or_raise(db, user_id=user_id, profile_id=profile_id)
    set_active_profile_for_user(db, user_id=user_id, profile_id=profile.id)


def _safe_unlink_project_file(relative_path: str | None) -> None:
    if not relative_path:
        return

    project_root = settings.project_root.resolve()
    file_path = (project_root / relative_path).resolve()
    try:
        file_path.relative_to(project_root)
    except ValueError:
        return

    if not file_path.is_file():
        return

    try:
        file_path.unlink(missing_ok=True)
    except PermissionError:
        try:
            file_path.chmod(file_path.stat().st_mode | stat.S_IWRITE)
            file_path.unlink(missing_ok=True)
        except OSError:
            return
    except OSError:
        return


def _delete_resume_export_files(resume_rows: list[tuple[int, str | None]]) -> None:
    for resume_id, pdf_path in resume_rows:
        _safe_unlink_project_file(pdf_path)
        _safe_unlink_project_file(str(settings.generated_path.relative_to(settings.project_root) / f"resume_{resume_id}.pdf"))
        _safe_unlink_project_file(str(settings.generated_path.relative_to(settings.project_root) / f"resume_{resume_id}.tex"))


def _delete_cover_letter_export_files(letter_rows: list[tuple[int, str | None]]) -> None:
    for letter_id, pdf_path in letter_rows:
        _safe_unlink_project_file(pdf_path)
        _safe_unlink_project_file(str(settings.generated_path.relative_to(settings.project_root) / f"cover_letter_{letter_id}.pdf"))
        _safe_unlink_project_file(str(settings.generated_path.relative_to(settings.project_root) / f"cover_letter_{letter_id}.tex"))


def delete_user_profile(db: Session, user_id: int, profile_id: int) -> None:
    profile = get_user_profile_or_raise(db, user_id=user_id, profile_id=profile_id)
    resume_rows, letter_rows = delete_profile_for_user(db, user_id=user_id, profile_id=profile.id)
    _delete_resume_export_files(resume_rows)
    _delete_cover_letter_export_files(letter_rows)


def _blank_experience_entry() -> dict[str, str]:
    return {
        "company": "",
        "position": "",
        "city": "",
        "start": "",
        "end": "",
        "tasks": "",
        "achievements": "",
    }


def _blank_project_entry() -> dict[str, str]:
    return {
        "name": "",
        "stack": "",
        "role": "",
        "description": "",
        "result": "",
    }


def _blank_education_entry() -> dict[str, str]:
    return {
        "org": "",
        "program": "",
        "start": "",
        "end": "",
        "details": "",
    }


def _blank_course_entry() -> dict[str, str]:
    return {
        "name": "",
        "provider": "",
        "year": "",
        "details": "",
    }


def _blank_activity_entry() -> dict[str, str]:
    return {
        "type": "",
        "name": "",
        "year": "",
        "result": "",
    }


def _split_comma_text(value: str | None) -> list[str]:
    if not value:
        return []
    return [chunk.strip() for chunk in value.replace("\n", ",").replace(";", ",").split(",") if chunk.strip()]


def _soft_skill_form_values(value: str | None) -> tuple[list[str], str]:
    selected: list[str] = []
    custom: list[str] = []

    for chunk in _split_comma_text(value):
        normalized = normalize_skill(chunk)
        if normalized in SOFT_SKILL_LABELS:
            if normalized not in selected:
                selected.append(normalized)
        else:
            custom.append(chunk)

    return selected, ", ".join(custom)


def _entries_or_blank(entries, blank_factory, legacy_key: str | None = None, legacy_text: str | None = None):
    if entries:
        return entries
    blank = blank_factory()
    if legacy_key and legacy_text:
        blank[legacy_key] = legacy_text
    return [blank]


def profile_to_form_initial(profile: Profile | None = None) -> dict:
    if profile is None:
        return {
            "full_name": "",
            "target_position": "",
            "email": "",
            "phone": "",
            "city": "",
            "work_format": "",
            "salary_expectation": "",
            "github_url": "",
            "linkedin_url": "",
            "summary_text": "",
            "experience_entries": [_blank_experience_entry()],
            "project_entries": [_blank_project_entry()],
            "education_entries": [_blank_education_entry()],
            "course_entries": [_blank_course_entry()],
            "activity_entries": [_blank_activity_entry()],
            "certificates_text": "",
            "languages_text": "",
            "soft_skills_text": "",
            "selected_soft_skills": [],
            "soft_skills_custom_text": "",
            "skills_text": "",
        }

    skills_text = ", ".join(
        skill.skill_norm
        for skill in profile.skills
        if normalize_skill(skill.skill_norm) not in SOFT_SKILL_LABELS
    )
    selected_soft_skills, soft_skills_custom_text = _soft_skill_form_values(profile.soft_skills_text)

    return {
        "full_name": profile.full_name,
        "target_position": profile.target_position,
        "email": profile.email,
        "phone": profile.phone,
        "city": profile.city or "",
        "work_format": getattr(profile, "work_format", None) or "",
        "salary_expectation": profile.salary_expectation or "",
        "github_url": profile.github_url or "",
        "linkedin_url": profile.linkedin_url or "",
        "summary_text": profile.summary_text or "",
        "experience_entries": _entries_or_blank(
            getattr(profile, "experience_entries", None),
            _blank_experience_entry,
            "tasks",
            profile.experience_text,
        ),
        "project_entries": _entries_or_blank(
            getattr(profile, "project_entries", None),
            _blank_project_entry,
            "description",
            profile.projects_text,
        ),
        "education_entries": _entries_or_blank(
            getattr(profile, "education_entries", None),
            _blank_education_entry,
            "details",
            profile.education_text,
        ),
        "course_entries": _entries_or_blank(getattr(profile, "course_entries", None), _blank_course_entry),
        "activity_entries": _entries_or_blank(getattr(profile, "activity_entries", None), _blank_activity_entry),
        "certificates_text": profile.certificates_text or "",
        "languages_text": profile.languages_text or "",
        "soft_skills_text": profile.soft_skills_text or "",
        "selected_soft_skills": selected_soft_skills,
        "soft_skills_custom_text": soft_skills_custom_text,
        "skills_text": skills_text,
    }
