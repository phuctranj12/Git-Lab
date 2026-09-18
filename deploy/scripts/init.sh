#!/usr/bin/env bash
# Tạo deploy/.env từ .env.example với secret ngẫu nhiên (chỉ khi chưa có). Sau đó sửa URL/host rồi `make bootstrap`.
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -f .env ]; then
    echo "deploy/.env đã tồn tại — không ghi đè."
    exit 0
fi
command -v openssl >/dev/null || { echo "Cần openssl"; exit 1; }
cp .env.example .env
for key in POSTGRES_PASSWORD JWT_SECRET INTERNAL_SERVICE_SECRET RUNNER_TOKEN MINIO_SECRET_KEY; do
    sed -i "s|^${key}=.*|${key}=$(openssl rand -hex 32)|" .env
done
sed -i "s|^FIRST_ADMIN_PASSWORD=.*|FIRST_ADMIN_PASSWORD=$(openssl rand -base64 18 | tr -d '/+=')|" .env
chmod 600 .env
IP=$(hostname -I 2>/dev/null | awk '{print $1}')
if [ -n "${IP:-}" ]; then
    sed -i "s|SERVER_IP|$IP|g" .env
fi
echo "Đã tạo deploy/.env (quyền 600). Mật khẩu admin ban đầu:"
grep '^FIRST_ADMIN_PASSWORD=' .env
echo "Kiểm tra lại WEB_BASE_URL / PACKAGE_BASE_URL / GIT_SSH_HOST rồi chạy: make bootstrap"
