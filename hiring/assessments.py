"""Employer-owned reusable questions. No generated scores or automatic decisions."""
from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from .db import Application, AssessmentTemplate
from .teams import company_member_ids


class TestQuestion(BaseModel):
    model_config = {'extra': 'forbid'}
    text: str = Field(min_length=10, max_length=800)
    rubric: str = Field(default='', max_length=1500)

    @field_validator('text', 'rubric', mode='before')
    @classmethod
    def clean(cls, value):
        return value.strip() if isinstance(value, str) else value


class TestBody(BaseModel):
    model_config = {'extra': 'forbid'}
    title: str = Field(min_length=3, max_length=120)
    questions: list[TestQuestion] = Field(min_length=1, max_length=3)
    active: bool = True

    @field_validator('title', mode='before')
    @classmethod
    def clean_title(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator('questions')
    @classmethod
    def unique(cls, value):
        if len({q.text.casefold() for q in value}) != len(value):
            raise ValueError('Вопросы не должны повторяться')
        return value


def owned_test(db, test_id, owner):
    row = db.get(AssessmentTemplate, test_id)
    if not row or row.owner_id not in company_member_ids(db, owner):
        raise HTTPException(404, 'Тест не найден')
    return row


def create_test(db, owner, body):
    count = db.scalar(select(func.count()).select_from(AssessmentTemplate).where(
        AssessmentTemplate.owner_id.in_(company_member_ids(db, owner))))
    if count >= 100:
        raise HTTPException(409, 'Лимит библиотеки: 100 шаблонов')
    row = AssessmentTemplate(owner_id=owner.id, **body.model_dump())
    db.add(row)
    db.flush()
    return row


def snapshot(row):
    if not row.active:
        raise HTTPException(409, 'Тест находится в архиве')
    return [{'id': f'test_{i}', 'kind': 'assessment', 'label': f'Задание {i + 1}',
             'text': q['text'], 'rubric': q.get('rubric', ''), 'template_id': row.id,
             'template_version': row.version, 'title': row.title} for i, q in enumerate(row.questions)]


def attach_test(db, job, row):
    if db.scalar(select(Application.id).where(Application.job_id == job.id).limit(1)):
        raise HTTPException(409, 'У вакансии есть отклики. Создайте новую версию вакансии.')
    job.test_questions = snapshot(row) if row else []


def test_view(row):
    return {name: getattr(row, name) for name in ('id', 'title', 'questions', 'version', 'active', 'created_at')}
