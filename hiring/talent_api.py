"""HR tools layered on the same tenant ownership and scoped-key boundary."""
import hashlib
from datetime import datetime
from typing import Literal
from fastapi import Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from .assessments import TestBody, attach_test, create_test, owned_test, test_view
from .db import AssessmentTemplate, ResumeDocument, serialize_writes
from .pdf_extract import MAX_BYTES, extract_pdf
from .services import owned_job
from .teams import company_member_ids

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


    @router.post('/tests', status_code=201, response_model=TestInfo)
    def add_test(body: TestBody, identity=Depends(require('tests:write')), db=Depends(db_session)):
        serialize_writes(db)
        row = create_test(db, identity[1], body)
        db.commit()
        return test_view(row)

    @router.get('/tests', response_model=TestPage)
    def tests(after: str | None = Query(None, pattern=r'^[0-9a-f]{32}$'), limit: int = Query(50, ge=1, le=100),
              identity=Depends(require('tests:read')), db=Depends(db_session)):
        query = select(AssessmentTemplate).where(
            AssessmentTemplate.owner_id.in_(company_member_ids(db, identity[1])))
        if after:
            query = query.where(AssessmentTemplate.id > after)
        rows = list(db.scalars(query.order_by(AssessmentTemplate.id).limit(limit + 1)))
        return {'items': [test_view(row) for row in rows[:limit]],
                'next_cursor': rows[limit - 1].id if len(rows) > limit else None}


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
