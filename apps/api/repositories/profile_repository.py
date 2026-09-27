from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from apps.api.db.models import GeneratedCoverLetter, GeneratedResume, Profile, ProfileSkill, UserSettings


def list_profiles_for_user(db: Session, user_id: int) -> list[Profile]:
    stmt = (
        select(Profile)
        .options(selectinload(Profile.skills))
        .where(Profile.user_id == user_id)
        .order_by(Profile.updated_at.desc(), Profile.id.desc())
    )
    return list(db.scalars(stmt).all())


def get_profile_for_user(db: Session, user_id: int) -> Profile | None:
    active_profile_id = db.scalar(select(UserSettings.active_profile_id).where(UserSettings.user_id == user_id))
    if active_profile_id is not None:
        active_stmt = (
            select(Profile)
            .options(selectinload(Profile.skills))
            .where(Profile.user_id == user_id, Profile.id == active_profile_id)
        )
        active_profile = db.scalar(active_stmt)
        if active_profile is not None:
            return active_profile

    stmt = (
        select(Profile)
        .options(selectinload(Profile.skills))
        .where(Profile.user_id == user_id)
        .order_by(Profile.updated_at.desc(), Profile.id.desc())
        .limit(1)
    )
    return db.scalar(stmt)


def get_profile_by_id_for_user(db: Session, user_id: int, profile_id: int) -> Profile | None:
    stmt = (
        select(Profile)
        .options(selectinload(Profile.skills))
        .where(Profile.user_id == user_id, Profile.id == profile_id)
    )
    return db.scalar(stmt)


def create_profile(
    db: Session,
    user_id: int,
    *,
    full_name: str,
    target_position: str,
    email: str,
    phone: str,
    city: str | None,
    work_format: str | None,
    salary_expectation: str | None,
    github_url: str | None,
    linkedin_url: str | None,
    summary_text: str | None,
    experience_text: str | None,
    projects_text: str | None,
    education_text: str | None,
    certificates_text: str | None,
    languages_text: str | None,
    soft_skills_text: str | None,
    experience_entries: list[dict[str, str]],
    project_entries: list[dict[str, str]],
    education_entries: list[dict[str, str]],
    course_entries: list[dict[str, str]],
    activity_entries: list[dict[str, str]],
    skills: list[str],
) -> Profile:
    profile = Profile(
        user_id=user_id,
        full_name=full_name,
        target_position=target_position,
        email=email,
        phone=phone,
        city=city,
        work_format=work_format,
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
        experience_entries=experience_entries,
        project_entries=project_entries,
        education_entries=education_entries,
        course_entries=course_entries,
        activity_entries=activity_entries,
    )
    db.add(profile)
    db.flush()

    for skill in skills:
        db.add(ProfileSkill(profile_id=profile.id, skill_norm=skill))

    db.commit()
    db.refresh(profile)
    return profile


def update_profile(
    db: Session,
    profile: Profile,
    *,
    full_name: str,
    target_position: str,
    email: str,
    phone: str,
    city: str | None,
    work_format: str | None,
    salary_expectation: str | None,
    github_url: str | None,
    linkedin_url: str | None,
    summary_text: str | None,
    experience_text: str | None,
    projects_text: str | None,
    education_text: str | None,
    certificates_text: str | None,
    languages_text: str | None,
    soft_skills_text: str | None,
    experience_entries: list[dict[str, str]],
    project_entries: list[dict[str, str]],
    education_entries: list[dict[str, str]],
    course_entries: list[dict[str, str]],
    activity_entries: list[dict[str, str]],
    skills: list[str],
) -> Profile:
    profile.full_name = full_name
    profile.target_position = target_position
    profile.email = email
    profile.phone = phone
    profile.city = city
    profile.work_format = work_format
    profile.salary_expectation = salary_expectation
    profile.github_url = github_url
    profile.linkedin_url = linkedin_url
    profile.summary_text = summary_text
    profile.experience_text = experience_text
    profile.projects_text = projects_text
    profile.education_text = education_text
    profile.certificates_text = certificates_text
    profile.languages_text = languages_text
    profile.soft_skills_text = soft_skills_text
    profile.experience_entries = experience_entries
    profile.project_entries = project_entries
    profile.education_entries = education_entries
    profile.course_entries = course_entries
    profile.activity_entries = activity_entries

    db.execute(delete(ProfileSkill).where(ProfileSkill.profile_id == profile.id))
    db.flush()

    for skill in skills:
        db.add(ProfileSkill(profile_id=profile.id, skill_norm=skill))

    db.commit()
    db.refresh(profile)
    return profile


def get_or_create_user_settings(db: Session, user_id: int) -> UserSettings:
    stmt = select(UserSettings).where(UserSettings.user_id == user_id)
    settings = db.scalar(stmt)

    if settings is None:
        settings = UserSettings(user_id=user_id)
        db.add(settings)
        db.flush()

    return settings


def set_active_profile_for_user(db: Session, user_id: int, profile_id: int) -> None:
    settings = get_or_create_user_settings(db, user_id)
    settings.active_profile_id = profile_id
    db.commit()


def delete_profile_for_user(
    db: Session,
    user_id: int,
    profile_id: int,
) -> tuple[list[tuple[int, str | None]], list[tuple[int, str | None]]]:
    profile = get_profile_by_id_for_user(db, user_id=user_id, profile_id=profile_id)
    if profile is None:
        return [], []

    resume_rows = list(
        db.execute(
            select(GeneratedResume.id, GeneratedResume.pdf_path).where(
                GeneratedResume.user_id == user_id,
                GeneratedResume.profile_id == profile_id,
            )
        ).all()
    )
    cover_letter_rows = list(
        db.execute(
            select(GeneratedCoverLetter.id, GeneratedCoverLetter.pdf_path).where(
                GeneratedCoverLetter.user_id == user_id,
                GeneratedCoverLetter.profile_id == profile_id,
            )
        ).all()
    )

    settings = db.scalar(select(UserSettings).where(UserSettings.user_id == user_id))
    if settings is not None:
        if settings.active_profile_id == profile_id:
            settings.active_profile_id = None
        settings.active_vacancy_id = None

    db.execute(
        delete(GeneratedResume).where(
            GeneratedResume.user_id == user_id,
            GeneratedResume.profile_id == profile_id,
        )
    )
    db.execute(
        delete(GeneratedCoverLetter).where(
            GeneratedCoverLetter.user_id == user_id,
            GeneratedCoverLetter.profile_id == profile_id,
        )
    )
    db.delete(profile)
    db.commit()
    return (
        [(resume_id, pdf_path) for resume_id, pdf_path in resume_rows],
        [(letter_id, pdf_path) for letter_id, pdf_path in cover_letter_rows],
    )
