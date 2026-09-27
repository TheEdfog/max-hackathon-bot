from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from apps.api.db.database import get_db
from apps.api.schemas.vacancy import VacancyFormData
from apps.web.dependencies import get_current_user
from apps.web.services.vacancy_service import (
    VacancyError,
    activate_vacancy_for_user,
    create_user_vacancy,
    delete_user_vacancy,
    extract_and_save_requirements,
    get_user_vacancy_or_raise,
    is_auto_extract_placeholder,
    list_user_vacancies,
    load_vacancy_raw_text,
    refresh_vacancy_requirement_policy,
    update_user_vacancy,
    vacancy_to_form_initial,
)

router = APIRouter(prefix="/vacancies", tags=["vacancies"])


def _build_context(
    request: Request,
    *,
    title: str,
    current_user,
    vacancies=None,
    vacancy=None,
    form_initial=None,
    form_error: str | None = None,
    is_edit: bool = False,
):
    return {
        "request": request,
        "title": title,
        "current_user": current_user,
        "vacancies": vacancies,
        "vacancy": vacancy,
        "form_initial": form_initial,
        "form_error": form_error,
        "is_edit": is_edit,
        "flash_message": request.query_params.get("message"),
        "flash_type": request.query_params.get("message_type", "info"),
    }


def _extract_vacancy_form_error(exc: Exception) -> str:
    if isinstance(exc, VacancyError):
        return str(exc)

    if isinstance(exc, ValidationError):
        first_error = exc.errors()[0] if exc.errors() else None
        if not first_error:
            return "Проверьте корректность заполнения формы."

        loc = first_error.get("loc") or [""]
        field_name = loc[-1] if loc else ""
        if first_error.get("type") == "value_error" and first_error.get("msg"):
            return str(first_error["msg"]).replace("Value error, ", "")
        if field_name == "source_url":
            return "Введите корректный URL вакансии."
        if field_name == "raw_text":
            return "Вставьте текст вакансии или укажите ссылку."
        return "Укажите ссылку на вакансию или вставьте текст вакансии."

    if isinstance(exc, ValueError):
        return str(exc)

    return "Проверьте корректность заполнения формы."


def _build_form_data_from_request(*, title: str, source_url: str, raw_text: str) -> VacancyFormData:
    return VacancyFormData(title=title, source_url=source_url, raw_text=raw_text)


@router.get("", response_class=HTMLResponse, name="vacancies_list")
async def vacancies_list(
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    vacancies = list_user_vacancies(db, current_user.id)
    for index, vacancy in enumerate(vacancies):
        if vacancy.requirements:
            vacancy = refresh_vacancy_requirement_policy(db, user_id=current_user.id, vacancy_id=vacancy.id)
            vacancies[index] = vacancy
        vacancy.raw_text = load_vacancy_raw_text(vacancy)

    return request.app.state.templates.TemplateResponse(
        request=request,
        name="vacancies/list.html",
        context=_build_context(
            request,
            title="Вакансии",
            current_user=current_user,
            vacancies=vacancies,
        ),
    )


@router.get("/create", response_class=HTMLResponse, name="vacancies_create_page")
async def vacancies_create_page(
    request: Request,
    current_user=Depends(get_current_user),
):
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="vacancies/form.html",
        context=_build_context(
            request,
            title="Добавление вакансии",
            current_user=current_user,
            form_initial=vacancy_to_form_initial(),
            is_edit=False,
        ),
    )


