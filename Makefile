# AIIC committee orchestrator — developer entry points.
#
# Everything here runs in Docker. The Mac's host Python is 3.14 and too new for
# the pinned dependencies, so a host virtualenv proves nothing (CONTRACTS.md §5).
# There is deliberately no `make venv`.
#
# Run `make` or `make help` for the list.

SHELL := /bin/bash
.DEFAULT_GOAL := help

DOCKER  ?= docker
COMPOSE ?= $(DOCKER) compose
PROJECT ?= aiic

# Image used for lint / type / test / audit. Built from the `dev` stage of
# backend/Dockerfile, which layers requirements-dev.txt on top of the runtime
# image. It is never what production runs.
DEV_IMAGE ?= aiic-dev

# The repo root is mounted at /src so the tools see pyproject.toml (repo root)
# and backend/ (source + tests) at the same time. The image's own /app copy is
# irrelevant for these targets.
DEV_RUN = $(DOCKER) run --rm -v "$(CURDIR)":/src -w /src $(DEV_IMAGE)

.PHONY: help
help: ## Show this help
	@echo "AIIC — make targets"
	@echo
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | sort \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'
	@echo
	@echo "First run:  cp .env.example .env  &&  edit it  &&  make up"

# ---------------------------------------------------------------------------
# Environment guard
# ---------------------------------------------------------------------------
.PHONY: env-check
env-check: ## Fail early with a useful message if .env is missing
	@test -f .env || { \
	  echo "ERROR: .env not found."; \
	  echo "  cp .env.example .env   then fill in ANTHROPIC_API_KEY and POSTGRES_PASSWORD."; \
	  echo "  docker-compose.yml uses \$${POSTGRES_PASSWORD:?...} and fails fast by design."; \
	  exit 1; }

# ---------------------------------------------------------------------------
# Stack lifecycle
# ---------------------------------------------------------------------------
.PHONY: up
up: env-check ## Build if needed and start postgres, redis and the backend
	$(COMPOSE) -p $(PROJECT) up -d --build
	@echo
	@$(COMPOSE) -p $(PROJECT) ps
	@echo
	@echo "Backend:  http://localhost:8100/docs"

.PHONY: down
down: ## Stop and remove the containers. Volumes (and the database) survive.
	$(COMPOSE) -p $(PROJECT) down

.PHONY: ps
ps: ## Show container status
	$(COMPOSE) -p $(PROJECT) ps

.PHONY: logs
logs: ## Tail the backend log (make logs SERVICE=postgres for another)
	$(COMPOSE) -p $(PROJECT) logs -f --tail=200 $(or $(SERVICE),backend)

.PHONY: restart
restart: ## Restart the backend. Does NOT pick up .env changes — use `make recreate`.
	$(COMPOSE) -p $(PROJECT) restart backend

.PHONY: recreate
recreate: env-check ## Recreate the backend so a changed .env is actually read
	$(COMPOSE) -p $(PROJECT) up -d --force-recreate backend

.PHONY: shell
shell: ## Open a shell inside the running backend container
	$(COMPOSE) -p $(PROJECT) exec backend /bin/bash

.PHONY: build
build: ## Build the production backend image (the `runtime` stage)
	$(COMPOSE) -p $(PROJECT) build backend

.PHONY: dev-image
dev-image: ## Build the dev image used by lint / typecheck / test / audit
	$(DOCKER) build --target dev -t $(DEV_IMAGE) backend

# ---------------------------------------------------------------------------
# Validation — each uses the instrument that consumes the artifact
# ---------------------------------------------------------------------------
.PHONY: compose-check
compose-check: ## Validate docker-compose.yml with Docker itself (no secrets needed)
	$(COMPOSE) config -q --no-interpolate
	@echo "compose: YAML and schema OK"

.PHONY: compose-check-full
compose-check-full: env-check ## Validate compose *with* interpolation against your real .env
	$(COMPOSE) config -q
	@echo "compose: interpolation OK"

.PHONY: import-check
import-check: ## Import app.main in the container's Python 3.12 (compile != import)
	$(COMPOSE) -p $(PROJECT) run --rm --no-deps backend python3 -c "import app.main; print('IMPORT OK')"

.PHONY: lint
lint: dev-image ## Run ruff
	$(DEV_RUN) ruff check .

.PHONY: fmt
fmt: dev-image ## Apply ruff's autofixes and formatter
	$(DEV_RUN) ruff check --fix .
	$(DEV_RUN) ruff format .

