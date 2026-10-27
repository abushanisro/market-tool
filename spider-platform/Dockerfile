FROM golang:1.27-alpine AS gobuild
WORKDIR /src
COPY go-fetcher/go.mod go-fetcher/go.sum ./
RUN go mod download
COPY go-fetcher ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags "-s -w" -o /out/emithran-fetch ./cmd/emithran-fetch

FROM python:3.12-slim

ARG WITH_PLAYWRIGHT=false
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends libpq5 curl && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install -r requirements.txt \
 && if [ "$WITH_PLAYWRIGHT" = "true" ]; then python -m playwright install --with-deps chromium; fi

COPY --from=gobuild /out/emithran-fetch /usr/local/bin/emithran-fetch
ENV EMITHRAN_GO_FETCHER_BIN=/usr/local/bin/emithran-fetch

COPY emithran ./emithran
COPY migrations ./migrations
COPY alembic.ini ./
COPY tests/fixtures/replay ./tests/fixtures/replay

RUN useradd -m -u 10001 emithran && mkdir -p /app/var && chown -R emithran /app
USER emithran
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD curl -fs http://localhost:8000/api/health || exit 1
CMD ["sh", "-c", "alembic upgrade head && python -m emithran.cli seed-sources && uvicorn emithran.api.main:app --host 0.0.0.0 --port 8000 --proxy-headers"]
