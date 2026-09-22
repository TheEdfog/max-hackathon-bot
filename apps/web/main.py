from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, status
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from apps.api.db.database import init_db
from apps.web.routes import auth, dashboard, profiles_dynamic as profiles, results, vacancies
from core.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.ensure_runtime_directories()
    init_db()
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        debug=settings.debug,
        lifespan=lifespan,
    )

    templates = Jinja2Templates(directory=str(settings.templates_path))
    templates.env.globals["app_name"] = settings.app_name
    app.state.templates = templates

    app.mount("/static", StaticFiles(directory=str(settings.static_path)), name="static")

    app.include_router(auth.router)
    app.include_router(dashboard.router)
    app.include_router(profiles.router)
    app.include_router(vacancies.router)
    app.include_router(results.router)

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)

    @app.get("/health", tags=["system"])
    async def health() -> dict:
        return {
            "status": "ok",
            "app_name": settings.app_name,
            "environment": settings.app_env,
        }

    return app


app = create_app()
