ARG PYTHON_BASE_IMAGE=python:3.12-slim
FROM ${PYTHON_BASE_IMAGE} AS runtime

ARG PIP_INDEX_URL=https://pypi.org/simple

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY knowledge ./knowledge
COPY eval ./eval
COPY migrations ./migrations
COPY alembic.ini ./

RUN python -m pip install \
    --disable-pip-version-check \
    --retries 5 \
    --timeout 120 \
    --index-url "${PIP_INDEX_URL}" \
    .

RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
CMD ["uvicorn", "agentic_rag.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
