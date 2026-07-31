# syntax=docker/dockerfile:1

# Backend image for local development (docker-compose.dev.yml).
# Build context is `backend/` (see docker-compose.dev.yml: build.context=../backend),
# so all COPY paths below are relative to the `backend/` directory.
#
# NOTE: this "dev" stage resolves dependencies fresh from pyproject.toml at
# build time (no committed lockfile is copied in). A pinned, multi-stage
# production image is introduced in TASK-026.

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
