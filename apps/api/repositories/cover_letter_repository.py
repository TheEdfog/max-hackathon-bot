from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from apps.api.db.models import GeneratedCoverLetter


def upsert_generated_cover_letter(
    db: Session,
    *,
    user_id: int,
    profile_id: int,
    vacancy_id: int,
    letter_json: dict,
    latex_source: str,
    pdf_path: str | None = None,
) -> GeneratedCoverLetter:
    stmt = select(GeneratedCoverLetter).where(
        GeneratedCoverLetter.user_id == user_id,
        GeneratedCoverLetter.vacancy_id == vacancy_id,
    )
    letter = db.scalar(stmt)
    if letter is None:
        letter = GeneratedCoverLetter(
            user_id=user_id,
            profile_id=profile_id,
            vacancy_id=vacancy_id,
            letter_json=letter_json,
            latex_source=latex_source,
            pdf_path=pdf_path,
        )
        db.add(letter)
    else:
        letter.profile_id = profile_id
        letter.letter_json = letter_json
        letter.latex_source = latex_source
        letter.pdf_path = pdf_path

    db.commit()
    db.refresh(letter)
    return letter


def get_cover_letter_by_id_for_user(
    db: Session,
    *,
    user_id: int,
    letter_id: int,
) -> GeneratedCoverLetter | None:
    stmt = (
        select(GeneratedCoverLetter)
        .options(
            selectinload(GeneratedCoverLetter.profile),
            selectinload(GeneratedCoverLetter.vacancy),
        )
        .where(GeneratedCoverLetter.user_id == user_id, GeneratedCoverLetter.id == letter_id)
    )
    return db.scalar(stmt)


def get_cover_letter_for_pair(
    db: Session,
    *,
    user_id: int,
    profile_id: int,
    vacancy_id: int,
) -> GeneratedCoverLetter | None:
    stmt = (
        select(GeneratedCoverLetter)
        .where(
            GeneratedCoverLetter.user_id == user_id,
            GeneratedCoverLetter.profile_id == profile_id,
            GeneratedCoverLetter.vacancy_id == vacancy_id,
        )
        .order_by(GeneratedCoverLetter.updated_at.desc(), GeneratedCoverLetter.id.desc())
        .limit(1)
    )
    return db.scalar(stmt)


def get_cover_letter_for_vacancy(
    db: Session,
    *,
    user_id: int,
    vacancy_id: int,
) -> GeneratedCoverLetter | None:
    stmt = (
        select(GeneratedCoverLetter)
        .where(
            GeneratedCoverLetter.user_id == user_id,
            GeneratedCoverLetter.vacancy_id == vacancy_id,
        )
        .order_by(GeneratedCoverLetter.updated_at.desc(), GeneratedCoverLetter.id.desc())
        .limit(1)
    )
    return db.scalar(stmt)
