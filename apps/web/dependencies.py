from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from apps.api.db.database import get_db
from apps.api.db.models import User
from apps.api.repositories.user_repository import get_user_by_id
from core.config import settings
from core.security import decode_access_token


def get_current_user_optional(
    request: Request,
    db: Session = Depends(get_db),
) -> User | None:
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        return None

    payload = decode_access_token(token)
    if payload is None:
        return None

    user_id_raw = payload.get("sub")
    if not user_id_raw:
        return None

    try:
        user_id = int(user_id_raw)
    except (TypeError, ValueError):
        return None

    return get_user_by_id(db, user_id)


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    user = get_current_user_optional(request=request, db=db)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Требуется авторизация",
        )
    return user