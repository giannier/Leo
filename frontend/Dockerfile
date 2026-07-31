# syntax=docker/dockerfile:1

# Frontend image for local development (docker-compose.dev.yml).
# Build context is `frontend/` (see docker-compose.dev.yml: services.frontend.build,
# context=../frontend, dockerfile=Dockerfile), so all COPY paths below are
# relative to this directory.
#
# NOTE: this "dev" stage installs dependencies fresh via `npm ci` at build
# time. A pinned, multi-stage production image is introduced in TASK-026.

FROM node:24-slim AS dev

ENV NODE_ENV=development

WORKDIR /app

# Copy the dependency manifests first so `npm ci` is cached across rebuilds
# that only touch application code.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .

EXPOSE 3000

# Hot-reload dev server; source is bind-mounted by docker-compose.dev.yml.
# `-H 0.0.0.0` binds inside the container so the mapped port is reachable
# from the host; the internal port stays fixed at 3000 (host-facing port is
# configurable via FRONTEND_PORT in docker-compose.dev.yml).
CMD ["npm", "run", "dev", "--", "-H", "0.0.0.0", "-p", "3000"]
