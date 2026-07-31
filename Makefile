.PHONY: up down test isolation evals lint format migrate worker front-install front-lint front-build front-dev

COMPOSE := docker compose -f deploy/docker-compose.dev.yml --env-file .env

up: ## Start the local dev stack (Postgres, Redis, MinIO, backend, frontend).
	$(COMPOSE) up -d --build

down: ## Stop the local dev stack.
	$(COMPOSE) down

test: ## Run the full backend test suite.
	uv run pytest backend/tests -v

isolation: ## Run only the multi-tenant isolation suite.
	uv run pytest backend/tests/isolation -v

evals: ## Run the agent evals dataset (dataset ships in TASK-016).
	uv run pytest backend/evals -v

lint: ## Ruff (check + format) + mypy, matching CI.
	uv run ruff check backend
	uv run ruff format --check backend
	uv run mypy backend/app

format: ## Auto-fix lint issues and reformat.
	uv run ruff check backend --fix
	uv run ruff format backend

migrate: ## Apply all pending Alembic migrations.
	uv run --directory backend alembic upgrade head

worker: ## Run the ARQ worker (knowledge indexing). Needs Redis.
	uv run --directory backend arq app.workers.settings.WorkerSettings

front-install: ## Install frontend dependencies.
	cd frontend && npm install

front-lint: ## Lint the frontend (ESLint).
	cd frontend && npm run lint

front-build: ## Type-check and production-build the frontend, matching CI.
	cd frontend && npm run build

front-dev: ## Run the frontend dev server outside Docker.
	cd frontend && npm run dev
