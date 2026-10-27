FROM python:3.12-slim

ARG WITH_PLAYWRIGHT=false
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends libpq5 curl && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install -r requirements.txt \
 && if [ "$WITH_PLAYWRIGHT" = "true" ]; then python -m playwright install --with-deps chromium; fi

COPY mip ./mip
COPY migrations ./migrations
COPY alembic.ini ./
COPY tests/fixtures/replay ./tests/fixtures/replay

RUN useradd -m -u 10001 mip && mkdir -p /app/var && chown -R mip /app
USER mip
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD curl -fs http://localhost:8000/api/health || exit 1
CMD ["sh", "-c", "alembic upgrade head && python -m mip.cli seed-sources && uvicorn mip.api.main:app --host 0.0.0.0 --port 8000 --proxy-headers"]
