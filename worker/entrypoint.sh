#!/bin/sh
set -eu
SOCK=/var/run/docker.sock
if [ -S "$SOCK" ]; then
    GID=$(stat -c '%g' "$SOCK")
    if ! getent group "$GID" >/dev/null 2>&1; then
        groupadd -g "$GID" dockersock
    fi
    GROUP=$(getent group "$GID" | cut -d: -f1)
    usermod -aG "$GROUP" worker
else
    echo "WARN: không thấy $SOCK — worker không tạo được container build" >&2
fi
# RUN_BEAT=true trên ĐÚNG MỘT worker để chạy job bảo trì định kỳ.
if [ "${RUN_BEAT:-false}" = "true" ] && [ "$1" = "celery" ]; then
    set -- "$@" -B --schedule /tmp/celerybeat-schedule
fi
exec setpriv --reuid=worker --regid=worker --init-groups "$@"
