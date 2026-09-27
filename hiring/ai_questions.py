"""Opt-in runtime drafts from canonical skills only. No applicant data or decisions."""
import hashlib
import json
import re
from datetime import timedelta
from typing import Literal

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select

from core.utils import normalize_skill
from .assessments import TestBody
from .db import AiTestDraft, now, serialize_writes
from .matching import ALIASES

ENDPOINT = 'https://foundation-models.api.cloud.ru/v1/chat/completions'
MODEL = 'ai-sage/GigaChat3-10B-A1.8B'
MAX_COMPLETION_TOKENS = 1000
GLOBAL_DAILY_LIMIT = 10
OWNER_DAILY_LIMIT = 3
LIFETIME_LIMIT = 50
MAX_RESPONSE_BYTES = 65536


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
    source: Literal['gigachat', 'local']
    model: str | None
    cached: bool
    review_required: Literal[True] = True
    published: Literal[False] = False
    notice: str


def local_result(body, reason):
    draft = TestBody(title='Вопросы: ' + ', '.join(body.skills)[:100], questions=[{
        'text': f'Опишите рабочую задачу с {skill}. Как вы проверите результат и что сделаете при ошибке?',
        'rubric': 'Проверьте конкретность задачи, роль кандидата, способ проверки и ограничения решения.'
    } for skill in body.skills[:3]])
    return AiDraftResult(draft=draft, source='local', model=None, cached=False,
                         notice=reason + ' Использован локальный шаблон. Проверьте и сохраните тест отдельно.')


def generate(body, api_key):
    # Only canonical labels and an enum leave this process. Never pass a job,
    # resume, profile, chat history, README or developer prompt to this function.
    payload = {
        'model': MODEL, 'max_tokens': MAX_COMPLETION_TOKENS, 'temperature': 0.3, 'top_p': 0.95,
        'messages': [
            {'role': 'system', 'content':
             'Ты готовишь профессиональные вопросы для первичного интервью. '
             'Верни только JSON с полями title и questions. questions: 1-3 объекта с text и rubric. '
             'Пиши по-русски. text: 10-500 символов, rubric: до 700, title: 3-100. '
             'Вопросы должны соответствовать заданным навыкам и уровню. '
             'Без личных данных, возраста, пола, здоровья, национальности, религии, семейного положения. '
             'Без внешних ссылок, инструкций запуска кода и оценки кандидата. '
             'Критерии нужны только человеку для ручного обсуждения ответа.'},
            {'role': 'user', 'content': json.dumps({'skills': body.skills, 'level': body.level}, ensure_ascii=False)}],
    }
    with httpx.Client(timeout=12, follow_redirects=False, trust_env=False) as client:
        with client.stream('POST', ENDPOINT, headers={'Authorization': 'Bearer ' + api_key}, json=payload) as response:
            if response.status_code != 200:
                raise ValueError('Provider unavailable')
            data = bytearray()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data) > MAX_RESPONSE_BYTES:
                    raise ValueError('Response too large')
    envelope = json.loads(data)
    choice = envelope['choices'][0]
    if choice.get('finish_reason') != 'stop':
        raise ValueError('Incomplete draft')
    content = choice['message']['content'].strip()
    if content.startswith('```json') and content.endswith('```'):
        content = content[7:-3].strip()
    draft = TestBody.model_validate_json(content)
    combined = json.dumps(draft.model_dump(), ensure_ascii=False)
    if re.search(r'https?://|www\.|[\w.+-]+@[\w.-]+\.[a-z]{2,}', combined, re.I):
        raise ValueError('External link or contact in draft')
    return draft


def create_draft(db, owner_id, body, config):
    if not config.gigachat_enabled or not config.cloudru_api_key:
        return local_result(body, 'GigaChat отключён или ключ не настроен.')
    digest = hashlib.sha256((MODEL + body.model_dump_json()).encode()).hexdigest()
    cutoff = now() - timedelta(hours=24)
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
            return AiDraftResult(draft=TestBody.model_validate(result), source='gigachat', model=MODEL,
                                 cached=True, notice='Кэш 24 часа. Проверьте и сохраните тест отдельно.')
        return local_result(body, 'Повторный внешний запрос для этого набора ограничен на 24 часа.')
    total = db.scalar(select(func.count()).select_from(AiTestDraft))
    daily = db.scalar(select(func.count()).select_from(AiTestDraft).where(AiTestDraft.created_at >= cutoff))
    owner_daily = db.scalar(select(func.count()).select_from(AiTestDraft).where(
        AiTestDraft.created_at >= cutoff, AiTestDraft.owner_id == owner_id))
    if total >= LIFETIME_LIMIT or daily >= GLOBAL_DAILY_LIMIT or owner_daily >= OWNER_DAILY_LIMIT:
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
        draft = generate(body, config.cloudru_api_key)
    except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError, AttributeError):
        row = db.get(AiTestDraft, identifier)
        row.status = 'failed'
        db.commit()
        return local_result(body, 'GigaChat не вернул проверяемый черновик.')
    row = db.get(AiTestDraft, identifier)
    row.status, row.result = 'ready', draft.model_dump()
    db.commit()
    return AiDraftResult(draft=draft, source='gigachat', model=MODEL, cached=False,
                         notice='AI-черновик. Проверьте корректность и сохраните тест отдельно. Решение принимает человек.')
