#!/usr/bin/env bash
# Backup HAWEE Tool Hub (spec mục 36):
#   /backups/YYYY-MM-DD_HHMM/
#   ├── postgres.dump              pg_dump -Fc
#   ├── git-repositories.tar.zst   toàn bộ bare repo (.tar.gz nếu host không có zstd)
#   ├── git-ssh-hostkeys.tar       host key SSH (giữ fingerprint khi khôi phục)
#   ├── minio/<bucket>/…           mirror package nội bộ + log + artifact (bỏ cache PyPI — tải lại được)
#   ├── minio-manifest.json        danh sách object + size + etag
#   └── metadata.json              thời điểm, version, alembic revision, checksum
# Biến: ENV_FILE (deploy/.env), BACKUP_ROOT (/backups), BACKUP_RETENTION_DAYS (14), MINIO_MIRROR (true)
set -euo pipefail
cd "$(dirname "$0")/../.."

ENV_FILE=${ENV_FILE:-deploy/.env}
BACKUP_ROOT=${BACKUP_ROOT:-/backups}
RETENTION=${BACKUP_RETENTION_DAYS:-14}
MINIO_MIRROR=${MINIO_MIRROR:-true}
MC_IMAGE=${MC_IMAGE:-quay.io/minio/mc:RELEASE.2025-04-16T18-13-26Z}
COMPOSE="docker compose -f deploy/docker-compose.yml --env-file $ENV_FILE"
PROJECT=toolhub

env_get() { grep -E "^$1=" "$ENV_FILE" | tail -1 | cut -d= -f2- | sed -E 's/[[:space:]]+#.*$//; s/^"(.*)"$/\1/'; }
PG_USER=$(env_get POSTGRES_USER); PG_USER=${PG_USER:-toolhub}
PG_DB=$(env_get POSTGRES_DB); PG_DB=${PG_DB:-toolhub}
MINIO_KEY=$(env_get MINIO_ACCESS_KEY)
MINIO_SECRET=$(env_get MINIO_SECRET_KEY)
TAG=$(env_get TOOLHUB_TAG); TAG=${TAG:-1.0.0}

STAMP=$(date +%F_%H%M)
DEST="$BACKUP_ROOT/$STAMP"
mkdir -p "$DEST"
chmod 700 "$DEST"
log() { printf '[%s] %s\n' "$(date +%T)" "$*"; }

log "1/5 PostgreSQL → postgres.dump"
$COMPOSE exec -T postgres pg_dump -U "$PG_USER" -d "$PG_DB" -Fc > "$DEST/postgres.dump"

log "2/5 Git repositories"
if command -v zstd >/dev/null 2>&1; then
    GIT_ARCHIVE="git-repositories.tar.zst"
    docker run --rm -v ${PROJECT}_git_repositories:/src:ro "toolhub-backend:$TAG" tar -C /src -cf - . | zstd -T0 -q -o "$DEST/$GIT_ARCHIVE"
else
    GIT_ARCHIVE="git-repositories.tar.gz"
    log "   (host không có zstd → dùng gzip)"
    docker run --rm -v ${PROJECT}_git_repositories:/src:ro "toolhub-backend:$TAG" tar -C /src -czf - . > "$DEST/$GIT_ARCHIVE"
fi
docker run --rm -v ${PROJECT}_git_ssh:/src:ro "toolhub-backend:$TAG" tar -C /src -cf - . > "$DEST/git-ssh-hostkeys.tar"

log "3/5 MinIO manifest"
MC="docker run --rm --network ${PROJECT}_default -e MC_HOST_src=http://${MINIO_KEY}:${MINIO_SECRET}@minio:9000"
$MC "$MC_IMAGE" ls --recursive --json src > "$DEST/minio-manifest.json"

if [ "$MINIO_MIRROR" = "true" ]; then
    log "4/5 MinIO mirror (package nội bộ, log, artifact — bỏ cache PyPI)"
    mkdir -p "$DEST/minio"
    for bucket in $(env_get MINIO_BUCKET_PACKAGES) $(env_get MINIO_BUCKET_LOGS) $(env_get MINIO_BUCKET_ARTIFACTS); do
        $MC -v "$DEST/minio:/backup" "$MC_IMAGE" mirror --quiet --exclude "upstream/*" "src/$bucket" "/backup/$bucket"
    done
else
    log "4/5 bỏ qua mirror MinIO (MINIO_MIRROR=false — production dùng replication/mc mirror riêng)"
fi

log "5/5 metadata.json"
REVISION=$($COMPOSE exec -T postgres psql -U "$PG_USER" -d "$PG_DB" -tAc "SELECT version_num FROM alembic_version" 2>/dev/null | tr -d '[:space:]' || true)
cat > "$DEST/metadata.json" <<META
{
  "created_at": "$(date -Iseconds)",
  "toolhub_tag": "$TAG",
  "alembic_revision": "$REVISION",
  "postgres_dump_sha256": "$(sha256sum "$DEST/postgres.dump" | cut -d' ' -f1)",
  "git_archive": "$GIT_ARCHIVE",
  "git_archive_sha256": "$(sha256sum "$DEST/$GIT_ARCHIVE" | cut -d' ' -f1)",
  "minio_mirrored": $([ "$MINIO_MIRROR" = "true" ] && echo true || echo false),
  "host": "$(hostname)"
}
META

log "Dọn backup cũ hơn $RETENTION ngày trong $BACKUP_ROOT"
find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -name '20*' -mtime +"$RETENTION" -exec rm -rf {} + || true

log "XONG: $DEST ($(du -sh "$DEST" | cut -f1))"
