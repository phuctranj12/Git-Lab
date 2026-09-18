#!/usr/bin/env bash
# Khôi phục từ một thư mục backup do backup.sh tạo (xem docs/DEPLOYMENT.md mục "Restore").
#   ./deploy/scripts/restore.sh /backups/2026-09-18_0200 [--yes]
# CẢNH BÁO: GHI ĐÈ toàn bộ DB, repository và object MinIO hiện tại.
set -euo pipefail
cd "$(dirname "$0")/../.."

SRC=${1:?Dùng: restore.sh <thư mục backup> [--yes]}
CONFIRM=${2:-}
ENV_FILE=${ENV_FILE:-deploy/.env}
MC_IMAGE=${MC_IMAGE:-quay.io/minio/mc:RELEASE.2025-04-16T18-13-26Z}
COMPOSE="docker compose -f deploy/docker-compose.yml --env-file $ENV_FILE"
PROJECT=toolhub

[ -f "$SRC/postgres.dump" ] || { echo "Không thấy $SRC/postgres.dump"; exit 1; }
env_get() { grep -E "^$1=" "$ENV_FILE" | tail -1 | cut -d= -f2- | sed -E 's/[[:space:]]+#.*$//; s/^"(.*)"$/\1/'; }
PG_USER=$(env_get POSTGRES_USER); PG_USER=${PG_USER:-toolhub}
PG_DB=$(env_get POSTGRES_DB); PG_DB=${PG_DB:-toolhub}
MINIO_KEY=$(env_get MINIO_ACCESS_KEY)
MINIO_SECRET=$(env_get MINIO_SECRET_KEY)
TAG=$(env_get TOOLHUB_TAG); TAG=${TAG:-1.0.0}
log() { printf '[%s] %s\n' "$(date +%T)" "$*"; }

cat "$SRC/metadata.json" 2>/dev/null || true
if [ "$CONFIRM" != "--yes" ]; then
    read -r -p "GHI ĐÈ dữ liệu hiện tại bằng backup $SRC? Gõ 'restore' để tiếp tục: " ans
    [ "$ans" = "restore" ] || { echo "Huỷ."; exit 1; }
fi

log "0. Kiểm tra checksum"
if command -v python3 >/dev/null && [ -f "$SRC/metadata.json" ]; then
    want=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['postgres_dump_sha256'])" "$SRC/metadata.json")
    have=$(sha256sum "$SRC/postgres.dump" | cut -d' ' -f1)
    [ "$want" = "$have" ] || { echo "postgres.dump sai checksum!"; exit 1; }
fi

log "1. Dừng dịch vụ ghi dữ liệu"
$COMPOSE stop web git-service worker backend

log "2. PostgreSQL"
$COMPOSE up -d postgres
$COMPOSE exec -T postgres sh -c "until pg_isready -U $PG_USER; do sleep 1; done"
$COMPOSE exec -T postgres psql -U "$PG_USER" -d postgres -v ON_ERROR_STOP=1 \
  -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='$PG_DB' AND pid<>pg_backend_pid();" \
  -c "DROP DATABASE IF EXISTS \"$PG_DB\";" -c "CREATE DATABASE \"$PG_DB\" OWNER \"$PG_USER\";"
$COMPOSE exec -T postgres pg_restore -U "$PG_USER" -d "$PG_DB" --no-owner --exit-on-error < "$SRC/postgres.dump"

log "3. Git repositories"
ARCHIVE=$(ls "$SRC"/git-repositories.tar.* | head -1)
case "$ARCHIVE" in
    *.zst) DECOMP="zstd -dc" ;;
    *.gz) DECOMP="gzip -dc" ;;
esac
docker run --rm -v ${PROJECT}_git_repositories:/dst "toolhub-backend:$TAG" sh -c 'find /dst -mindepth 1 -delete'
$DECOMP "$ARCHIVE" | docker run --rm -i -v ${PROJECT}_git_repositories:/dst "toolhub-backend:$TAG" tar -C /dst -xf -
if [ -f "$SRC/git-ssh-hostkeys.tar" ]; then
    docker run --rm -i -u 0 -v ${PROJECT}_git_ssh:/dst "toolhub-backend:$TAG" tar -C /dst -xf - < "$SRC/git-ssh-hostkeys.tar"
fi

log "4. MinIO"
$COMPOSE up -d minio
if [ -d "$SRC/minio" ]; then
    for dir in "$SRC"/minio/*/; do
        bucket=$(basename "$dir")
        docker run --rm --network ${PROJECT}_default -v "$SRC/minio:/backup:ro" \
          -e MC_HOST_dst=http://${MINIO_KEY}:${MINIO_SECRET}@minio:9000 "$MC_IMAGE" \
          sh -c "mc mb --ignore-existing dst/$bucket && mc mirror --overwrite --quiet /backup/$bucket dst/$bucket"
    done
else
    log "   backup không có mirror MinIO — khôi phục object từ replication/mc mirror riêng"
fi

log "5. Khởi động lại + bootstrap (migrate idempotent)"
$COMPOSE up -d
./deploy/scripts/healthcheck.sh
log "KHÔI PHỤC XONG từ $SRC"
