from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from apps.api.db.database import get_db
from apps.api.schemas.profile import ProfileFormData
from apps.web.dependencies import get_current_user
from apps.web.services.profile_service import (
    ProfileError,
    create_user_profile,
    delete_user_profile,
    get_profile_key_skills,
    get_user_profile,
    get_user_profile_or_raise,
    merge_profile_context_skills,
    profile_to_form_initial,
    update_user_profile,
)
from core.utils import SOFT_SKILL_LABELS, SOFT_SKILL_OPTIONS

router = APIRouter(prefix="/profiles", tags=["profiles"])


def _build_context(
    request: Request,
    *,
    title: str,
    current_user,
    profile=None,
    form_initial=None,
    form_error: str | None = None,
    skill_extract_message: str | None = None,
    skill_extract_type: str = "info",
    is_edit: bool = False,
):
    return {
        "request": request,
        "title": title,
        "current_user": current_user,
        "profile": profile,
        "profile_key_skills": get_profile_key_skills(profile),
        "form_initial": form_initial,
        "form_error": form_error,
        "skill_extract_message": skill_extract_message,
        "skill_extract_type": skill_extract_type,
        "is_edit": is_edit,
        "soft_skill_options": SOFT_SKILL_OPTIONS,
        "flash_message": request.query_params.get("message"),
        "flash_type": request.query_params.get("message_type", "info"),
    }


def _extract_profile_form_error(exc: Exception) -> str:
    if isinstance(exc, ProfileError):
        return str(exc)

    if isinstance(exc, ValidationError):
        first_error = exc.errors()[0] if exc.errors() else None
        if not first_error:
            return "Проверьте корректность заполнения формы."

        field_name = first_error.get("loc", [""])[-1]
        if field_name == "email":
            return "Введите корректный email."
        if field_name in {"github_url", "linkedin_url"}:
            return "Проверьте корректность URL. Ссылка должна начинаться с http:// или https://."
        return "Проверьте обязательные поля профиля."

    if isinstance(exc, ValueError):
        return str(exc)

    return "Проверьте корректность заполнения формы."


def _clean(value: str | None) -> str:
    return (value or "").strip()


def _form_value(form, key: str) -> str:
    return _clean(str(form.get(key, "")))


def _form_list(form, key: str) -> list[str]:
    return [_clean(str(value)) for value in form.getlist(key)]


def _non_empty_entries(entries: list[dict[str, str]]) -> list[dict[str, str]]:
    return [entry for entry in entries if any(_clean(value) for value in entry.values())]


def _entries_from_form(form, mapping: dict[str, str]) -> list[dict[str, str]]:
    values_by_field = {target: _form_list(form, source) for target, source in mapping.items()}
    max_len = max((len(values) for values in values_by_field.values()), default=0)
    entries = []
    for index in range(max_len):
        entry = {
            target: values[index] if index < len(values) else ""
            for target, values in values_by_field.items()
        }
        entries.append(entry)
    return _non_empty_entries(entries)


def _join_blocks(blocks: list[str]) -> str:
    return "\n\n".join(block for block in blocks if block.strip())


def _split_soft_custom(value: str) -> list[str]:
    return [chunk.strip() for chunk in value.replace("\n", ",").replace(";", ",").split(",") if chunk.strip()]


def _combine_soft_skills(selected_soft_skills: list[str], custom_soft_skills_text: str) -> str:
    parts = [SOFT_SKILL_LABELS[value] for value in selected_soft_skills if value in SOFT_SKILL_LABELS]
    parts.extend(_split_soft_custom(custom_soft_skills_text))
    unique_parts: list[str] = []
    seen = set()
    for part in parts:
        key = part.lower()
        if key not in seen:
            unique_parts.append(part)
            seen.add(key)
    return ", ".join(unique_parts)


def _labeled_block(title: str, fields: list[tuple[str, str]]) -> str:
    lines = [title]
    for label, value in fields:
        if _clean(value):
            lines.append(f"{label}: {_clean(value)}")
    return "\n".join(lines)


def _period(start: str, end: str, *, current_label: str = "по настоящее время") -> str:
    start = _clean(start)
    end = _clean(end)
    if start and not end:
        return f"{start} - {current_label}"
    if start or end:
        return " - ".join(part for part in [start, end] if part)
    return ""


