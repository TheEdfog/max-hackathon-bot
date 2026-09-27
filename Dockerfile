FROM python:3.13.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements-hiring.lock ./
RUN pip install --no-cache-dir -r requirements-hiring.lock && useradd --uid 10001 --create-home bot
COPY core/utils.py ./core/utils.py
COPY apps/web/services/vacancy_parser_service.py ./apps/web/services/vacancy_parser_service.py
COPY hiring ./hiring
COPY scripts/smoke_hiring.py ./scripts/smoke_hiring.py
COPY scripts/smoke_llm.py ./scripts/smoke_llm.py
RUN mkdir -p /app/data && chown -R bot:bot /app/data
USER bot

FROM runtime AS polling
CMD ["python", "-m", "hiring.polling"]

FROM runtime AS api
EXPOSE 8000
CMD ["uvicorn", "hiring.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
