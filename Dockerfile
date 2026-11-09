FROM python:3.12.14-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src ./src

RUN pip install --no-cache-dir uv==0.12.5 \
    && uv sync --frozen --no-dev \
    && mkdir -p /data/files /data/models \
    && chown -R 10001:10001 /app /data

USER 10001:10001
EXPOSE 8000

CMD ["/app/.venv/bin/uvicorn", "filemind.main:app", "--host", "0.0.0.0", "--port", "8000"]
