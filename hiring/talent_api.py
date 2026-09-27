"""HR tools layered on the same tenant ownership and scoped-key boundary."""
import hashlib
from datetime import datetime
from typing import Literal
from fastapi import Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from .assessments import TestBody, attach_test, create_test, owned_test, test_view
from .ai_questions import AiDraftBody, AiDraftResult, create_draft
from .db import ApplicationReview, AssessmentTemplate, IntegrationEvent, ResumeDocument, serialize_writes
from .matching import evidence, extract
from .pdf_extract import MAX_BYTES, extract_pdf
from .services import owned_job

ReviewStage = Literal['new', 'reviewing', 'shortlisted', 'on_hold', 'rejected', 'hired']


class DraftBody(BaseModel):
    model_config = {'extra': 'forbid'}
    resume: str = Field(min_length=40, max_length=20000)
    title: str = Field(default='Специалист', min_length=3, max_length=160)


class DraftResult(BaseModel):
    title: str
    description: str
    requirements: list[dict]
    terms: str
    review_required: Literal[True]
    published: Literal[False]
    notice: str


class ParsedDocument(BaseModel):
    text: str
    stored: Literal[False]


class TestInfo(TestBody):
    id: str
    version: int
    created_at: datetime


class TestPage(BaseModel):
    items: list[TestInfo]
    next_cursor: str | None


class JobAssessment(BaseModel):
    job_id: str
    questions: list[dict]


class ReviewInfo(BaseModel):
    stage: str
    note: str
    tags: list[str]
    version: int
    notice: str = 'Внутренний этап. Кандидату уведомление не отправлялось.'


class SourceBody(BaseModel):
    model_config = {'extra': 'forbid'}
    url: str = Field(min_length=10, max_length=2048)


class SourcePreview(BaseModel):
    text: str
    provider: str
    warning: str
    stored: Literal[False]


class GithubReviewInfo(BaseModel):
    status: Literal['not_requested', 'pending', 'working', 'ready', 'failed']
    links: list[str]
    report: dict | None
    error: str
    expires_at: datetime | None
    notice: str


class CompatibilityInfo(BaseModel):
    method: str
    total_pct: float | None
    must_pct: float | None
    nice_pct: float | None
    must_count: int
    nice_count: int
    requirements: list[dict]
    notice: str
    formula: str
    limitations: list[str]


def vacancy_draft(body):
    # Never copy contact details, identity or original source snippets to a vacancy.
    rows = evidence(body.resume, {}, extract(body.resume))['requirements']
    requirements = [{**r, 'source': ''} for r in rows if r['state'] == 'mentioned']
    requirements = [{k: r[k] for k in ('id', 'skill', 'label', 'type', 'source')} for r in requirements]
    return {'title': body.title, 'description': 'Профессиональные навыки для обсуждения: ' +
            (', '.join(r['label'] for r in requirements) or 'добавьте вручную') +
            '. Работодатель должен описать задачи, уровень и условия.',
            'requirements': requirements, 'terms': '', 'review_required': True, 'published': False,
            'notice': 'Черновик по упомянутым навыкам, не готовый профиль должности. Проверьте требования вручную.'}


class ReviewBody(BaseModel):
    model_config = {'extra': 'forbid'}
    expected_version: int = Field(ge=0)
    stage: ReviewStage
    note: str = Field(default='', max_length=3000)
    tags: list[str] = Field(default_factory=list, max_length=10)

    @field_validator('tags')
    @classmethod
    def valid_tags(cls, value):
        values = [v.strip() for v in value]
        if any(not 1 <= len(v) <= 40 for v in values) or len(set(values)) != len(values):
            raise ValueError('Уникальные метки: от 1 до 40 символов')
        return values


class TestUpdate(TestBody):
    expected_version: int = Field(ge=1)


class TestAttachment(BaseModel):
    model_config = {'extra': 'forbid'}
    template_id: str | None = Field(default=None, pattern=r'^[0-9a-f]{32}$')
    expected_version: int | None = Field(default=None, ge=1)


def read_pdf(raw):
    if len(raw) > MAX_BYTES:
        raise HTTPException(413, 'Максимальный размер PDF - 5 МБ')
    try:
        return extract_pdf(raw)
    except ValueError:
        raise HTTPException(422, 'Нужен текстовый PDF до 10 страниц и 20 000 символов. Сканы и защищённые PDF не поддерживаются.')


