"""Versioned server-to-server API. Keys never grant account-management access."""
import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Generic, Literal, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from fastapi.security import HTTPBearer
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from .db import Application, ExternalJob, IntegrationEvent, IntegrationKey, Job, User, now, serialize_writes, uid
from .services import application_view, invite, owned_job

Scope = Literal['jobs:read', 'jobs:write', 'applications:read', 'applications:pii', 'invitations:write', 'events:read', 'metrics:read']
READ_SCOPES = ['jobs:read', 'applications:read', 'events:read', 'metrics:read']
T = TypeVar('T')


class KeyInfo(BaseModel):
    id: str
    name: str
    scopes: list[Scope]
    revoked: bool
    created_at: datetime
    expires_at: datetime


class KeyIssued(KeyInfo):
    token: str
    notice: str


class JobInfo(BaseModel):
    id: str
    title: str
    company: str
    description: str
    terms: str
    requirements: list[dict]
    screening_questions: list[str]
    active: bool
    applications: int
    created_at: datetime
    apply_url: str
    max_url: str | None
    external_id: str | None
    source: str | None


class Page(BaseModel, Generic[T]):
    items: list[T]
    next_cursor: str | None


class AppSummary(BaseModel):
    id: str
    job_id: str
    status: str
    created_at: datetime
    consent_at: datetime
    answered_questions: int
    total_questions: int
    deleted: Literal[False]


class AppPrivate(BaseModel):
    id: str
    job_id: str
    status: str
    name: str
    job_title: str
    company: str
    resume: str
    answers: dict[str, str]
    questions: list[dict]
    invitation: str
    assessment: dict
    timeline: list[dict]
    created_at: datetime
    consent_at: datetime
    deleted: Literal[False]


class Tombstone(BaseModel):
    id: str
    job_id: str
    status: Literal['withdrawn']
    deleted: Literal[True]


class EventInfo(BaseModel):
    seq: int
    type: str
    resource_id: str
    at: datetime


class EventPage(BaseModel):
    items: list[EventInfo]
    next_cursor: str
    has_more: bool


class InvitationResult(BaseModel):
    id: str
    status: str
    delivery: Literal['queued', 'no_max_link']


class Metrics(BaseModel):
    active_jobs: int
    applications: int
    statuses: dict[str, int]
    notice: str


class KeyCreate(BaseModel):
    model_config = {'extra': 'forbid'}
    name: str = Field(min_length=2, max_length=80)
    scopes: list[Scope] = Field(default_factory=lambda: list(READ_SCOPES), min_length=1, max_length=7)
    expires_in_days: int = Field(default=30, ge=1, le=90)

    @field_validator('name')
    @classmethod
    def valid_name(cls, value):
        if len(value.strip()) < 2:
            raise ValueError('Name must contain at least two non-whitespace characters')
        return value.strip()

    @field_validator('scopes')
    @classmethod
    def valid_scopes(cls, value):
        if len(set(value)) != len(value):
            raise ValueError('Scopes must be unique')
        if 'applications:pii' in value and 'applications:read' not in value:
            raise ValueError('applications:pii requires applications:read')
        return value


def key_view(row):
    return {'id': row.id, 'name': row.name, 'scopes': row.scopes, 'revoked': row.revoked,
            'created_at': row.created_at, 'expires_at': row.expires_at}


def issue_key(db, owner, body):
    if owner.role != 'employer' or owner.demo:
        raise HTTPException(403, 'Ключи доступны только обычному работодателю')
    count = db.scalar(select(func.count()).select_from(IntegrationKey).where(
        IntegrationKey.owner_id == owner.id, IntegrationKey.revoked == False, IntegrationKey.expires_at > now()))
    if count >= 20:
        raise HTTPException(409, 'Отзовите неиспользуемые ключи: максимум 20 активных')
    identifier = uid()
    token = 'rhi_' + identifier + '_' + secrets.token_urlsafe(32)
    row = IntegrationKey(id=identifier, owner_id=owner.id, name=body.name.strip(),
                         digest=hashlib.sha256(token.encode()).hexdigest(), scopes=body.scopes,
                         expires_at=now() + timedelta(days=body.expires_in_days))
    db.add(row)
    db.flush()
    return row, token