@router.post("/create", response_class=HTMLResponse, name="vacancies_create_submit")
async def vacancies_create_submit(
    request: Request,
    title: str = Form(""),
    source_url: str = Form(""),
    raw_text: str = Form(""),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    raw_form_initial = {
        "title": title,
        "source_url": source_url,
        "raw_text": raw_text,
    }

    try:
        form_data = _build_form_data_from_request(**raw_form_initial)
        vacancy = create_user_vacancy(
            db,
            user_id=current_user.id,
            form_data=form_data,
            contact_email=current_user.email,
        )
        vacancy = extract_and_save_requirements(db, user_id=current_user.id, vacancy_id=vacancy.id)
    except Exception as exc:
        return request.app.state.templates.TemplateResponse(
            request=request,
            name="vacancies/form.html",
            context=_build_context(
                request,
                title="Добавление вакансии",
                current_user=current_user,
                form_initial=raw_form_initial,
                form_error=_extract_vacancy_form_error(exc),
                is_edit=False,
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if is_auto_extract_placeholder(vacancy.raw_text):
        message = quote("Ссылка сохранена. Текст вакансии не удалось получить автоматически, вставьте его вручную в редактировании.")
        message_type = "warning"
    elif vacancy.requirements:
        message = quote("Вакансия сохранена, требования извлечены")
        message_type = "success"
    else:
        message = quote("Вакансия сохранена. Если требований мало или нет, проверьте текст вакансии.")
        message_type = "warning"
    return RedirectResponse(
        url=f"/vacancies?message={message}&message_type={message_type}",
        status_code=status.HTTP_302_FOUND,
    )


@router.post("/{vacancy_id}/delete", name="vacancies_delete")
async def vacancies_delete(
    vacancy_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    try:
        delete_user_vacancy(db, user_id=current_user.id, vacancy_id=vacancy_id)
        message = quote("Вакансия удалена вместе с анализом и сгенерированными документами.")
        message_type = "success"
    except VacancyError as exc:
        message = quote(str(exc))
        message_type = "danger"

    return RedirectResponse(
        url=f"/vacancies?message={message}&message_type={message_type}",
        status_code=status.HTTP_302_FOUND,
    )


@router.get("/{vacancy_id}", response_class=HTMLResponse, name="vacancies_edit_page")
async def vacancies_edit_page(
    vacancy_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    vacancy = get_user_vacancy_or_raise(db, user_id=current_user.id, vacancy_id=vacancy_id)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="vacancies/form.html",
        context=_build_context(
            request,
            title="Редактирование вакансии",
            current_user=current_user,
            vacancy=vacancy,
            form_initial=vacancy_to_form_initial(vacancy),
            is_edit=True,
        ),
    )


@router.post("/{vacancy_id}", response_class=HTMLResponse, name="vacancies_edit_submit")
async def vacancies_edit_submit(
    vacancy_id: int,
    request: Request,
    title: str = Form(""),
    source_url: str = Form(""),
    raw_text: str = Form(""),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    raw_form_initial = {
        "title": title,
        "source_url": source_url,
        "raw_text": raw_text,
    }

    try:
        vacancy = get_user_vacancy_or_raise(db, user_id=current_user.id, vacancy_id=vacancy_id)
        form_data = _build_form_data_from_request(**raw_form_initial)
        vacancy = update_user_vacancy(
            db,
            user_id=current_user.id,
            vacancy_id=vacancy_id,
            form_data=form_data,
            contact_email=current_user.email,
        )
        vacancy = extract_and_save_requirements(db, user_id=current_user.id, vacancy_id=vacancy_id)
    except Exception as exc:
        return request.app.state.templates.TemplateResponse(
            request=request,
            name="vacancies/form.html",
            context=_build_context(
                request,
                title="Редактирование вакансии",
                current_user=current_user,
                vacancy=vacancy if "vacancy" in locals() else None,
                form_initial=raw_form_initial,
                form_error=_extract_vacancy_form_error(exc),
                is_edit=True,
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if is_auto_extract_placeholder(vacancy.raw_text):
        message = quote("Ссылка сохранена. Текст вакансии не удалось получить автоматически, вставьте его вручную.")
        message_type = "warning"
    elif vacancy.requirements:
        message = quote("Вакансия обновлена, требования пересчитаны")
        message_type = "success"
    else:
        message = quote("Вакансия обновлена. Если требований мало или нет, проверьте текст вакансии.")
        message_type = "warning"
    return RedirectResponse(
        url=f"/vacancies/{vacancy_id}?message={message}&message_type={message_type}",
        status_code=status.HTTP_302_FOUND,
    )


@router.post("/{vacancy_id}/extract", name="vacancies_extract_requirements")
async def vacancies_extract_requirements(
    vacancy_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    try:
        vacancy = get_user_vacancy_or_raise(db, user_id=current_user.id, vacancy_id=vacancy_id)
        if vacancy.requirements:
            message = quote("Требования уже подготовлены. Можно запускать match-анализ.")
            message_type = "info"
        else:
            vacancy = extract_and_save_requirements(db, user_id=current_user.id, vacancy_id=vacancy_id)
            if vacancy.requirements:
                message = quote("Требования вакансии подготовлены. Теперь можно посчитать match.")
                message_type = "success"
            else:
                message = quote("Вакансия открыта для анализа, но требований найдено мало. Проверьте текст вакансии.")
                message_type = "warning"
        activate_vacancy_for_user(db, user_id=current_user.id, vacancy_id=vacancy_id)
        redirect_url = f"/result?vacancy_id={vacancy_id}&message={message}&message_type={message_type}"
    except Exception as exc:
        message = quote(_extract_vacancy_form_error(exc))
        message_type = "danger"
        redirect_url = f"/vacancies?message={message}&message_type={message_type}"

    return RedirectResponse(
        url=redirect_url,
        status_code=status.HTTP_302_FOUND,
    )
