FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_SYSTEM_PYTHON=1 \
    PYTHONPATH=/app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates libpq5 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /usr/sbin/nologin appuser

WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:0.9.17 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
RUN uv pip install --system --no-cache .

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini
COPY content ./content
COPY middle-math-70-exam.pdf middle-math-70-solutions.pdf ./
COPY scripts ./scripts

RUN chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD ["sh", "-c", "alembic upgrade head && mm70 import-bundle ${MM70_AUTO_IMPORT_BUNDLE:-content/bundles/math70-v2.json} || exit 1; uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000"]
