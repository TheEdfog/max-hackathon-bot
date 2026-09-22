from __future__ import annotations

import enum
from typing import Optional

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from apps.api.db.database import Base


class RequirementType(str, enum.Enum):
    MUST = "must"
    NICE = "nice"


requirement_type_enum = Enum(
    RequirementType,
    name="vacancy_requirement_type",
    native_enum=False,
    values_callable=lambda enum_cls: [item.value for item in enum_cls],
)


class TimestampMixin:
    created_at: Mapped[str] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[str] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[str] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    profiles: Mapped[list["Profile"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    vacancies: Mapped[list["Vacancy"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    generated_resumes: Mapped[list["GeneratedResume"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    generated_cover_letters: Mapped[list["GeneratedCoverLetter"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    settings: Mapped[Optional["UserSettings"]] = relationship(
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
        foreign_keys="UserSettings.user_id",
    )


class Profile(Base, TimestampMixin):
    __tablename__ = "profiles"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_profiles_user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    target_position: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str] = mapped_column(String(50), nullable=False)
    city: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    work_format: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    salary_expectation: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    github_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    linkedin_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    summary_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    experience_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    projects_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    education_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    certificates_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    languages_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    soft_skills_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    experience_entries: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    project_entries: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    education_entries: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    course_entries: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    activity_entries: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)

    user: Mapped["User"] = relationship(back_populates="profiles")
    skills: Mapped[list["ProfileSkill"]] = relationship(
        back_populates="profile",
        cascade="all, delete-orphan",
    )
    attachments: Mapped[list["ProfileAttachment"]] = relationship(
        back_populates="profile",
        cascade="all, delete-orphan",
    )
    generated_resumes: Mapped[list["GeneratedResume"]] = relationship(
        back_populates="profile",
    )
    generated_cover_letters: Mapped[list["GeneratedCoverLetter"]] = relationship(
        back_populates="profile",
    )


class ProfileSkill(Base):
    __tablename__ = "profile_skills"
    __table_args__ = (
        UniqueConstraint("profile_id", "skill_norm", name="uq_profile_skills_profile_id_skill_norm"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False, index=True)
    skill_norm: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    created_at: Mapped[str] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    profile: Mapped["Profile"] = relationship(back_populates="skills")


class ProfileAttachment(Base):
    __tablename__ = "profile_attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False, index=True)

    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(500), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    uploaded_at: Mapped[str] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    profile: Mapped["Profile"] = relationship(back_populates="attachments")


class Vacancy(Base, TimestampMixin):
    __tablename__ = "vacancies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    source_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    raw_data_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    user: Mapped["User"] = relationship(back_populates="vacancies")
    requirements: Mapped[list["VacancyRequirement"]] = relationship(
        back_populates="vacancy",
        cascade="all, delete-orphan",
    )
    generated_resumes: Mapped[list["GeneratedResume"]] = relationship(
        back_populates="vacancy",
    )
    generated_cover_letters: Mapped[list["GeneratedCoverLetter"]] = relationship(
        back_populates="vacancy",
    )


class VacancyRequirement(Base):
    __tablename__ = "vacancy_requirements"
    __table_args__ = (
        UniqueConstraint(
            "vacancy_id",
            "type",
            "skill_norm",
            name="uq_vacancy_requirements_vacancy_id_type_skill_norm",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vacancy_id: Mapped[int] = mapped_column(ForeignKey("vacancies.id", ondelete="CASCADE"), nullable=False, index=True)
    type: Mapped[RequirementType] = mapped_column(requirement_type_enum, nullable=False)
    skill_norm: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    display_name: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    category: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    source_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    created_at: Mapped[str] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    vacancy: Mapped["Vacancy"] = relationship(back_populates="requirements")


class GeneratedResume(Base):
    __tablename__ = "generated_resumes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False, index=True)
    vacancy_id: Mapped[int] = mapped_column(ForeignKey("vacancies.id", ondelete="CASCADE"), nullable=False, index=True)

    resume_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    latex_source: Mapped[str] = mapped_column(Text, nullable=False)
    pdf_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_at: Mapped[str] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    user: Mapped["User"] = relationship(back_populates="generated_resumes")
    profile: Mapped["Profile"] = relationship(back_populates="generated_resumes")
    vacancy: Mapped["Vacancy"] = relationship(back_populates="generated_resumes")


class GeneratedCoverLetter(Base):
    __tablename__ = "generated_cover_letters"
    __table_args__ = (
        UniqueConstraint("user_id", "vacancy_id", name="uq_generated_cover_letters_user_id_vacancy_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False, index=True)
    vacancy_id: Mapped[int] = mapped_column(ForeignKey("vacancies.id", ondelete="CASCADE"), nullable=False, index=True)

    letter_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    latex_source: Mapped[str] = mapped_column(Text, nullable=False)
    pdf_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_at: Mapped[str] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[str] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    user: Mapped["User"] = relationship(back_populates="generated_cover_letters")
    profile: Mapped["Profile"] = relationship(back_populates="generated_cover_letters")
    vacancy: Mapped["Vacancy"] = relationship(back_populates="generated_cover_letters")


class UserSettings(Base):
    __tablename__ = "user_settings"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    active_profile_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("profiles.id", ondelete="SET NULL"),
        nullable=True,
    )
    active_vacancy_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("vacancies.id", ondelete="SET NULL"),
        nullable=True,
    )

    user: Mapped["User"] = relationship(
        back_populates="settings",
        foreign_keys=[user_id],
    )
    active_profile: Mapped[Optional["Profile"]] = relationship(
        foreign_keys=[active_profile_id],
    )
    active_vacancy: Mapped[Optional["Vacancy"]] = relationship(
        foreign_keys=[active_vacancy_id],
    )