def _build_experience_text(entries: list[dict[str, str]]) -> str:
    blocks = []
    for index, entry in enumerate(entries, start=1):
        blocks.append(
            _labeled_block(
                f"Место работы {index}",
                [
                    ("Компания", entry.get("company", "")),
                    ("Должность", entry.get("position", "")),
                    ("Город", entry.get("city", "")),
                    ("Период", _period(entry.get("start", ""), entry.get("end", ""))),
                    ("Задачи", entry.get("tasks", "")),
                    ("Достижения и результаты", entry.get("achievements", "")),
                ],
            )
        )
    return _join_blocks(blocks)


def _build_projects_text(entries: list[dict[str, str]]) -> str:
    blocks = []
    for index, entry in enumerate(entries, start=1):
        blocks.append(
            _labeled_block(
                f"Проект {index}",
                [
                    ("Название", entry.get("name", "")),
                    ("Стек", entry.get("stack", "")),
                    ("Роль", entry.get("role", "")),
                    ("Описание", entry.get("description", "")),
                    ("Результат", entry.get("result", "")),
                ],
            )
        )
    return _join_blocks(blocks)


def _build_education_text(entries: list[dict[str, str]], activities: list[dict[str, str]]) -> str:
    blocks = []
    for index, entry in enumerate(entries, start=1):
        blocks.append(
            _labeled_block(
                f"Образование {index}",
                [
                    ("Учебное заведение", entry.get("org", "")),
                    ("Направление", entry.get("program", "")),
                    ("Период", _period(entry.get("start", ""), entry.get("end", ""), current_label="сейчас")),
                    ("Детали", entry.get("details", "")),
                ],
            )
        )

    for index, entry in enumerate(activities, start=1):
        blocks.append(
            _labeled_block(
                f"Активность {index}",
                [
                    ("Тип", entry.get("type", "")),
                    ("Название", entry.get("name", "")),
                    ("Год", entry.get("year", "")),
                    ("Результат", entry.get("result", "")),
                ],
            )
        )
    return _join_blocks(blocks)


def _build_courses_text(entries: list[dict[str, str]]) -> str:
    blocks = []
    for index, entry in enumerate(entries, start=1):
        blocks.append(
            _labeled_block(
                f"Курс или сертификат {index}",
                [
                    ("Название", entry.get("name", "")),
                    ("Организация", entry.get("provider", "")),
                    ("Год", entry.get("year", "")),
                    ("Детали", entry.get("details", "")),
                ],
            )
        )
    return _join_blocks(blocks)


def _form_initial_from_form(form) -> dict:
    experience_entries = _entries_from_form(
        form,
        {
            "company": "experience_company",
            "position": "experience_position",
            "city": "experience_city",
            "start": "experience_start",
            "end": "experience_end",
            "tasks": "experience_tasks",
            "achievements": "experience_achievements",
        },
    )
    project_entries = _entries_from_form(
        form,
        {
            "name": "project_name",
            "stack": "project_stack",
            "role": "project_role",
            "description": "project_description",
            "result": "project_result",
        },
    )
    education_entries = _entries_from_form(
        form,
        {
            "org": "education_org",
            "program": "education_program",
            "start": "education_start",
            "end": "education_end",
            "details": "education_details",
        },
    )
    course_entries = _entries_from_form(
        form,
        {
            "name": "course_name",
            "provider": "course_provider",
            "year": "course_year",
            "details": "course_details",
        },
    )
    activity_entries = _entries_from_form(
        form,
        {
            "type": "activity_type",
            "name": "activity_name",
            "year": "activity_year",
            "result": "activity_result",
        },
    )

    selected_soft_skills = [value for value in _form_list(form, "soft_skill_option") if value in SOFT_SKILL_LABELS]
    custom_soft_skills_text = _form_value(form, "soft_skills_custom_text")

    return {
        "full_name": _form_value(form, "full_name"),
        "target_position": _form_value(form, "target_position"),
        "email": _form_value(form, "email"),
        "phone": _form_value(form, "phone"),
        "city": _form_value(form, "city"),
        "work_format": _form_value(form, "work_format"),
        "salary_expectation": _form_value(form, "salary_expectation"),
        "github_url": _form_value(form, "github_url"),
        "linkedin_url": _form_value(form, "linkedin_url"),
        "summary_text": _form_value(form, "summary_text"),
        "selected_soft_skills": selected_soft_skills,
        "soft_skills_custom_text": custom_soft_skills_text,
        "soft_skills_text": _combine_soft_skills(selected_soft_skills, custom_soft_skills_text),
        "skills_text": _form_value(form, "skills_text"),
        "languages_text": _form_value(form, "languages_text"),
        "experience_entries": experience_entries or [{"company": "", "position": "", "city": "", "start": "", "end": "", "tasks": "", "achievements": ""}],
        "project_entries": project_entries or [{"name": "", "stack": "", "role": "", "description": "", "result": ""}],
        "education_entries": education_entries or [{"org": "", "program": "", "start": "", "end": "", "details": ""}],
        "course_entries": course_entries or [{"name": "", "provider": "", "year": "", "details": ""}],
        "activity_entries": activity_entries or [{"type": "", "name": "", "year": "", "result": ""}],
    }


