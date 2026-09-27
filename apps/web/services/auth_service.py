from __future__ import annotations

from sqlalchemy.orm import Session

from apps.api.db.models import User
from apps.api.repositories.user_repository import create_user, get_user_by_email
from core.security import hash_password, verify_password


class AuthError(Exception):
    pass


def register_user(db: Session, email: str, password: str) -> User:
    existing_user = get_user_by_email(db, email)
    if existing_user is not None:
        raise AuthError("Пользователь с таким email уже существует")

    password_hash = hash_password(password)
    return create_user(db=db, email=email, password_hash=password_hash)


def authenticate_user(db: Session, email: str, password: str) -> User:
    user = get_user_by_email(db, email)
    if user is None:
        raise AuthError("Неверный email или пароль")

    if not verify_password(password, user.password_hash):
        raise AuthError("Неверный email или пароль")

    return user