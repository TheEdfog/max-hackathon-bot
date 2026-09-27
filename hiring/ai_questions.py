"""Opt-in runtime drafts from canonical skills only. No applicant data or decisions."""
import hashlib
import json
import re
from datetime import timedelta
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select

from core.utils import normalize_skill
from .assessments import TestBody
from .db import AiTestDraft, now, serialize_writes
from .matching import ALIASES
from .llm import CLOUDRU_MODEL, LLMSettings, complete

PROMPT_VERSION = 'interview-questions-1'
CACHE_HOURS = 24


class AiDraftBody(BaseModel):
    model_config = {'extra': 'forbid'}
    skills: list[str] = Field(min_length=1, max_length=5)
    level: Literal['junior', 'middle', 'senior'] = 'junior'
    allow_external_generation: Literal[True]

    @field_validator('skills')
    @classmethod
    def canonical_only(cls, values):
        result = [normalize_skill(v.strip()) for v in values]
        if any(v not in ALIASES for v in result) or len(set(result)) != len(result):
            raise ValueError('Выберите 1-5 разных навыков из /tests/ai-skills. Свободный текст запрещён.')
        return sorted(result)


class AiDraftResult(BaseModel):
    draft: TestBody
    source: Literal['gigachat', 'llm', 'local']
    provider: str | None = None
    model: str | None
    cached: bool
    review_required: Literal[True] = True
    published: Literal[False] = False
    notice: str


class AiDraftStatus(BaseModel):
    provider: str
    model: str | None
    ready: bool
    owner_requests_24h: int
    owner_limit_24h: int
    available_requests: int
    max_output_tokens: int
    cache_hours: int = CACHE_HOURS
    notice: str = 'Считаются запросы, включая неудачные. Это не баланс провайдера. Кэш не расходует запросы.'


def request_counts(db, owner_id):
    recent = AiTestDraft.created_at >= now() - timedelta(hours=CACHE_HOURS)
    return db.execute(select(
        func.count(AiTestDraft.id),
        func.count(AiTestDraft.id).filter(recent),
        func.count(AiTestDraft.id).filter(recent, AiTestDraft.owner_id == owner_id),
    )).one()


def draft_status(db, owner_id, settings):
    total, daily, owner_daily = request_counts(db, owner_id)
    available = max(0, min(settings.total_limit - total, settings.daily_limit - daily,
                           settings.owner_daily_limit - owner_daily)) if settings.ready else 0
    return AiDraftStatus(provider=settings.provider, model=settings.model or None, ready=settings.ready,
                         owner_requests_24h=owner_daily, owner_limit_24h=settings.owner_daily_limit,
                         available_requests=available, max_output_tokens=settings.max_tokens)


def local_result(body, reason):
    draft = TestBody(title='Вопросы: ' + ', '.join(body.skills)[:100], questions=[{
        'text': f'Опишите рабочую задачу с {skill}. Как вы проверите результат и что сделаете при ошибке?',
        'rubric': 'Проверьте конкретность задачи, роль кандидата, способ проверки и ограничения решения.'
    } for skill in body.skills[:3]])
    return AiDraftResult(draft=draft, source='local', model=None, cached=False,
                         notice=reason + ' Использован локальный шаблон. Проверьте и сохраните тест отдельно.')


def generate(body: AiDraftBody, settings: LLMSettings) -> TestBody:
    # Only canonical labels and an enum leave this process. Never pass a job,
    # resume, profile, chat history, README or developer prompt to this function.
    messages = [
            {'role': 'system', 'content':
             'Ты готовишь профессиональные вопросы для первичного интервью. '
             'Верни только JSON с полями title и questions. questions: 1-3 объекта с text и rubric. '
             'Пиши по-русски. text: 10-500 символов, rubric: до 700, title: 3-100. '
             'Вопросы должны соответствовать заданным навыкам и уровню. '
             'Без личных данных, возраста, пола, здоровья, национальности, религии, семейного положения. '
             'Без внешних ссылок, инструкций запуска кода и оценки кандидата. '
             'Критерии нужны только человеку для ручного обсуждения ответа.'},
            {'role': 'user', 'content': json.dumps({'skills': body.skills, 'level': body.level}, ensure_ascii=False)}]
    content = complete(settings, messages)
    if content.startswith('```json') and content.endswith('```'):
        content = content[7:-3].strip()
    draft = TestBody.model_validate_json(content)
    combined = json.dumps(draft.model_dump(), ensure_ascii=False)
    if re.search(r'https?://|www\.|[\w.+-]+@[\w.-]+\.[a-z]{2,}', combined, re.I):
        raise ValueError('External link or contact in draft')
    return draft


def create_draft(db, owner_id, body, config):
    settings = config.llm
    settings.validate()
    if not settings.ready:
        return local_result(body, 'Внешняя модель отключена или ключ не настроен.')
    cache_key = [PROMPT_VERSION, settings.provider, settings.base_url, settings.model,
                 settings.max_tokens, body.model_dump()]
    digest = hashlib.sha256(json.dumps(cache_key, sort_keys=True).encode()).hexdigest()
    cutoff = now() - timedelta(hours=CACHE_HOURS)
    # End read-only authorization transaction, then serialize reservation. The
    # network request below never holds the SQLite write lock.
    db.rollback()
    serialize_writes(db)
    recent = db.scalar(select(AiTestDraft).where(AiTestDraft.owner_id == owner_id,
        AiTestDraft.digest == digest, AiTestDraft.created_at >= cutoff).order_by(AiTestDraft.created_at.desc()))
    if recent:
        status, result = recent.status, dict(recent.result)
        db.rollback()
        if status == 'ready':
            return model_result(TestBody.model_validate(result), settings, cached=True)
        return local_result(body, 'Повторный внешний запрос для этого набора ограничен на 24 часа.')
    total, daily, owner_daily = request_counts(db, owner_id)
    if total >= settings.total_limit or daily >= settings.daily_limit or owner_daily >= settings.owner_daily_limit:
        db.rollback()
        raise HTTPException(429, 'Лимит AI-черновиков. Создайте тест вручную. Автоповторов нет.')
    active = db.scalar(select(AiTestDraft.id).where(AiTestDraft.status == 'reserved',
                       AiTestDraft.created_at >= now() - timedelta(minutes=2)).limit(1))
    if active:
        db.rollback()
        raise HTTPException(409, 'Уже формируется другой AI-черновик. Повторите позже.')
    row = AiTestDraft(owner_id=owner_id, digest=digest)
    db.add(row)
    db.commit()
    identifier = row.id
    try:
        draft = generate(body, settings)
    except ValueError:
        row = db.get(AiTestDraft, identifier)
        row.status = 'failed'
        db.commit()
        return local_result(body, 'Внешняя модель не вернула проверяемый черновик.')
    row = db.get(AiTestDraft, identifier)
    row.status, row.result = 'ready', draft.model_dump()
    db.commit()
    return model_result(draft, settings)


def model_result(draft, settings, cached=False):
    # Preserve the original API value for the previously supported model.
    source = 'gigachat' if settings.provider == 'cloudru' and settings.model == CLOUDRU_MODEL else 'llm'
    return AiDraftResult(draft=draft, source=source, provider=settings.provider, model=settings.model,
                         cached=cached, notice='Проверьте черновик и сохраните тест отдельно. Решение принимает человек.')