def _build_form_data_from_initial(form_initial: dict) -> ProfileFormData:
    experience_entries = _non_empty_entries(form_initial["experience_entries"])
    project_entries = _non_empty_entries(form_initial["project_entries"])
    education_entries = _non_empty_entries(form_initial["education_entries"])
    course_entries = _non_empty_entries(form_initial["course_entries"])
    activity_entries = _non_empty_entries(form_initial["activity_entries"])
    if not any([experience_entries, project_entries, education_entries, course_entries, activity_entries]):
        raise ProfileError(
            "Добавьте хотя бы один содержательный блок: опыт работы, проект, образование, курс или активность."
        )

    return ProfileFormData(
        full_name=form_initial["full_name"],
        target_position=form_initial["target_position"],
        email=form_initial["email"],
        phone=form_initial["phone"],
        city=form_initial["city"],
        work_format=form_initial["work_format"],
        salary_expectation=form_initial["salary_expectation"],
        github_url=form_initial["github_url"],
        linkedin_url=form_initial["linkedin_url"],
        summary_text=form_initial["summary_text"],
        experience_text=_build_experience_text(experience_entries),
        projects_text=_build_projects_text(project_entries),
        education_text=_build_education_text(education_entries, activity_entries),
        certificates_text=_build_courses_text(course_entries),
        languages_text=form_initial["languages_text"],
        soft_skills_text=form_initial["soft_skills_text"],
        skills_text=form_initial["skills_text"],
        experience_entries=experience_entries,
        project_entries=project_entries,
        education_entries=education_entries,
        course_entries=course_entries,
        activity_entries=activity_entries,
    )


def _profile_context_text_from_initial(form_initial: dict) -> str:
    experience_entries = _non_empty_entries(form_initial.get("experience_entries", []))
    project_entries = _non_empty_entries(form_initial.get("project_entries", []))
    education_entries = _non_empty_entries(form_initial.get("education_entries", []))
    course_entries = _non_empty_entries(form_initial.get("course_entries", []))
    activity_entries = _non_empty_entries(form_initial.get("activity_entries", []))

    return _join_blocks(
        [
            form_initial.get("target_position", ""),
            form_initial.get("summary_text", ""),
            _build_experience_text(experience_entries),
            _build_projects_text(project_entries),
            _build_education_text(education_entries, activity_entries),
            _build_courses_text(course_entries),
            form_initial.get("languages_text", ""),
        ]
    )


def _apply_profile_skill_extraction(form_initial: dict) -> tuple[str, str]:
    merged_skills_text, added_skills = merge_profile_context_skills(
        form_initial.get("skills_text"),
        _profile_context_text_from_initial(form_initial),
    )
    form_initial["skills_text"] = merged_skills_text

    if not added_skills:
        return "Новых профессиональных навыков в тексте анкеты не найдено. Текущий список оставлен без изменений.", "info"

    preview = ", ".join(added_skills[:12])
    if len(added_skills) > 12:
        preview += f" и ещё {len(added_skills) - 12}"
    return f"Добавлены найденные навыки: {preview}. Проверьте список и сохраните профиль, если всё верно.", "success"


@router.get("", response_class=HTMLResponse, name="profiles_list")
async def profiles_list(
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    profile = get_user_profile(db, current_user.id)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="profiles/list.html",
        context=_build_context(
            request,
            title="Профиль",
            current_user=current_user,
            profile=profile,
        ),
    )


