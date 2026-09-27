"""
Репозиторий сгенерированных резюме.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from apps.api.db.models import GeneratedResume


def create_generated_resume(
    db: Session,
    *,
    user_id: int,
    profile_id: int,
    vacancy_id: int,
    resume_json: dict,
    latex_source: str,
    pdf_path: str | None = None,
) -> GeneratedResume:
    resume = GeneratedResume(
        user_id=user_id,
        profile_id=profile_id,
        vacancy_id=vacancy_id,
        resume_json=resume_json,
        latex_source=latex_source,
        pdf_path=pdf_path,
    )
    db.add(resume)
    db.commit()
    db.refresh(resume)
    return resume


def get_generated_resume_by_id_for_user(
    db: Session,
    *,
    user_id: int,
    resume_id: int,
) -> GeneratedResume | None:
    stmt = (
        select(GeneratedResume)
        .options(
            selectinload(GeneratedResume.profile),
            selectinload(GeneratedResume.vacancy),
        )
        .where(GeneratedResume.user_id == user_id, GeneratedResume.id == resume_id)
    )
    return db.scalar(stmt)


def list_generated_resumes_for_pair(
    db: Session,
    *,
    user_id: int,
    profile_id: int,
    vacancy_id: int,
) -> list[GeneratedResume]:
    stmt = (
        select(GeneratedResume)
        .where(
            GeneratedResume.user_id == user_id,
            GeneratedResume.profile_id == profile_id,
            GeneratedResume.vacancy_id == vacancy_id,
        )
        .order_by(GeneratedResume.created_at.desc(), GeneratedResume.id.desc())
    )
    return list(db.scalars(stmt).all())
