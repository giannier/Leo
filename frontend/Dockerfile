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
# `dev:docker`, not `dev`: it runs webpack instead of Turbopack. Turbopack is
# the faster of the two and is what `npm run dev` uses on the host, but it
# ignores WATCHPACK_POLLING - and a bind mount from a Windows or macOS host
# never delivers inotify events, so inside a container it simply never notices
# an edit. The symptom is the worst kind: the file has changed, the container
# can read the change, and the page keeps serving the old version until
# somebody restarts it.
CMD ["npm", "run", "dev:docker", "--", "-H", "0.0.0.0", "-p", "3000"]
