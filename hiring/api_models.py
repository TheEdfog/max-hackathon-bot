"""Public request schemas shared by the HTTP and integration routes."""
from typing import Literal
from pydantic import BaseModel, EmailStr, Field, field_validator


class Login(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class Registration(Login):
    name: str = Field(min_length=2, max_length=160)
    company: str = Field(default="", max_length=160)
    role: Literal["employer", "candidate"] = "candidate"
    code: str = Field(default="", max_length=200)


class TextBody(BaseModel):
    text: str = Field(min_length=20, max_length=20000)


class Requirement(BaseModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,40}$")
    skill: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=120)
    type: Literal["must", "nice"] = "must"
    source: str = Field(default="", max_length=700)

    @field_validator('id')
    @classmethod
    def reserved_ids(cls, value):
        if value.startswith(('screen_', 'test_')):
            raise ValueError('screen_ and test_ are reserved for interview questions')
        return value


class JobBody(BaseModel):
    title: str = Field(min_length=3, max_length=160)
    description: str = Field(min_length=30, max_length=20000)
    terms: str = Field(default="", max_length=500)
    requirements: list[Requirement] = Field(min_length=1, max_length=15)
    screening_questions: list[Literal['screen_motivation', 'screen_conditions', 'screen_availability']] = Field(default_factory=list, max_length=3)

    @field_validator('screening_questions')
    @classmethod
    def unique_screening(cls, value):
        if len(set(value)) != len(value):
            raise ValueError('Вопросы не должны повторяться')
        return value

    @field_validator("requirements")
    @classmethod
    def unique_requirements(cls, value):
        from .skills import normalize_skill
        if len({r.id for r in value}) != len(value) or len({normalize_skill(r.skill) for r in value}) != len(value):
            raise ValueError("Требования не должны повторяться")
        return value


class ApplicationBody(BaseModel):
    resume: str = Field(min_length=40, max_length=20000)
    consent: Literal[True]
    name: str = Field(min_length=2, max_length=160)

    @field_validator('name', 'resume', mode='before')
    @classmethod
    def strip_text(cls, value):
        return value.strip() if isinstance(value, str) else value


class AnswersBody(BaseModel):
    answers: dict[str, str] = Field(max_length=10)

    @field_validator("answers")
    @classmethod
    def lengths(cls, value):
        if any(not 2 <= len(v.strip()) <= 2500 for v in value.values()):
            raise ValueError("Ответы: от 2 до 2500 символов")
        return {k: v.strip() for k, v in value.items()}


class InviteBody(BaseModel):
    message: str = Field(min_length=10, max_length=1500)


class MaxBody(BaseModel):
    init_data: str = Field(min_length=1, max_length=16000)


class EmployerBody(BaseModel):
    company: str = Field(min_length=2, max_length=160)
    code: str = Field(default='', max_length=200)


class ActiveBody(BaseModel):
    active: bool
