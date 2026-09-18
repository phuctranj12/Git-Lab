# HAWEE Tool Hub — lệnh vận hành (spec mục 51). Chạy trên Linux/macOS (Windows: Git Bash + make, hoặc WSL).
SHELL := /bin/bash
ENV_FILE ?= deploy/.env
COMPOSE := docker compose -f deploy/docker-compose.yml --env-file $(ENV_FILE)
COMPOSE_PROD := docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.prod.yml --env-file $(ENV_FILE)
TEST_COMPOSE := docker compose -f deploy/docker-compose.test.yml
PYTHON ?= python3

.PHONY: help dev prod stop logs ps migrate seed bootstrap test test-unit test-integration test-worker test-frontend \
        e2e smoke-test backup restore healthcheck check-repo

help:
	@grep -E '^[a-z-]+:' Makefile | cut -d: -f1 | sort | xargs -n1 echo "  make"

$(ENV_FILE):
	@echo "Thiếu $(ENV_FILE) — chạy: cp deploy/.env.example $(ENV_FILE) && điền secret (openssl rand -hex 32)"; exit 1

dev: $(ENV_FILE)          ## build + chạy stack staging
	$(COMPOSE) up -d --build
	$(COMPOSE) ps

prod: $(ENV_FILE)         ## build + chạy stack production (HTTPS)
	$(COMPOSE_PROD) up -d --build
	$(COMPOSE_PROD) ps

stop:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail=200

ps:
	$(COMPOSE) ps

migrate:                  ## alembic upgrade head
	$(COMPOSE) run --rm migrate python -m app.cli migrate

bootstrap: $(ENV_FILE)    ## lần đầu deploy: env → PG → migrate → bucket → admin → runner token → git → health
	$(COMPOSE) up -d postgres redis minio
	$(COMPOSE) run --rm migrate python -m app.cli bootstrap
	$(COMPOSE) up -d --build
	./deploy/scripts/healthcheck.sh

seed:                     ## dữ liệu demo (group/project/user mẫu)
	$(COMPOSE) exec -T backend python -m app.seed

# ── Test ──
test: test-unit test-integration test-worker test-frontend  ## toàn bộ automated test

test-unit:
	cd backend && $(PYTHON) -m pytest -m unit

test-integration:         ## cần Postgres/Redis/MinIO tạm (deploy/docker-compose.test.yml)
	$(TEST_COMPOSE) up -d --wait
	cd backend && $(PYTHON) -m pytest --cov=app --cov-report=term-missing:skip-covered; rc=$$?; \
	  cd .. && $(TEST_COMPOSE) down -v; exit $$rc

test-worker:
	cd worker && $(PYTHON) -m pytest

test-frontend:
	cd frontend && npm ci --no-audit --no-fund && npm run typecheck && npm run build

e2e:                      ## end-to-end trên stack đang chạy: git push → build → release → pip install
	$(PYTHON) scripts/smoke_test.py --env-file $(ENV_FILE) --full

smoke-test:               ## smoke test production (spec mục 52)
	$(PYTHON) scripts/smoke_test.py --env-file $(ENV_FILE)

backup:
	./deploy/scripts/backup.sh

restore:                  ## make restore BACKUP=/backups/2026-09-18_0200
	./deploy/scripts/restore.sh $(BACKUP)

healthcheck:
	./deploy/scripts/healthcheck.sh

check-repo:
	bash scripts/check_repo.sh .