def save_pdf(db, application, raw, provider='upload'):
    if not db.get(ResumeDocument, application.id):
        db.add(ResumeDocument(application_id=application.id, data=raw,
                             sha256=hashlib.sha256(raw).hexdigest(), provider=provider))


def install_talent_routes(router, require, db_session, owned_application):
    def private_application(db, app_id, identity):
        if 'applications:pii' not in identity[0].scopes:
            raise HTTPException(403, 'Ключу не разрешено: applications:pii')
        row = owned_application(db, app_id, identity[1])
        if row.status == 'withdrawn':
            raise HTTPException(410, 'Отклик отозван, персональные данные удалены')
        return row

    @router.get('/applications/{app_id}/compatibility', response_model=CompatibilityInfo)
    def match(app_id: str, identity=Depends(require('applications:read')), db=Depends(db_session)):
        from .compatibility import compatibility
        from .db import Job
        row = private_application(db, app_id, identity)
        return compatibility(row.resume, row.answers, db.get(Job, row.job_id).requirements)

    @router.get('/applications/{app_id}/github', response_model=GithubReviewInfo)
    def github_get(app_id: str, identity=Depends(require('applications:read')), db=Depends(db_session)):
        from .github_review import review_view
        return review_view(db, private_application(db, app_id, identity))

    @router.post('/applications/{app_id}/github', response_model=GithubReviewInfo, status_code=202)
    def github_request(app_id: str, body: SourceBody, identity=Depends(require('applications:write')), db=Depends(db_session)):
        from .github_review import request_review
        serialize_writes(db)
        result = request_review(db, private_application(db, app_id, identity), identity[1], body.url)
        db.commit()
        return result

    @router.post('/jobs/draft-from-resume', response_model=DraftResult)
    def draft(body: DraftBody, identity=Depends(require('jobs:write'))):
        return vacancy_draft(body)

    @router.post('/sources/preview', response_model=SourcePreview)
    def preview(body: SourceBody, identity=Depends(require('jobs:write'))):
        from .sources import import_source, SourceError
        try:
            result = import_source(body.url)
            return {'text': result.text, 'provider': result.provider, 'warning': result.warning, 'stored': False}
        except SourceError as exc:
            raise HTTPException(422, str(exc))

    @router.post('/documents/parse', response_model=ParsedDocument)
    def parse(file: UploadFile = File(...), identity=Depends(require('jobs:write'))):
        return {'text': read_pdf(file.file.read(MAX_BYTES + 1)), 'stored': False}

    @router.get('/applications/{app_id}/resume.txt', response_class=Response,
                responses={200: {'content': {'text/plain': {}}, 'description': 'Candidate-provided text'}})
    def resume_text(app_id: str, identity=Depends(require('applications:read')), db=Depends(db_session)):
        row = private_application(db, app_id, identity)
        return Response(row.resume, media_type='text/plain; charset=utf-8',
                        headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                                 'Content-Disposition': f'attachment; filename="resume-{row.id}.txt"'})

    @router.get('/applications/{app_id}/resume.pdf', response_class=Response,
                responses={200: {'content': {'application/pdf': {}}, 'description': 'Original uploaded PDF'},
                           404: {'description': 'Original PDF was not provided'}, 410: {'description': 'Withdrawn'}})
    def resume_pdf(app_id: str, identity=Depends(require('applications:read')), db=Depends(db_session)):
        row = private_application(db, app_id, identity)
        doc = db.get(ResumeDocument, row.id)
        if not doc:
            raise HTTPException(404, 'Кандидат не прикреплял PDF. Используйте resume.txt.')
        return Response(doc.data, media_type='application/pdf', headers={
            'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', 'Content-Security-Policy': 'sandbox',
            'X-Content-SHA256': doc.sha256, 'Content-Disposition': f'attachment; filename="resume-{row.id}.pdf"'})

    @router.get('/applications/{app_id}/review', response_model=ReviewInfo)
    def review(app_id: str, identity=Depends(require('applications:read')), db=Depends(db_session)):
        row = private_application(db, app_id, identity)
        value = db.get(ApplicationReview, row.id)
        return {'stage': value.stage if value else 'new', 'note': value.note if value else '',
                'tags': value.tags if value else [], 'version': value.version if value else 0}

    @router.patch('/applications/{app_id}/review', response_model=ReviewInfo)
    def change_review(app_id: str, body: ReviewBody, identity=Depends(require('applications:write')), db=Depends(db_session)):
        serialize_writes(db)
        row = private_application(db, app_id, identity)
        value = db.get(ApplicationReview, row.id)
        if body.expected_version != (value.version if value else 0):
            raise HTTPException(409, 'Карточка уже изменена. Прочитайте актуальную версию.')
        if not value:
            value = ApplicationReview(application_id=row.id, version=0)
            db.add(value)
        value.stage, value.note, value.tags = body.stage, body.note.strip(), body.tags
        value.version += 1
        db.add(IntegrationEvent(owner_id=identity[1].id, kind='application.reviewed', resource_id=row.id))
        db.commit()
        return {'stage': value.stage, 'note': value.note, 'tags': value.tags, 'version': value.version,
                'notice': 'Внутренний этап. Кандидату уведомление не отправлялось.'}

    @router.post('/tests', status_code=201, response_model=TestInfo)
    def add_test(body: TestBody, identity=Depends(require('tests:write')), db=Depends(db_session)):
        serialize_writes(db)
        row = create_test(db, identity[1], body)
        db.commit()
        return test_view(row)

    @router.get('/tests', response_model=TestPage)
    def tests(after: str | None = Query(None, pattern=r'^[0-9a-f]{32}$'), limit: int = Query(50, ge=1, le=100),
              identity=Depends(require('tests:read')), db=Depends(db_session)):
        query = select(AssessmentTemplate).where(AssessmentTemplate.owner_id == identity[1].id)
        if after:
            query = query.where(AssessmentTemplate.id > after)
        rows = list(db.scalars(query.order_by(AssessmentTemplate.id).limit(limit + 1)))
        return {'items': [test_view(row) for row in rows[:limit]],
                'next_cursor': rows[limit - 1].id if len(rows) > limit else None}

    @router.get('/tests/ai-skills', response_model=list[str])
    def ai_skills(identity=Depends(require('tests:write'))):
        from .matching import ALIASES
        return sorted(ALIASES)

    @router.post('/tests/ai-draft', response_model=AiDraftResult)
    def ai_draft(body: AiDraftBody, request: Request, identity=Depends(require('tests:write')), db=Depends(db_session)):
        return create_draft(db, identity[1].id, body, request.app.state.config)

    @router.get('/tests/{test_id}', response_model=TestInfo)
    def get_test(test_id: str, identity=Depends(require('tests:read')), db=Depends(db_session)):
        return test_view(owned_test(db, test_id, identity[1]))

    @router.put('/tests/{test_id}', response_model=TestInfo)
    def update_test(test_id: str, body: TestUpdate, identity=Depends(require('tests:write')), db=Depends(db_session)):
        serialize_writes(db)
        row = owned_test(db, test_id, identity[1])
        if row.version != body.expected_version:
            raise HTTPException(409, 'Шаблон уже изменён. Прочитайте актуальную версию.')
        row.title, row.questions, row.active = body.title, [q.model_dump() for q in body.questions], body.active
        row.version += 1
        db.commit()
        return test_view(row)

    @router.put('/jobs/{job_id}/assessment', response_model=JobAssessment)
    def attach(job_id: str, body: TestAttachment, identity=Depends(require('jobs:write')), db=Depends(db_session)):
        serialize_writes(db)
        job = owned_job(db, job_id, identity[1])
        row = owned_test(db, body.template_id, identity[1]) if body.template_id else None
        if row and body.expected_version != row.version:
            raise HTTPException(409, 'Укажите актуальную expected_version шаблона')
        attach_test(db, job, row)
        db.commit()
        return {'job_id': job.id, 'questions': job.test_questions}

    @router.get('/jobs/{job_id}/assessment', response_model=JobAssessment)
    def assessment(job_id: str, identity=Depends(require('jobs:read')), db=Depends(db_session)):
        return {'job_id': job_id, 'questions': owned_job(db, job_id, identity[1]).test_questions}
