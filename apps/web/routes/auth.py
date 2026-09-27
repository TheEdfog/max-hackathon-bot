from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from apps.api.db.database import get_db
from apps.api.schemas.auth import LoginRequest, RegisterRequest
from apps.web.dependencies import get_current_user_optional
from apps.web.services.auth_service import AuthError, authenticate_user, register_user
from core.config import settings
from core.security import create_access_token

router = APIRouter(tags=["auth"])


def _build_context(
    request: Request,
    title: str,
    error: str | None = None,
    current_user=None,
    email_value: str = "",
) -> dict:
    return {
        "request": request,
        "title": title,
        "error": error,
        "current_user": current_user,
        "email_value": email_value,
        "flash_message": request.query_params.get("message"),
        "flash_type": request.query_params.get("message_type", "info"),
    }


def _set_auth_cookie(response: RedirectResponse, token: str) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=settings.access_token_expire_minutes * 60,
    )


def _extract_error_message(exc: Exception) -> str:
    if isinstance(exc, AuthError):
        return str(exc)

    if isinstance(exc, ValidationError):
        first_error = exc.errors()[0] if exc.errors() else None
        if not first_error:
            return "Проверьте корректность введённых данных."

        field_name = first_error.get("loc", [""])[-1]
        if field_name == "email":
            return "Введите корректный email."
        if field_name == "password":
            return "Пароль должен содержать не менее 6 символов."

    return "Проверьте корректность введённых данных."


@router.get("/login", response_class=HTMLResponse, name="login_page")
async def login_page(
    request: Request,
    current_user=Depends(get_current_user_optional),
):
    if current_user is not None:
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)

    return request.app.state.templates.TemplateResponse(
        request=request,
        name="auth/login.html",
        context=_build_context(request, "Вход"),
    )


@router.post("/login", response_class=HTMLResponse, name="login_submit")
async def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    try:
        data = LoginRequest(email=email, password=password)
        user = authenticate_user(db=db, email=data.email, password=data.password)
    except Exception as exc:
        return request.app.state.templates.TemplateResponse(
            request=request,
            name="auth/login.html",
            context=_build_context(request, "Вход", error=_extract_error_message(exc), email_value=email),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    token = create_access_token(user_id=user.id, email=user.email)
    response = RedirectResponse(
        url=f"/dashboard?message={quote('Вы успешно вошли в систему')}&message_type=success",
        status_code=status.HTTP_302_FOUND,
    )
    _set_auth_cookie(response, token)
    return response


@router.get("/register", response_class=HTMLResponse, name="register_page")
async def register_page(
    request: Request,
    current_user=Depends(get_current_user_optional),
):
    if current_user is not None:
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)

    return request.app.state.templates.TemplateResponse(
        request=request,
        name="auth/register.html",
        context=_build_context(request, "Регистрация"),
    )


@router.post("/register", response_class=HTMLResponse, name="register_submit")
async def register_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    try:
        data = RegisterRequest(email=email, password=password)
        user = register_user(db=db, email=data.email, password=data.password)
    except Exception as exc:
        return request.app.state.templates.TemplateResponse(
            request=request,
            name="auth/register.html",
            context=_build_context(request, "Регистрация", error=_extract_error_message(exc), email_value=email),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    token = create_access_token(user_id=user.id, email=user.email)
    response = RedirectResponse(
        url=f"/dashboard?message={quote('Регистрация прошла успешно')}&message_type=success",
        status_code=status.HTTP_302_FOUND,
    )
    _set_auth_cookie(response, token)
    return response


@router.post("/logout", name="logout")
async def logout():
    response = RedirectResponse(
        url=f"/login?message={quote('Вы вышли из системы')}&message_type=warning",
        status_code=status.HTTP_302_FOUND,
    )
    response.delete_cookie(settings.session_cookie_name)
    return response
