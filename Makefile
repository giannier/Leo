.PHONY: admin reindex simulate-trial env-reference hooks up down test isolation coverage evals lint format migrate worker front-install front-lint front-build front-e2e front-dev

# `--env-file .env` is load-bearing, not decoration: it makes the repo's
# own .env the source for variable interpolation. Compose otherwise
# interpolates from the shell alone, so a `${LLM_PROFILE:-offline}`
# default in the compose file silently wins over the value in .env -
# `environment:` beats `env_file:`, and the file you edited loses to a
# default you never saw. Always drive the stack through `make`, or pass
# the flag by hand.
COMPOSE := docker compose -f deploy/docker-compose.dev.yml --env-file .env

env-reference: ## Regenerate deploy/env.reference from Settings.
	uv run --directory backend python -m scripts.env_reference

hooks: ## Install the git hooks that gate pushes to main.
	git config core.hooksPath deploy/git-hooks
	chmod +x deploy/git-hooks/* 2>/dev/null || true
	@echo "core.hooksPath -> deploy/git-hooks"

up: ## Start the local dev stack (Postgres, Redis, MinIO, backend, frontend).
	$(COMPOSE) up -d --build

down: ## Stop the local dev stack.
	$(COMPOSE) down

test: ## Run the full backend test suite.
	uv run python -m pytest backend/tests -v

isolation: ## Run only the multi-tenant isolation suite.
	uv run python -m pytest backend/tests/isolation -v

coverage: ## Run the suite under coverage and enforce the Constitution's standard.
	uv run --directory backend python -m coverage run -m pytest -q
	uv run --directory backend python -m coverage report
	uv run --directory backend python -m scripts.coverage_gate

evals: ## Run the golden agent conversations against the configured model.
	uv run python -m pytest backend/evals -v -m evals

lint: ## Ruff (check + format) + mypy, matching CI.
	uv run python -m ruff check backend
	uv run python -m ruff format --check backend
	uv run python -m mypy backend/app

format: ## Auto-fix lint issues and reformat.
	uv run python -m ruff check backend --fix
	uv run python -m ruff format backend

admin: ## Grant platform-admin rights. EMAIL=tu@correo.com [ARGS=--revoke]
	uv run --directory backend python -m scripts.grant_platform_admin $(EMAIL) $(ARGS)

reindex: ## Re-embed knowledge whose vectors came from another model. ARGS=--dry-run to preview.
	uv run --directory backend python -m scripts.reindex_knowledge $(ARGS)

simulate-trial: ## Walk a tenant through a whole 14-day trial in seconds.
	uv run --directory backend python -m scripts.simulate_trial

migrate: ## Apply all pending Alembic migrations.
	uv run --directory backend python -m alembic upgrade head

worker: ## Run the ARQ worker (knowledge indexing). Needs Redis.
	uv run --directory backend python -m arq app.workers.settings.WorkerSettings

front-install: ## Install frontend dependencies.
	cd frontend && npm install

front-lint: ## Lint the frontend (ESLint).
	cd frontend && npm run lint

front-build: ## Type-check and production-build the frontend, matching CI.
	cd frontend && npm run build

front-e2e: ## Run the Playwright suite against the local stack (needs `make up`).
	cd frontend && E2E_OUTBOX=../backend/.tmp/outbox.jsonl npx playwright test

front-dev: ## Run the frontend dev server outside Docker.
	cd frontend && npm run dev
