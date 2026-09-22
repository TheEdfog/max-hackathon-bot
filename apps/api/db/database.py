from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import MetaData, create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from core.config import settings

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Base(DeclarativeBase):
    metadata = metadata


engine = create_engine(
    settings.database_url,
    echo=settings.debug,
    pool_pre_ping=True,
    future=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    class_=Session,
)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    # Import is required so SQLAlchemy sees all models before create_all.
    from apps.api.db import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _ensure_runtime_schema_updates()


def _ensure_runtime_schema_updates() -> None:
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())

    if "vacancies" in table_names:
        vacancy_columns = {column["name"] for column in inspector.get_columns("vacancies")}
        if "raw_data_path" not in vacancy_columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE vacancies ADD COLUMN raw_data_path VARCHAR(500)"))

    if "vacancy_requirements" in table_names:
        requirement_columns = {column["name"] for column in inspector.get_columns("vacancy_requirements")}
        requirement_runtime_columns = {
            "display_name": "VARCHAR(150)",
            "category": "VARCHAR(80)",
            "source_text": "TEXT",
            "confidence": "DOUBLE PRECISION",
        }

        with engine.begin() as connection:
            for column_name, column_type in requirement_runtime_columns.items():
                if column_name not in requirement_columns:
                    connection.execute(text(f"ALTER TABLE vacancy_requirements ADD COLUMN {column_name} {column_type}"))

    if "profiles" in table_names:
        profile_columns = {column["name"] for column in inspector.get_columns("profiles")}
        profile_runtime_columns = {
            "city": "VARCHAR(120)",
            "work_format": "VARCHAR(120)",
            "salary_expectation": "VARCHAR(120)",
            "experience_text": "TEXT",
            "projects_text": "TEXT",
            "education_text": "TEXT",
            "certificates_text": "TEXT",
            "languages_text": "TEXT",
            "soft_skills_text": "TEXT",
            "experience_entries": "JSONB",
            "project_entries": "JSONB",
            "education_entries": "JSONB",
            "course_entries": "JSONB",
            "activity_entries": "JSONB",
        }

        with engine.begin() as connection:
            for column_name, column_type in profile_runtime_columns.items():
                if column_name not in profile_columns:
                    connection.execute(text(f"ALTER TABLE profiles ADD COLUMN {column_name} {column_type}"))
