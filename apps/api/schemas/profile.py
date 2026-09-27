from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field, field_validator

from core.utils import normalize_optional_text, normalize_optional_url


class ProfileFormData(BaseModel):
    full_name: str = Field(min_length=1, max_length=255)
    target_position: str = Field(min_length=1, max_length=255)
    email: EmailStr
    phone: str = Field(min_length=1, max_length=50)

    city: str | None = Field(default=None, max_length=120)
    work_format: str | None = Field(default=None, max_length=120)
    salary_expectation: str | None = Field(default=None, max_length=120)
    github_url: str | None = None
    linkedin_url: str | None = None
    summary_text: str | None = None
    experience_text: str | None = None
    projects_text: str | None = None
    education_text: str | None = None
    certificates_text: str | None = None
    languages_text: str | None = None
    soft_skills_text: str | None = None
    skills_text: str | None = None
    experience_entries: list[dict[str, str]] = Field(default_factory=list)
    project_entries: list[dict[str, str]] = Field(default_factory=list)
    education_entries: list[dict[str, str]] = Field(default_factory=list)
    course_entries: list[dict[str, str]] = Field(default_factory=list)
    activity_entries: list[dict[str, str]] = Field(default_factory=list)

    @field_validator("full_name", "target_position", "phone")
    @classmethod
    def validate_required_text_fields(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Поле не может быть пустым")
        return normalized

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: EmailStr) -> str:
        return value.strip().lower()

    @field_validator("github_url", "linkedin_url", mode="before")
    @classmethod
    def validate_optional_urls(cls, value):
        return normalize_optional_url(value)

    @field_validator(
        "city",
        "work_format",
        "salary_expectation",
        "summary_text",
        "experience_text",
        "projects_text",
        "education_text",
        "certificates_text",
        "languages_text",
        "soft_skills_text",
        "skills_text",
        mode="before",
    )
    @classmethod
    def normalize_optional_fields(cls, value):
        return normalize_optional_text(value)
