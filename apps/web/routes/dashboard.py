from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.api.db.database import get_db
from apps.api.db.models import UserSettings
from apps.api.repositories.cover_letter_repository import get_cover_letter_for_pair
from apps.api.repositories.resume_repository import list_generated_resumes_for_pair
from apps.web.dependencies import get_current_user_optional
from apps.web.services.match_service import build_low_match_warning, build_match_result
from apps.web.services.profile_service import get_user_profile
from apps.web.services.vacancy_service import (
    VacancyError,
    get_user_vacancy_or_raise,
    list_user_vacancies,
    load_vacancy_raw_text,
)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


def _load_last_analysis(db: Session, user_id: int, profile):
    if profile is None:
        return None

    settings = db.scalar(select(UserSettings).where(UserSettings.user_id == user_id))
    if settings is None or settings.active_vacancy_id is None:
        return None

    try:
        vacancy = get_user_vacancy_or_raise(db, user_id=user_id, vacancy_id=settings.active_vacancy_id)
    except VacancyError:
        return None

    vacancy.raw_text = load_vacancy_raw_text(vacancy)
    if not vacancy.requirements:
        return {"vacancy": vacancy, "match_result": None}

    resumes = list_generated_resumes_for_pair(
        db,
        user_id=user_id,
        profile_id=profile.id,
        vacancy_id=vacancy.id,
    )
    cover_letter = get_cover_letter_for_pair(
        db,
        user_id=user_id,
        profile_id=profile.id,
        vacancy_id=vacancy.id,
    )
    match_result = build_match_result(profile, vacancy)
    return {
        "vacancy": vacancy,
        "match_result": match_result,
        "low_match_warning": build_low_match_warning(match_result),
        "resume": resumes[0] if resumes else None,
        "cover_letter": cover_letter,
    }


@router.get("", response_class=HTMLResponse, name="dashboard")
async def dashboard_page(
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user_optional),
):
    if current_user is None:
        return RedirectResponse(
            url=f"/login?message={quote('Сначала выполните вход')}&message_type=warning",
            status_code=status.HTTP_302_FOUND,
        )

    profile = get_user_profile(db, current_user.id)
    vacancies = list_user_vacancies(db, current_user.id)
    for vacancy in vacancies:
        vacancy.raw_text = load_vacancy_raw_text(vacancy)
    last_analysis = _load_last_analysis(db, current_user.id, profile)

    return request.app.state.templates.TemplateResponse(
        request=request,
        name="dashboard/index.html",
        context={
            "request": request,
            "title": "Главная",
            "current_user": current_user,
            "profile": profile,
            "vacancies": vacancies,
            "last_analysis": last_analysis,
            "flash_message": request.query_params.get("message"),
            "flash_type": request.query_params.get("message_type", "info"),
        },
    )
