#!/bin/sh
set -eu

: "${TOOLHUB_API_URL:=http://backend:8000}"
: "${INTERNAL_SERVICE_SECRET:?INTERNAL_SERVICE_SECRET chưa đặt}"
: "${TOOLHUB_SPOOL_DIR:=/var/spool/toolhub}"

# Secret cho script (sshd không truyền env): root:toolhub 0640.
mkdir -p /etc/toolhub
umask 027
cat > /etc/toolhub/git-service.env <<CFG
TOOLHUB_API_URL=${TOOLHUB_API_URL}
TOOLHUB_INTERNAL_SECRET=${INTERNAL_SERVICE_SECRET}
TOOLHUB_SPOOL_DIR=${TOOLHUB_SPOOL_DIR}
CFG
chown root:toolhub /etc/toolhub/git-service.env
chmod 0640 /etc/toolhub/git-service.env
umask 022

# Host key bền vững (volume) → fingerprint server không đổi sau mỗi lần deploy.
mkdir -p /data/ssh
[ -f /data/ssh/ssh_host_ed25519_key ] || ssh-keygen -q -t ed25519 -N "" -f /data/ssh/ssh_host_ed25519_key
[ -f /data/ssh/ssh_host_rsa_key ] || ssh-keygen -q -t rsa -b 4096 -N "" -f /data/ssh/ssh_host_rsa_key
chmod 600 /data/ssh/ssh_host_*_key

mkdir -p /data/git/repositories "$TOOLHUB_SPOOL_DIR" /run/sshd
chown git:git /data/git/repositories "$TOOLHUB_SPOOL_DIR"

echo "git-service: host key fingerprints:"
for k in /data/ssh/ssh_host_*_key.pub; do ssh-keygen -lf "$k"; done

# Gửi lại event push bị kẹt (chạy nền dưới quyền git).
setpriv --reuid=git --regid=git --init-groups /opt/toolhub/bin/toolhub-spool-flush &

exec /usr/sbin/sshd -D -e -f /etc/ssh/sshd_config
