#!/usr/bin/env bash
# Chạy toàn bộ automated test (backend unit+integration, worker, frontend) — tương đương `make test`.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PYTHON:-python3}
docker compose -f deploy/docker-compose.test.yml up -d --wait
trap 'docker compose -f deploy/docker-compose.test.yml down -v >/dev/null 2>&1 || true' EXIT
(cd backend && $PY -m pytest --cov=app --cov-report=term-missing:skip-covered)
(cd worker && $PY -m pytest)
(cd frontend && npm ci --no-audit --no-fund && npm run typecheck && npm run build)
echo "ALL TESTS PASSED"
