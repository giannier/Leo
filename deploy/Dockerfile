# syntax=docker/dockerfile:1

# Backend image for local development (docker-compose.dev.yml).
# Build context is `backend/` (see docker-compose.dev.yml: build.context=../backend),
# so all COPY paths below are relative to the `backend/` directory.
#
# NOTE: the "dev" stage resolves dependencies fresh from pyproject.toml at
# build time (no committed lockfile is copied in). The "prod" stage below
# (TASK-026) installs exactly what `uv.lock` pins, without the dev group.

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS dev

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Copy the dependency manifest first so dependency installation is cached
# across rebuilds that only touch application code.
COPY pyproject.toml ./
RUN uv sync --no-install-project

COPY . .
RUN uv sync

EXPOSE 8000

# Hot-reload dev server; source is bind-mounted by docker-compose.dev.yml.
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]


# ----------------------------------------------------------------------- prod
# Production image (TASK-026, docker-compose.prod.yml). Pinned by `uv.lock`,
# no dev tools, no source bind mount, not root. The same image runs the API,
# the ARQ worker and the one-shot migration; the command picks which.

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS prod-build

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY . .
RUN uv sync --frozen --no-dev && rm -rf tests evals .tmp

FROM python:3.12-slim-bookworm AS prod

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

RUN useradd --system --uid 10001 --home /app leo
WORKDIR /app
COPY --from=prod-build --chown=leo:leo /app /app
USER leo

EXPOSE 8000

# Two processes: realtime fans out through Redis, so the API is stateless
# across workers. `--proxy-headers` with Caddy in front (TRUST_PROXY_HEADERS).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--proxy-headers", "--forwarded-allow-ips", "*"]
