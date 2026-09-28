import hmac
import secrets
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path

import jwt
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.security import HTTPBearer
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from .bot import process_event, start_worker
from .api_models import (ActiveBody, AnswersBody, ApplicationBody, EmployerBody, InviteBody, JobBody, Login,
                         MaxBody, Registration, TextBody)
from .config import Config
from .db import Application, Audit, Job, Outbox, User, connect, serialize_writes
from .matching import extract
from .security import check_password, hash_password, max_identity, token_for
from .services import answer, application_view, confirm, invite, owned_job, submit, withdraw_application


def create_app(config=None):
    config = config or Config()
    config.validate()
    if config.sandbox:
        raise ValueError('Sandbox personas are supported only by the local polling launcher, not the public API')
    if config.database_url.startswith("sqlite:///data/"):
        Path("data").mkdir(exist_ok=True)
    engine, factory = connect(config.database_url)

    @asynccontextmanager
    async def lifespan(app):
        worker = start_worker(factory, config) if config.worker else None
        yield
        if worker:
            worker[0].set()
            worker[1].join(timeout=15)
        engine.dispose()

    app = FastAPI(title="РезюмИТ Найм", version="1.7.0", lifespan=lifespan)
    app.state.factory, app.state.config = factory, config
    buckets = defaultdict(deque)

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request, exc):
        # SQL exceptions can contain parameters, including private texts. Never echo them.
        return JSONResponse({'detail': 'Хранилище временно недоступно. Повторите действие.'}, 503)

    @app.middleware("http")
    async def guards(request, call_next):
        try:
            length = int(request.headers.get("content-length", "0"))
        except ValueError:
            return JSONResponse({"detail": "Некорректный размер запроса"}, 400)
        if length > 6 * 1024 * 1024:
            return JSONResponse({"detail": "Максимальный размер файла - 5 МБ"}, 413)
        if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            limit = 128 * 1024 if request.url.path == '/api/max/webhook' else 6 * 1024 * 1024
            chunks, received = [], 0
            async for chunk in request.stream():
                received += len(chunk)
                if received > limit:
                    return JSONResponse({'detail': 'Слишком большой запрос'}, 413)
                chunks.append(chunk)
            request._body = b''.join(chunks)  # Starlette cached request reused by call_next.
        integration = request.url.path.startswith('/api/integrations/') or request.url.path.startswith('/api/integration-keys')
        if request.url.path.startswith("/api/auth") or request.url.path == '/api/me/employer' or integration:
            key = (request.client.host if request.client else "unknown", 'integration' if integration else 'auth')
            if len(buckets) > 10000:
                buckets.clear()
            q = buckets[key]
            while q and q[0] < time.monotonic() - 60:
                q.popleft()
            if len(q) >= (120 if integration else 30):
                return JSONResponse({"detail": "Слишком много попыток. Попробуйте через минуту."}, 429, headers={'Retry-After': '60'})
            q.append(time.monotonic())
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def db_session():
        with factory() as db:
            yield db

    bearer = HTTPBearer(auto_error=False)

    def current(request: Request, credentials=Depends(bearer), db=Depends(db_session)):
        try:
            raw = request.headers.get("authorization", "")
            if not raw.startswith("Bearer "):
                raise ValueError()
            payload = jwt.decode(raw[7:], config.secret, algorithms=["HS256"], audience="rezumit-hiring")
            user = db.get(User, payload["sub"])
            if not user:
                raise ValueError()
            return user
        except (jwt.PyJWTError, KeyError, ValueError):
            raise HTTPException(401, "Войдите в аккаунт")

    def employer(user=Depends(current)):
        if user.role != "employer":
            raise HTTPException(403, "Раздел доступен работодателю")
        return user

    def user_view(user):
        return {"id": user.id, "name": user.name, "role": user.role, "company": user.company, "demo": user.demo, "max_connected": bool(user.max_id)}

    def auth_response(user):
        return {"token": token_for(user, config.secret), "user": user_view(user)}

    def job_view(db, job):
        count = db.scalar(select(func.count()).select_from(Application).where(Application.job_id == job.id, Application.status != "withdrawn"))
        return {"id": job.id, "title": job.title, "company": job.company, "description": job.description,
                "terms": job.terms, "requirements": job.requirements, "screening_questions": job.screening_questions,
                "active": job.active, "applications": count,
                "created_at": job.created_at.isoformat(), "apply_url": config.public_url + "/apply/" + job.id,
                "max_url": f"https://max.ru/{config.bot_name}?start=apply_{job.id}" if config.bot_name else None}

    @app.get("/health")
    def health(db=Depends(db_session)):
        db.execute(text("SELECT 1"))
        return {"status": "ok", "version": app.version}

    @app.get("/api/config")
    def public_config():
        return {"demo": config.demo, "max_bot": config.bot_name, "employer_code_required": bool(config.employer_code), "engine": "rules", "privacy_version": "2026-09-22"}

    @app.post("/api/auth/register", status_code=201)
    def register(body: Registration, db=Depends(db_session)):
        if body.role == "employer" and config.employer_code and not hmac.compare_digest(body.code.encode(), config.employer_code.encode()):
            raise HTTPException(403, "Нужен код доступа работодателя")
        if body.role == "employer" and not body.company.strip():
            raise HTTPException(422, "Укажите название компании")
        user = User(email=str(body.email).lower(), name=body.name.strip(), company=body.company.strip(), role=body.role, password=hash_password(body.password))
        db.add(user)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, "Этот email уже зарегистрирован")
        return auth_response(user)

    @app.post("/api/auth/login")
    def login(body: Login, db=Depends(db_session)):
        user = db.scalar(select(User).where(User.email == str(body.email).lower()))
        if not user or not check_password(body.password, user.password):
            raise HTTPException(401, "Неверный email или пароль")
        return auth_response(user)

    @app.post("/api/auth/max")
    def max_login(body: MaxBody, db=Depends(db_session)):
        try:
            max_id, name = max_identity(body.init_data, config.bot_token)
        except (ValueError, KeyError, TypeError):
            raise HTTPException(401, "Не удалось подтвердить вход через MAX. Откройте приложение заново.")
        user = db.scalar(select(User).where(User.max_id == max_id))
        if not user:
            user = User(max_id=max_id, name=name[:160], role="candidate")
            db.add(user)
            db.commit()
        return auth_response(user)

    @app.post("/api/me/link-max")
    def link_max(body: MaxBody, user=Depends(current), db=Depends(db_session)):
        if user.demo:
            raise HTTPException(403, "Связь с MAX недоступна в демо")
        try:
            max_id, _ = max_identity(body.init_data, config.bot_token)
        except (ValueError, KeyError, TypeError):
            raise HTTPException(401, "Откройте приложение внутри MAX")
        other = db.scalar(select(User).where(User.max_id == max_id, User.id != user.id))
        if other:
            raise HTTPException(409, "MAX уже связан с другим аккаунтом. Используйте вход через MAX.")
        user.max_id = max_id
        db.commit()
        return user_view(user)

    @app.get("/api/me")
    def me(user=Depends(current)):
        return user_view(user)

    @app.post("/api/me/employer")
    def enable_employer(body: EmployerBody, user=Depends(current), db=Depends(db_session)):
        serialize_writes(db)
        if config.employer_code and not hmac.compare_digest(body.code.encode(), config.employer_code.encode()):
            raise HTTPException(403, "Неверный код работодателя")
        if db.scalar(select(Application).where(Application.user_id == user.id)):
            raise HTTPException(409, "У вас уже есть отклики кандидата. Используйте отдельный аккаунт работодателя.")
        user.role, user.company = "employer", body.company
        db.commit()
        return user_view(user)

    @app.post("/api/requirements/extract")
    def extract_requirements(body: TextBody, user=Depends(employer)):
        return {"requirements": extract(body.text), "engine": "rules", "notice": "Проверьте и подтвердите требования перед публикацией"}

    @app.get("/api/jobs")
    def jobs(user=Depends(employer), db=Depends(db_session)):
        return [job_view(db, j) for j in db.scalars(select(Job).where(Job.owner_id == user.id).order_by(Job.created_at.desc()))]

    @app.post("/api/jobs", status_code=201)
    def create_job(body: JobBody, user=Depends(employer), db=Depends(db_session)):
        job = Job(owner_id=user.id, title=body.title, company=user.company, description=body.description, terms=body.terms,
                  requirements=[r.model_dump() for r in body.requirements], screening_questions=body.screening_questions)
        db.add(job)
        db.commit()
        return job_view(db, job)

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str, user=Depends(employer), db=Depends(db_session)):
        return job_view(db, owned_job(db, job_id, user))

    @app.patch("/api/jobs/{job_id}")
    def set_active(job_id: str, body: ActiveBody, user=Depends(employer), db=Depends(db_session)):
        serialize_writes(db)
        job = owned_job(db, job_id, user)
        job.active = body.active
        db.commit()
        return job_view(db, job)

    @app.get("/api/public/jobs/{job_id}")
    def public_job(job_id: str, db=Depends(db_session)):
        job = db.get(Job, job_id)
        if not job:
            raise HTTPException(404, "Вакансия не найдена")
        result = job_view(db, job)
        result.pop("applications")
        return result

    @app.post("/api/resume/extract")
    def pdf_extract(file: UploadFile = File(...), user=Depends(current)):
        raw = file.file.read(5 * 1024 * 1024 + 1)
        if len(raw) > 5 * 1024 * 1024:
            raise HTTPException(413, "Максимальный размер PDF - 5 МБ")
        if not raw.startswith(b"%PDF"):
            raise HTTPException(422, "Загрузите PDF с текстовым слоем")
        try:
            from .pdf_extract import extract_pdf
            result = extract_pdf(raw)
        except ValueError:
            raise HTTPException(422, "Не удалось прочитать текст. Вставьте его вручную; сканы и защищённые PDF не поддерживаются.")
        return {"text": result}

    @app.post('/api/jobs/{job_id}/apply-pdf', status_code=201)
    def apply_pdf(job_id: str, file: UploadFile = File(...), consent: bool = Form(...),
                  name: str = Form(min_length=2, max_length=160), user=Depends(current), db=Depends(db_session)):
        from .talent_api import read_pdf, save_pdf
        if consent is not True or not 2 <= len(name.strip()) <= 160:
            raise HTTPException(422, 'Нужно согласие кандидата и имя от 2 до 160 символов')
        raw = file.file.read(5 * 1024 * 1024 + 1)
        resume = read_pdf(raw)
        serialize_writes(db)
        job = db.get(Job, job_id)
        if not job:
            raise HTTPException(404, 'Вакансия не найдена')
        previous = db.scalar(select(Application).where(Application.job_id == job.id, Application.user_id == user.id))
        row = submit(db, user, job, resume)
        if not previous:
            user.name = name.strip()
            save_pdf(db, row, raw)
        db.commit()
        return application_view(db, row)

    @app.post("/api/jobs/{job_id}/apply", status_code=201)
    def apply(job_id: str, body: ApplicationBody, user=Depends(current), db=Depends(db_session)):
        serialize_writes(db)
        job = db.get(Job, job_id)
        if not job:
            raise HTTPException(404, "Вакансия не найдена")
        user.name = body.name.strip()
        try:
            row = submit(db, user, job, body.resume)
            db.commit()
        except IntegrityError:
            db.rollback()
            row = db.scalar(select(Application).where(Application.job_id == job.id, Application.user_id == user.id))
            if not row:
                raise HTTPException(409, "Повторите отправку отклика")
        return application_view(db, row)

    @app.get("/api/jobs/{job_id}/applications")
    def job_applications(job_id: str, user=Depends(employer), db=Depends(db_session)):
        owned_job(db, job_id, user)
        return [application_view(db, a) for a in db.scalars(select(Application).where(Application.job_id == job_id, Application.status != "withdrawn").order_by(Application.created_at))]

    @app.get("/api/applications")
    def my_applications(user=Depends(current), db=Depends(db_session)):
        return [application_view(db, a) for a in db.scalars(select(Application).where(Application.user_id == user.id).order_by(Application.created_at.desc()))]

    def own_application(db, app_id, user):
        row = db.get(Application, app_id)
        if not row or row.user_id != user.id:
            raise HTTPException(404, "Отклик не найден")
        return row

    @app.post("/api/applications/{app_id}/answers")
    def save_answers(app_id: str, body: AnswersBody, user=Depends(current), db=Depends(db_session)):
        serialize_writes(db)
        row = answer(db, own_application(db, app_id, user), body.answers)
        db.commit()
        return application_view(db, row)

    @app.post("/api/applications/{app_id}/invite")
    def invite_candidate(app_id: str, body: InviteBody, user=Depends(employer), db=Depends(db_session)):
        serialize_writes(db)
        row = db.get(Application, app_id)
        if not row:
            raise HTTPException(404, "Отклик не найден")
        owned_job(db, row.job_id, user)
        invite(db, row, body.message)
        db.commit()
        return application_view(db, row)

    @app.post("/api/applications/{app_id}/confirm")
    def confirm_invitation(app_id: str, user=Depends(current), db=Depends(db_session)):
        serialize_writes(db)
        row = confirm(db, own_application(db, app_id, user))
        db.commit()
        return application_view(db, row)

    @app.delete("/api/applications/{app_id}", status_code=204)
    def withdraw(app_id: str, user=Depends(current), db=Depends(db_session)):
        serialize_writes(db)
        row = own_application(db, app_id, user)
        withdraw_application(db, row)
        db.commit()
        return Response(status_code=204)

    @app.get("/api/metrics")
    def metrics(user=Depends(employer), db=Depends(db_session)):
        rows = list(db.scalars(select(Application).join(Job).where(Job.owner_id == user.id, Application.status != "withdrawn")))
        return {"applications": len(rows), "ready": sum(a.status == "ready" for a in rows), "invited": sum(a.status in ("invited", "confirmed") for a in rows), "confirmed": sum(a.status == "confirmed" for a in rows), "answered_questions": sum(len(a.answers) for a in rows), "demo": user.demo}

    @app.post("/api/max/webhook")
    async def webhook(request: Request):
        if not config.webhook_secret or not hmac.compare_digest(request.headers.get("x-max-bot-api-secret", "").encode(), config.webhook_secret.encode()):
            raise HTTPException(403, "Недействительная подпись webhook")
        try:
            data = await request.json()
            if not isinstance(data, dict):
                raise ValueError()
        except ValueError:
            raise HTTPException(400, "Ожидается JSON-объект")
        from starlette.concurrency import run_in_threadpool
        await run_in_threadpool(process_event, factory, data, config)
        return {"ok": True}

    from .integrations import install_routes
    install_routes(app, config, db_session, employer, job_view)

    @app.get('/', include_in_schema=False)
    def index():
        return {'service': app.title, 'channel': 'MAX', 'docs': '/docs',
                'bot_url': f'https://max.ru/{config.bot_name}' if config.bot_name else None}

    @app.get('/apply/{job_id}', include_in_schema=False)
    def open_vacancy(job_id: str, db=Depends(db_session)):
        job = db.get(Job, job_id)
        if not job or not job.active:
            raise HTTPException(404, 'Вакансия не найдена или закрыта')
        if not config.bot_name:
            raise HTTPException(503, 'Ссылка MAX ещё не настроена')
        return RedirectResponse(f'https://max.ru/{config.bot_name}?start=apply_{job.id}', status_code=307)

    return app


app = create_app()