@router.get("/create", response_class=HTMLResponse, name="profiles_create_page")
async def profiles_create_page(
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    profile = get_user_profile(db, current_user.id)
    if profile is not None:
        return RedirectResponse(url=f"/profiles/{profile.id}", status_code=status.HTTP_302_FOUND)

    return request.app.state.templates.TemplateResponse(
        request=request,
        name="profiles/dynamic_form.html",
        context=_build_context(
            request,
            title="Создание профиля",
            current_user=current_user,
            form_initial=profile_to_form_initial(),
            is_edit=False,
        ),
    )


@router.post("/create", response_class=HTMLResponse, name="profiles_create_submit")
async def profiles_create_submit(
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    form = await request.form()
    raw_form_initial = _form_initial_from_form(form)

    if _form_value(form, "profile_action") == "extract_skills":
        skill_extract_message, skill_extract_type = _apply_profile_skill_extraction(raw_form_initial)
        return request.app.state.templates.TemplateResponse(
            request=request,
            name="profiles/dynamic_form.html",
            context=_build_context(
                request,
                title="Создание профиля",
                current_user=current_user,
                form_initial=raw_form_initial,
                skill_extract_message=skill_extract_message,
                skill_extract_type=skill_extract_type,
                is_edit=False,
            ),
        )

    try:
        form_data = _build_form_data_from_initial(raw_form_initial)
        profile = create_user_profile(db, user_id=current_user.id, form_data=form_data)
    except Exception as exc:
        return request.app.state.templates.TemplateResponse(
            request=request,
            name="profiles/dynamic_form.html",
            context=_build_context(
                request,
                title="Создание профиля",
                current_user=current_user,
                form_initial=raw_form_initial,
                form_error=_extract_profile_form_error(exc),
                is_edit=False,
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    message = quote("Профиль сохранён")
    return RedirectResponse(
        url=f"/profiles/{profile.id}?message={message}&message_type=success",
        status_code=status.HTTP_302_FOUND,
    )


@router.get("/{profile_id}", response_class=HTMLResponse, name="profiles_edit_page")
async def profiles_edit_page(
    profile_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    profile = get_user_profile_or_raise(db, user_id=current_user.id, profile_id=profile_id)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="profiles/dynamic_form.html",
        context=_build_context(
            request,
            title="Редактирование профиля",
            current_user=current_user,
            profile=profile,
            form_initial=profile_to_form_initial(profile),
            is_edit=True,
        ),
    )


@router.post("/{profile_id}", response_class=HTMLResponse, name="profiles_edit_submit")
async def profiles_edit_submit(
    profile_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    form = await request.form()
    raw_form_initial = _form_initial_from_form(form)

    try:
        profile = get_user_profile_or_raise(db, user_id=current_user.id, profile_id=profile_id)
        if _form_value(form, "profile_action") == "extract_skills":
            skill_extract_message, skill_extract_type = _apply_profile_skill_extraction(raw_form_initial)
            return request.app.state.templates.TemplateResponse(
                request=request,
                name="profiles/dynamic_form.html",
                context=_build_context(
                    request,
                    title="Редактирование профиля",
                    current_user=current_user,
                    profile=profile,
                    form_initial=raw_form_initial,
                    skill_extract_message=skill_extract_message,
                    skill_extract_type=skill_extract_type,
                    is_edit=True,
                ),
            )

        form_data = _build_form_data_from_initial(raw_form_initial)
        update_user_profile(db, user_id=current_user.id, profile_id=profile_id, form_data=form_data)
    except Exception as exc:
        return request.app.state.templates.TemplateResponse(
            request=request,
            name="profiles/dynamic_form.html",
            context=_build_context(
                request,
                title="Редактирование профиля",
                current_user=current_user,
                profile=profile if "profile" in locals() else None,
                form_initial=raw_form_initial,
                form_error=_extract_profile_form_error(exc),
                is_edit=True,
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    message = quote("Профиль обновлён")
    return RedirectResponse(
        url=f"/profiles/{profile_id}?message={message}&message_type=success",
        status_code=status.HTTP_302_FOUND,
    )


@router.post("/{profile_id}/delete", name="profiles_delete")
async def profiles_delete(
    profile_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    try:
        delete_user_profile(db, user_id=current_user.id, profile_id=profile_id)
        message = quote("Профиль удалён. Можно заполнить анкету заново.")
        message_type = "success"
    except ProfileError as exc:
        message = quote(str(exc))
        message_type = "danger"

    return RedirectResponse(
        url=f"/profiles?message={message}&message_type={message_type}",
        status_code=status.HTTP_302_FOUND,
    )