def install_routes(app, config, db_session, employer, job_view):
    from .main import JobBody, InviteBody

    class JobPut(JobBody):
        model_config = {'extra': 'forbid'}
        active: bool = True

    class ActivePatch(BaseModel):
        model_config = {'extra': 'forbid'}
        active: bool

    @app.post('/api/integration-keys', status_code=201, response_model=KeyIssued, tags=['Integration keys'])
    def create_key(body: KeyCreate, owner=Depends(employer), db=Depends(db_session)):
        serialize_writes(db)
        row, token = issue_key(db, owner, body)
        db.commit()
        return {**key_view(row), 'token': token, 'notice': 'Сохраните в secret storage сервера. Повторно токен не показывается.'}

    @app.get('/api/integration-keys', response_model=list[KeyInfo], tags=['Integration keys'])
    def list_keys(owner=Depends(employer), db=Depends(db_session)):
        return [key_view(row) for row in db.scalars(select(IntegrationKey).where(IntegrationKey.owner_id == owner.id))]

    @app.delete('/api/integration-keys/{key_id}', status_code=204, tags=['Integration keys'])
    def revoke_key(key_id: str, owner=Depends(employer), db=Depends(db_session)):
        serialize_writes(db)
        row = db.get(IntegrationKey, key_id)
        if not row or row.owner_id != owner.id:
            raise HTTPException(404, 'Ключ не найден')
        row.revoked = True
        db.commit()

    security = HTTPBearer(auto_error=False, scheme_name='IntegrationKey',
                          description='Scoped rhi_ key, created by employer/admin. Not a JWT. Server-side only.')

    def authenticate(credentials=Depends(security), db=Depends(db_session)):
        raw = credentials.credentials if credentials and credentials.scheme.lower() == 'bearer' else ''
        match = re.fullmatch(r'rhi_([0-9a-f]{32})_([A-Za-z0-9_-]{43})', raw)
        row = db.get(IntegrationKey, match[1]) if match else None
        if (not row or row.revoked or row.expires_at.replace(tzinfo=timezone.utc) <= now()
                or not hmac.compare_digest(row.digest, hashlib.sha256(raw.encode()).hexdigest())):
            raise HTTPException(401, 'Недействительный или истёкший ключ интеграции')
        owner = db.get(User, row.owner_id)
        if not owner or owner.role != 'employer' or owner.demo:
            raise HTTPException(403, 'Доступ работодателя недоступен')
        return row, owner

    def require(scope):
        def check(identity=Depends(authenticate)):
            if scope not in identity[0].scopes:
                raise HTTPException(403, 'Ключу не разрешено: ' + scope)
            return identity
        return check

    router = APIRouter(prefix='/api/integrations/v1', tags=['HR integration v1'])

    def job_payload(db, row):
        mapping = db.scalar(select(ExternalJob).where(ExternalJob.job_id == row.id))
        return {**job_view(db, row), 'external_id': mapping.external_id if mapping else None,
                'source': mapping.source if mapping else None}

    def application_payload(db, row, key, details=False):
        if row.status == 'withdrawn':
            return {'id': row.id, 'job_id': row.job_id, 'status': 'withdrawn', 'deleted': True}
        if details and 'applications:pii' in key.scopes:
            return {**application_view(db, row), 'deleted': False}
        return {'id': row.id, 'job_id': row.job_id, 'status': row.status, 'created_at': row.created_at,
                'consent_at': row.consent_at, 'answered_questions': len(row.answers),
                'total_questions': len(row.questions), 'deleted': False}

    @router.get('/jobs', response_model=Page[JobInfo])
    def list_jobs(active: bool | None = None, after: str | None = Query(None, pattern=r'^[0-9a-f]{32}$'),
                  limit: int = Query(50, ge=1, le=100), identity=Depends(require('jobs:read')), db=Depends(db_session)):
        query = select(Job).where(Job.owner_id == identity[1].id)
        if active is not None:
            query = query.where(Job.active == active)
        if after:
            query = query.where(Job.id > after)
        rows = list(db.scalars(query.order_by(Job.id).limit(limit + 1)))
        return {'items': [job_payload(db, j) for j in rows[:limit]],
                'next_cursor': rows[limit - 1].id if len(rows) > limit else None}

    @router.get('/jobs/{job_id}', response_model=JobInfo)
    def get_job(job_id: str, identity=Depends(require('jobs:read')), db=Depends(db_session)):
        return job_payload(db, owned_job(db, job_id, identity[1]))

    @router.put('/jobs/by-external/{source}/{external_id}', response_model=JobInfo)
    def put_job(body: JobPut, source: str = Path(pattern=r'^[a-zA-Z0-9_-]{1,40}$'),
                external_id: str = Path(pattern=r'^[a-zA-Z0-9_.-]{1,120}$'),
                identity=Depends(require('jobs:write')), db=Depends(db_session)):
        serialize_writes(db)
        owner = identity[1]
        mapping = db.scalar(select(ExternalJob).where(ExternalJob.owner_id == owner.id,
                            ExternalJob.source == source, ExternalJob.external_id == external_id))
        values = body.model_dump()
        if mapping:
            row = owned_job(db, mapping.job_id, owner)
            changes = {key: value for key, value in values.items() if getattr(row, key) != value}
            if set(changes) - {'active'} and db.scalar(select(Application.id).where(Application.job_id == row.id).limit(1)):
                raise HTTPException(409, 'У вакансии есть отклики: создайте новую версию с новым external_id. Можно менять только active.')
            for key, value in changes.items():
                setattr(row, key, value)
        else:
            row = Job(owner_id=owner.id, company=owner.company, **values)
            db.add(row)
            db.flush()
            db.add(ExternalJob(owner_id=owner.id, job_id=row.id, source=source, external_id=external_id))
        db.commit()
        return job_payload(db, row)

    @router.patch('/jobs/{job_id}', response_model=JobInfo)
    def change_active(job_id: str, body: ActivePatch, identity=Depends(require('jobs:write')), db=Depends(db_session)):
        serialize_writes(db)
        row = owned_job(db, job_id, identity[1])
        row.active = body.active
        db.commit()
        return job_payload(db, row)

    @router.get('/applications', response_model=Page[AppSummary | Tombstone])
    def list_applications(job_id: str | None = None,
                          status: Literal['clarifying', 'ready', 'invited', 'confirmed', 'withdrawn'] | None = None,
                          after: str | None = Query(None, pattern=r'^[0-9a-f]{32}$'),
                          limit: int = Query(50, ge=1, le=100),
                          identity=Depends(require('applications:read')), db=Depends(db_session)):
        query = select(Application).join(Job).where(Job.owner_id == identity[1].id)
        if job_id:
            owned_job(db, job_id, identity[1])
            query = query.where(Application.job_id == job_id)
        if status:
            query = query.where(Application.status == status)
        if after:
            query = query.where(Application.id > after)
        rows = list(db.scalars(query.order_by(Application.id).limit(limit + 1)))
        return {'items': [application_payload(db, a, identity[0]) for a in rows[:limit]],
                'next_cursor': rows[limit - 1].id if len(rows) > limit else None}

    def owned_application(db, app_id, owner):
        row = db.scalar(select(Application).join(Job).where(Application.id == app_id, Job.owner_id == owner.id))
        if not row:
            raise HTTPException(404, 'Отклик не найден')
        return row

    @router.get('/applications/{app_id}', response_model=AppPrivate | AppSummary | Tombstone)
    def get_application(app_id: str, identity=Depends(require('applications:read')), db=Depends(db_session)):
        row = owned_application(db, app_id, identity[1])
        return application_payload(db, row, identity[0], details=True)

    @router.post('/applications/{app_id}/invite', response_model=InvitationResult)
    def send_invitation(app_id: str, body: InviteBody, identity=Depends(require('invitations:write')), db=Depends(db_session)):
        serialize_writes(db)
        row = owned_application(db, app_id, identity[1])
        invite(db, row, body.message)
        db.commit()
        return {'id': row.id, 'status': row.status, 'delivery': 'queued' if db.get(User, row.user_id).max_id else 'no_max_link'}

    @router.get('/events', response_model=EventPage)
    def events(after: str = Query('0', max_length=19, pattern=r'^(0|[1-9][0-9]*)$'),
               limit: int = Query(100, ge=1, le=200), identity=Depends(require('events:read')), db=Depends(db_session)):
        cursor = int(after)
        if cursor > 2**63 - 1:
            raise HTTPException(422, 'Cursor exceeds signed 64-bit range')
        rows = list(db.scalars(select(IntegrationEvent).where(IntegrationEvent.owner_id == identity[1].id,
                          IntegrationEvent.seq > cursor).order_by(IntegrationEvent.seq).limit(limit + 1)))
        items = rows[:limit]
        return {'items': [{'seq': e.seq, 'type': e.kind, 'resource_id': e.resource_id, 'at': e.created_at} for e in items],
                'next_cursor': str(items[-1].seq) if items else after, 'has_more': len(rows) > limit}

    @router.get('/metrics', response_model=Metrics)
    def metrics(identity=Depends(require('metrics:read')), db=Depends(db_session)):
        counts = dict(db.execute(select(Application.status, func.count()).join(Job).where(
            Job.owner_id == identity[1].id).group_by(Application.status)).all())
        return {'active_jobs': db.scalar(select(func.count()).select_from(Job).where(Job.owner_id == identity[1].id, Job.active == True)),
                'applications': sum(n for status, n in counts.items() if status != 'withdrawn'), 'statuses': counts,
                'notice': 'Фактические статусы, не рейтинг кандидатов'}

    app.include_router(router)
