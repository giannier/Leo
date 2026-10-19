# syntax=docker/dockerfile:1

# Frontend image for local development (docker-compose.dev.yml).
# Build context is `frontend/` (see docker-compose.dev.yml: services.frontend.build,
# context=../frontend, dockerfile=Dockerfile), so all COPY paths below are
# relative to this directory.
#
# The "dev" stage is the default for docker-compose.dev.yml (target: dev); the
# "prod" stage below is the production image (TASK-026).

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


# ----------------------------------------------------------------------- prod
# Production image (TASK-026). `NEXT_PUBLIC_API_URL` is read at build time:
# Next.js inlines it into the browser bundle, so it is a build argument, not
# a runtime variable. With Caddy serving `/api` on the same domain, it is the
# site's own address and the browser never makes a cross-origin call.

FROM node:24-slim AS prod-build
ARG NEXT_PUBLIC_API_URL
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
    NEXT_TELEMETRY_DISABLED=1
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build && npm prune --omit=dev

FROM node:24-slim AS prod
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1
WORKDIR /app
COPY --from=prod-build --chown=node:node /app/package.json /app/package-lock.json ./
COPY --from=prod-build --chown=node:node /app/node_modules ./node_modules
COPY --from=prod-build --chown=node:node /app/.next ./.next
USER node
EXPOSE 3000
CMD ["npx", "next", "start", "-H", "0.0.0.0", "-p", "3000"]
