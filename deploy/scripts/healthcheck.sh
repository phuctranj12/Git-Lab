#!/usr/bin/env bash
# Kiểm tra nhanh stack đang chạy: API ready (PG/Redis/MinIO/Git storage), registry, Git SSH.
# Exit 0 = OK. Dùng cho monitoring/cron hoặc sau deploy.
set -uo pipefail
cd "$(dirname "$0")/../.."
ENV_FILE=${ENV_FILE:-deploy/.env}
env_get() { grep -E "^$1=" "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2- | sed -E 's/[[:space:]]+#.*$//'; }

WEB=${WEB_URL:-http://127.0.0.1:$(env_get WEB_PORT || true)}
WEB=${WEB%:}
[ "$WEB" = "http://127.0.0.1" ] && WEB=http://127.0.0.1:8080
PKG_PORT=$(env_get PACKAGES_PORT); PKG=${PACKAGES_URL:-http://127.0.0.1:${PKG_PORT:-8081}}
SSH_PORT=$(env_get GIT_SSH_PORT); SSH_PORT=${SSH_PORT:-2222}

fail=0
check() {
    local name=$1; shift
    local out
    for _ in $(seq 1 ${RETRIES:-30}); do
        if out=$("$@" 2>&1); then printf '  %-22s PASS\n' "$name"; return 0; fi
        sleep 2
    done
    printf '  %-22s FAIL  %s\n' "$name" "$(echo "$out" | head -c 300)"
    fail=1
}

echo "HAWEE Tool Hub healthcheck — $WEB"
check "API live"        curl -fsS "$WEB/health/live"
check "API ready"       curl -fsS "$WEB/health/ready"
check "Registry"        curl -fsS "$PKG/health"
check "Git SSH port"    bash -c "exec 3<>/dev/tcp/127.0.0.1/$SSH_PORT && head -c 7 <&3 | grep -q SSH-2.0"
curl -fsS "$WEB/health/ready" 2>/dev/null | python3 -m json.tool 2>/dev/null | sed 's/^/    /' || true
exit $fail
