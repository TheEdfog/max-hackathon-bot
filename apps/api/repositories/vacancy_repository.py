from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from apps.api.db.models import GeneratedCoverLetter, GeneratedResume, RequirementType, UserSettings, Vacancy, VacancyRequirement
from core.utils import normalize_skill


def list_vacancies_for_user(db: Session, user_id: int) -> list[Vacancy]:
    stmt = (
        select(Vacancy)
        .options(selectinload(Vacancy.requirements))
        .where(Vacancy.user_id == user_id)
        .order_by(Vacancy.updated_at.desc(), Vacancy.id.desc())
    )
    return list(db.scalars(stmt).all())


def get_vacancy_by_id_for_user(db: Session, user_id: int, vacancy_id: int) -> Vacancy | None:
    stmt = (
        select(Vacancy)
        .options(selectinload(Vacancy.requirements))
        .where(Vacancy.user_id == user_id, Vacancy.id == vacancy_id)
    )
    return db.scalar(stmt)


def create_vacancy(
    db: Session,
    user_id: int,
    *,
    title: str | None,
    source_url: str | None,
    raw_text: str,
    raw_data_path: str | None = None,
) -> Vacancy:
    vacancy = Vacancy(
        user_id=user_id,
        title=title,
        source_url=source_url,
        raw_text=raw_text,
        raw_data_path=raw_data_path,
    )
    db.add(vacancy)
    db.commit()
    db.refresh(vacancy)
    return vacancy


def update_vacancy(
    db: Session,
    vacancy: Vacancy,
    *,
    title: str | None,
    source_url: str | None,
    raw_text: str,
    raw_data_path: str | None = None,
) -> Vacancy:
    vacancy.title = title
    vacancy.source_url = source_url
    vacancy.raw_text = raw_text
    vacancy.raw_data_path = raw_data_path

    db.commit()
    db.refresh(vacancy)
    return vacancy


def get_or_create_user_settings(db: Session, user_id: int) -> UserSettings:
    stmt = select(UserSettings).where(UserSettings.user_id == user_id)
    settings = db.scalar(stmt)

    if settings is None:
        settings = UserSettings(user_id=user_id)
        db.add(settings)
        db.flush()

    return settings


def set_active_vacancy_for_user(db: Session, user_id: int, vacancy_id: int) -> None:
    settings = get_or_create_user_settings(db, user_id)
    settings.active_vacancy_id = vacancy_id
    db.commit()


def replace_vacancy_requirements(
    db: Session,
    vacancy: Vacancy,
    requirements: list[dict],
) -> Vacancy:
    db.execute(delete(VacancyRequirement).where(VacancyRequirement.vacancy_id == vacancy.id))
    db.flush()

    seen: set[str] = set()
    for item in requirements:
        skill_norm = normalize_skill(item["skill_norm"])
        raw_type = item.get("type", RequirementType.MUST.value)
        requirement_type = RequirementType.NICE if raw_type == RequirementType.NICE.value else RequirementType.MUST
        if not skill_norm or skill_norm in seen:
            continue

        db.add(
            VacancyRequirement(
                vacancy_id=vacancy.id,
                type=requirement_type,
                skill_norm=skill_norm,
                display_name=item.get("display_name") or skill_norm,
                category=item.get("category") or "other",
                source_text=item.get("source_text"),
                confidence=item.get("confidence"),
            )
        )
        seen.add(skill_norm)

    db.commit()
    db.refresh(vacancy)
    return vacancy


def delete_vacancy_for_user(
    db: Session,
    user_id: int,
    vacancy_id: int,
) -> tuple[str | None, list[tuple[int, str | None]], list[tuple[int, str | None]]] | None:
    vacancy = get_vacancy_by_id_for_user(db, user_id=user_id, vacancy_id=vacancy_id)
    if vacancy is None:
        return None

    raw_data_path = vacancy.raw_data_path
    resume_rows = list(
        db.execute(
            select(GeneratedResume.id, GeneratedResume.pdf_path).where(
                GeneratedResume.user_id == user_id,
                GeneratedResume.vacancy_id == vacancy_id,
            )
        ).all()
    )
    cover_letter_rows = list(
        db.execute(
            select(GeneratedCoverLetter.id, GeneratedCoverLetter.pdf_path).where(
                GeneratedCoverLetter.user_id == user_id,
                GeneratedCoverLetter.vacancy_id == vacancy_id,
            )
        ).all()
    )

    settings = db.scalar(select(UserSettings).where(UserSettings.user_id == user_id))
    if settings is not None and settings.active_vacancy_id == vacancy_id:
        settings.active_vacancy_id = None

    db.execute(
        delete(GeneratedResume).where(
            GeneratedResume.user_id == user_id,
            GeneratedResume.vacancy_id == vacancy_id,
        )
    )
    db.execute(
        delete(GeneratedCoverLetter).where(
            GeneratedCoverLetter.user_id == user_id,
            GeneratedCoverLetter.vacancy_id == vacancy_id,
        )
    )
    db.delete(vacancy)
    db.commit()
    return (
        raw_data_path,
        [(resume_id, pdf_path) for resume_id, pdf_path in resume_rows],
        [(letter_id, pdf_path) for letter_id, pdf_path in cover_letter_rows],
    )
