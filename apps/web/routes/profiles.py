from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from apps.api.db.database import get_db
from apps.api.schemas.profile import ProfileFormData
from apps.web.dependencies import get_current_user
from apps.web.services.profile_service import (
    ProfileError,
    create_user_profile,
    get_profile_key_skills,
    get_user_profile,
    get_user_profile_or_raise,
    profile_to_form_initial,
    update_user_profile,
)

router = APIRouter(prefix="/profiles", tags=["profiles"])


def _build_context(
    request: Request,
    *,
    title: str,
    current_user,
    profile=None,
    form_initial=None,
    form_error: str | None = None,
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
        "is_edit": is_edit,
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


def _join_blocks(*blocks: str) -> str:
    return "\n\n".join(block for block in (_clean(block) for block in blocks) if block)


def _build_experience_text(
    *,
    experience_company: str,
    experience_position: str,
    experience_start: str,
    experience_end: str,
    experience_tasks: str,
    experience_achievements: str,
    extra_experience_text: str,
) -> str:
    header_parts = []
    if _clean(experience_company):
        header_parts.append(_clean(experience_company))
    period = " - ".join(part for part in [_clean(experience_start), _clean(experience_end) or "по настоящее время"] if part)
    if period:
        header_parts.append(period)
    if _clean(experience_position):
        header_parts.append(_clean(experience_position))

    structured = []
    if header_parts:
        structured.append(", ".join(header_parts))
    if _clean(experience_tasks):
        structured.append("Задачи:\n" + _clean(experience_tasks))
    if _clean(experience_achievements):
        structured.append("Достижения:\n" + _clean(experience_achievements))

    return _join_blocks("\n".join(structured), extra_experience_text)


def _build_projects_text(
    *,
    project_name: str,
    project_stack: str,
    project_role: str,
    project_description: str,
    project_result: str,
    extra_projects_text: str,
) -> str:
    structured = []
    if _clean(project_name):
        structured.append(_clean(project_name))
    if _clean(project_stack):
        structured.append("Стек: " + _clean(project_stack))
    if _clean(project_role):
        structured.append("Роль: " + _clean(project_role))
    if _clean(project_description):
        structured.append("Описание: " + _clean(project_description))
    if _clean(project_result):
        structured.append("Результат: " + _clean(project_result))

    return _join_blocks("\n".join(structured), extra_projects_text)


def _build_education_text(
    *,
    education_org: str,
    education_program: str,
    education_start: str,
    education_end: str,
    education_details: str,
    extra_education_text: str,
) -> str:
    structured = []
    header_parts = []
    if _clean(education_org):
        header_parts.append(_clean(education_org))
    if _clean(education_program):
        header_parts.append(_clean(education_program))
    period = " - ".join(part for part in [_clean(education_start), _clean(education_end)] if part)
    if period:
        header_parts.append(period)
    if header_parts:
        structured.append(", ".join(header_parts))
    if _clean(education_details):
        structured.append(_clean(education_details))

    return _join_blocks("\n".join(structured), extra_education_text)


def _build_form_data_from_request(
    *,
    full_name: str,
    target_position: str,
    email: str,
    phone: str,
    city: str,
    salary_expectation: str,
    github_url: str,
    linkedin_url: str,
    summary_text: str,
    experience_company: str,
    experience_position: str,
    experience_start: str,
    experience_end: str,
    experience_tasks: str,
    experience_achievements: str,
    extra_experience_text: str,
    project_name: str,
    project_stack: str,
    project_role: str,
    project_description: str,
    project_result: str,
    extra_projects_text: str,
    education_org: str,
    education_program: str,
    education_start: str,
    education_end: str,
    education_details: str,
    extra_education_text: str,
    certificates_text: str,
    languages_text: str,
    soft_skills_text: str,
    skills_text: str,
) -> ProfileFormData:
    experience_text = _build_experience_text(
        experience_company=experience_company,
        experience_position=experience_position,
        experience_start=experience_start,
        experience_end=experience_end,
        experience_tasks=experience_tasks,
        experience_achievements=experience_achievements,
        extra_experience_text=extra_experience_text,
    )
    projects_text = _build_projects_text(
        project_name=project_name,
        project_stack=project_stack,
        project_role=project_role,
        project_description=project_description,
        project_result=project_result,
        extra_projects_text=extra_projects_text,
    )
    education_text = _build_education_text(
        education_org=education_org,
        education_program=education_program,
        education_start=education_start,
        education_end=education_end,
        education_details=education_details,
        extra_education_text=extra_education_text,
    )

    return ProfileFormData(
        full_name=full_name,
        target_position=target_position,
        email=email,
        phone=phone,
        city=city,
        salary_expectation=salary_expectation,
        github_url=github_url,
        linkedin_url=linkedin_url,
        summary_text=summary_text,
        experience_text=experience_text,
        projects_text=projects_text,
        education_text=education_text,
        certificates_text=certificates_text,
        languages_text=languages_text,
        soft_skills_text=soft_skills_text,
        skills_text=skills_text,
    )


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
        name="profiles/form.html",
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
    full_name: str = Form(...),
    target_position: str = Form(...),
    email: str = Form(...),
    phone: str = Form(...),
    city: str = Form(""),
    salary_expectation: str = Form(""),
    github_url: str = Form(""),
    linkedin_url: str = Form(""),
    summary_text: str = Form(""),
    experience_company: str = Form(""),
    experience_position: str = Form(""),
    experience_start: str = Form(""),
    experience_end: str = Form(""),
    experience_tasks: str = Form(""),
    experience_achievements: str = Form(""),
    extra_experience_text: str = Form(""),
    project_name: str = Form(""),
    project_stack: str = Form(""),
    project_role: str = Form(""),
    project_description: str = Form(""),
    project_result: str = Form(""),
    extra_projects_text: str = Form(""),
    education_org: str = Form(""),
    education_program: str = Form(""),
    education_start: str = Form(""),
    education_end: str = Form(""),
    education_details: str = Form(""),
    extra_education_text: str = Form(""),
    certificates_text: str = Form(""),
    languages_text: str = Form(""),
    soft_skills_text: str = Form(""),
    skills_text: str = Form(""),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    raw_form_initial = {
        "full_name": full_name,
        "target_position": target_position,
        "email": email,
        "phone": phone,
        "city": city,
        "salary_expectation": salary_expectation,
        "github_url": github_url,
        "linkedin_url": linkedin_url,
        "summary_text": summary_text,
        "experience_company": experience_company,
        "experience_position": experience_position,
        "experience_start": experience_start,
        "experience_end": experience_end,
        "experience_tasks": experience_tasks,
        "experience_achievements": experience_achievements,
        "extra_experience_text": extra_experience_text,
        "project_name": project_name,
        "project_stack": project_stack,
        "project_role": project_role,
        "project_description": project_description,
        "project_result": project_result,
        "extra_projects_text": extra_projects_text,
        "education_org": education_org,
        "education_program": education_program,
        "education_start": education_start,
        "education_end": education_end,
        "education_details": education_details,
        "extra_education_text": extra_education_text,
        "certificates_text": certificates_text,
        "languages_text": languages_text,
        "soft_skills_text": soft_skills_text,
        "skills_text": skills_text,
    }

    try:
        form_data = _build_form_data_from_request(**raw_form_initial)
        profile = create_user_profile(db, user_id=current_user.id, form_data=form_data)
    except Exception as exc:
        return request.app.state.templates.TemplateResponse(
            request=request,
            name="profiles/form.html",
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
        name="profiles/form.html",
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
    full_name: str = Form(...),
    target_position: str = Form(...),
    email: str = Form(...),
    phone: str = Form(...),
    city: str = Form(""),
    salary_expectation: str = Form(""),
    github_url: str = Form(""),
    linkedin_url: str = Form(""),
    summary_text: str = Form(""),
    experience_company: str = Form(""),
    experience_position: str = Form(""),
    experience_start: str = Form(""),
    experience_end: str = Form(""),
    experience_tasks: str = Form(""),
    experience_achievements: str = Form(""),
    extra_experience_text: str = Form(""),
    project_name: str = Form(""),
    project_stack: str = Form(""),
    project_role: str = Form(""),
    project_description: str = Form(""),
    project_result: str = Form(""),
    extra_projects_text: str = Form(""),
    education_org: str = Form(""),
    education_program: str = Form(""),
    education_start: str = Form(""),
    education_end: str = Form(""),
    education_details: str = Form(""),
    extra_education_text: str = Form(""),
    certificates_text: str = Form(""),
    languages_text: str = Form(""),
    soft_skills_text: str = Form(""),
    skills_text: str = Form(""),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    raw_form_initial = {
        "full_name": full_name,
        "target_position": target_position,
        "email": email,
        "phone": phone,
        "city": city,
        "salary_expectation": salary_expectation,
        "github_url": github_url,
        "linkedin_url": linkedin_url,
        "summary_text": summary_text,
        "experience_company": experience_company,
        "experience_position": experience_position,
        "experience_start": experience_start,
        "experience_end": experience_end,
        "experience_tasks": experience_tasks,
        "experience_achievements": experience_achievements,
        "extra_experience_text": extra_experience_text,
        "project_name": project_name,
        "project_stack": project_stack,
        "project_role": project_role,
        "project_description": project_description,
        "project_result": project_result,
        "extra_projects_text": extra_projects_text,
        "education_org": education_org,
        "education_program": education_program,
        "education_start": education_start,
        "education_end": education_end,
        "education_details": education_details,
        "extra_education_text": extra_education_text,
        "certificates_text": certificates_text,
        "languages_text": languages_text,
        "soft_skills_text": soft_skills_text,
        "skills_text": skills_text,
    }

    try:
        profile = get_user_profile_or_raise(db, user_id=current_user.id, profile_id=profile_id)
        form_data = _build_form_data_from_request(**raw_form_initial)
        update_user_profile(db, user_id=current_user.id, profile_id=profile_id, form_data=form_data)
    except Exception as exc:
        return request.app.state.templates.TemplateResponse(
            request=request,
            name="profiles/form.html",
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