.PHONY: fmt-check
fmt-check: dev-image ## Check formatting without writing
	$(DEV_RUN) ruff format --check .

.PHONY: typecheck
typecheck: dev-image ## Run mypy
	$(DEV_RUN) mypy

.PHONY: test
test: dev-image ## Run the test suite (excludes tests marked `live` and `db`)
# pytest exits 5 on "no tests collected". agent/qa and agent/calibration are
# writing the suite right now, so an empty run is a WARNING, not a failure.
# Any other non-zero exit is a real failure and propagates.
	@$(DEV_RUN) pytest -m "not live and not db"; rc=$$?; \
	  if [ $$rc -eq 5 ]; then \
	    echo "WARNING: no tests collected. The runner works; the suite is empty."; \
	    exit 0; \
	  fi; exit $$rc

.PHONY: test-db
test-db: env-check ## Run the tests that need a live Postgres, inside the stack
	$(COMPOSE) -p $(PROJECT) run --rm backend python -m pytest -m db

# --- ACCEPTED-VULNERABILITY LEDGER, baselined 24 Aug 2026 -------------------
# Seven advisories against starlette 0.41.3, which is transitively pinned by
# fastapi==0.115.6 (starlette<0.42.0). Every one is unreachable from this
# codebase today; clearing them all needs fastapi>=0.133. Full per-CVE
# reachability analysis: docs/reviews/dependency-audit.md.
#
# DELETE THESE LINES when fastapi is bumped. Do not add new entries without
# writing the reachability argument into the audit doc first.
AUDIT_IGNORE = \
  --ignore-vuln PYSEC-2026-161  \
  --ignore-vuln PYSEC-2026-248  \
  --ignore-vuln PYSEC-2026-249  \
  --ignore-vuln PYSEC-2026-1941 \
  --ignore-vuln PYSEC-2026-1942 \
  --ignore-vuln PYSEC-2026-2280 \
  --ignore-vuln PYSEC-2026-2281

.PHONY: audit
audit: dev-image ## Audit pinned dependencies against the accepted-vuln ledger
	$(DEV_RUN) pip-audit -r backend/requirements.txt --progress-spinner off $(AUDIT_IGNORE)

.PHONY: audit-all
audit-all: dev-image ## Audit with NO exemptions — shows the accepted ones too
	-$(DEV_RUN) pip-audit -r backend/requirements.txt --progress-spinner off

.PHONY: secrets
secrets: ## Scan the working tree and full git history for committed secrets
	@command -v gitleaks >/dev/null || { echo "gitleaks not installed: brew install gitleaks"; exit 1; }
	gitleaks detect --config .gitleaks.toml --redact --verbose
	gitleaks detect --config .gitleaks.toml --redact --no-git --source .

.PHONY: check
check: compose-check lint typecheck test audit ## Everything CI runs, locally

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
.PHONY: db-shell
db-shell: ## psql into the running database
	$(COMPOSE) -p $(PROJECT) exec postgres psql -U committee -d committee

.PHONY: migrate
migrate: ## Apply pending schema migrations (owned by agent/persistence)
	$(COMPOSE) -p $(PROJECT) exec backend python -m app.database

.PHONY: db-reset
db-reset: ## DESTROY the local database volume and re-init from init.sql
	@echo "This deletes the '$(PROJECT)_pgdata' volume and every row in it."
	@read -r -p "Type the project name '$(PROJECT)' to confirm: " ans; \
	  [ "$$ans" = "$(PROJECT)" ] || { echo "aborted"; exit 1; }
	$(COMPOSE) -p $(PROJECT) down -v
	$(COMPOSE) -p $(PROJECT) up -d postgres
	@echo "Fresh volume: init.sql has now run. It will NOT run again on this volume."

.PHONY: db-backup
db-backup: ## Dump the local database to ./aiic-db-backup-<date>.sql.gz
	$(COMPOSE) -p $(PROJECT) exec -T postgres pg_dump -U committee committee \
	  | gzip > aiic-db-backup-$$(date +%Y%m%d).sql.gz
	@ls -lh aiic-db-backup-$$(date +%Y%m%d).sql.gz

# ---------------------------------------------------------------------------
# Teardown
# ---------------------------------------------------------------------------
.PHONY: clean
clean: ## Remove containers AND volumes for this project. Destroys local data.
	$(COMPOSE) -p $(PROJECT) down -v
