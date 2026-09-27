from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.api.db.models import User, UserSettings


def get_user_by_email(db: Session, email: str) -> User | None:
    stmt = select(User).where(User.email == email)
    return db.scalar(stmt)


def get_user_by_id(db: Session, user_id: int) -> User | None:
    stmt = select(User).where(User.id == user_id)
    return db.scalar(stmt)


def create_user(db: Session, email: str, password_hash: str) -> User:
    user = User(email=email, password_hash=password_hash)
    db.add(user)
    db.flush()

    settings = UserSettings(user_id=user.id)
    db.add(settings)

    db.commit()
    db.refresh(user)
    return user