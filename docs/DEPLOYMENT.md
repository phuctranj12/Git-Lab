# Hướng dẫn Triển khai & Vận hành HAWEE Tool Hub

Tài liệu hướng dẫn chi tiết quy trình cài đặt, cấu hình, triển khai production, nâng cấp hệ thống, sao lưu và phục hồi thảm họa cho nền tảng **HAWEE Tool Hub**.

---

## 1. Yêu cầu Hệ thống & Kiến trúc Triển khai

### 1.1. Yêu cầu phần cứng đề xuất
- **CPU**: Tối thiểu 4 Cores (khuyến nghị 8 Cores nếu có nhiều build đồng thời).
- **RAM**: Tối thiểu 8 GB (khuyến nghị 16 GB; mỗi container build chiếm tối đa 2–4 GB theo cấu hình).
- **Ổ cứng**: Tối thiểu 100 GB SSD (khuyến nghị 250 GB+ SSD NVMe để chứa Docker images, Git bare repos, database PostgreSQL và cache packages MinIO).
- **Hệ điều hành**: Linux (Ubuntu 22.04 LTS / Debian 12 khuyến nghị; hỗ trợ RHEL/Rocky Linux 9). Trên máy cá nhân Windows có thể dùng WSL2 hoặc Docker Desktop.

### 1.2. Yêu cầu phần mềm
- **Docker Engine**: Phiên bản 24.0+ hoặc mới hơn.
- **Docker Compose**: Phiên bản v2.20+ (sử dụng cú pháp lệnh `docker compose`).
- **Make**: Tiện ích GNU Make (có sẵn trên Linux/macOS; Windows dùng Git Bash hoặc WSL).
- **Mạng**:
  - Máy chủ cần quyền tải các image cơ sở (`quay.io/minio/minio`, `python:3.12-slim`, `node:20-alpine`, `nginx:alpine`).
  - Cần truy cập ra Internet tới `https://pypi.org` và `files.pythonhosted.org` để proxy & cache package ngoài (trừ khi triển khai môi trường hoàn toàn air-gapped).

### 1.3. Mô hình triển khai

```text
┌─────────────────────────────────────────────────────────────────────────┐
│ Host Server A (Single-Server hoặc Core Services)                       │
│                                                                         │
│   Host Ports:                                                           │
│     :8080 (hoặc :80/:443)  ──▶ Nginx (web / frontend + API proxy)       │
│     :8081                  ──▶ Nginx (PyPI Simple API & Package proxy) │
│     :2222                  ──▶ git-service (OpenSSH bare repos)         │
│                                                                         │
│   Internal Network:                                                     │
│     backend (FastAPI)                                                   │
│     postgres (PostgreSQL 16)                                            │
│     redis (Redis 7)                                                     │
│     minio (MinIO Object Storage)                                        │
│     worker (Celery Builder Coordinator)                                 │
└─────────────────────────────────────────────────────────────────────────┘
```

- **Mô hình Single-server**: Toàn bộ stack chạy chung trên 1 server vật lý / VM thông qua `deploy/docker-compose.yml`.
- **Mô hình Multi-server (Worker tách biệt)**: Server A chứa DB, MinIO, Git và API. Server B chứa Docker daemon và Worker chạy job build (xem mục 6).

---

## 2. Chuẩn bị Môi trường & Cấu hình (.env)

### 2.1. Khởi tạo file cấu hình tự động
Tại thư mục gốc của repository, chạy script khởi tạo:

```bash
chmod +x ./deploy/scripts/*.sh
./deploy/scripts/init.sh
```

Script sẽ sao chép `deploy/.env.example` thành `deploy/.env` và tự động sinh ngẫu nhiên các secret an toàn (32 ký tự hex) cho:
- `POSTGRES_PASSWORD`
- `JWT_SECRET`
- `INTERNAL_SERVICE_SECRET`
- `RUNNER_TOKEN`
- `MINIO_SECRET_KEY`
- `FIRST_ADMIN_PASSWORD`

### 2.2. Kiểm tra và điều chỉnh các biến quan trọng
Mở file `deploy/.env` và chỉnh sửa các thông số theo hạ tầng mạng thực tế:

| Biến | Ý nghĩa | Mặc định / Gợi ý |
|---|---|---|
| `APP_ENV` | Môi trường triển khai | `staging` hoặc `production` |
| `WEB_BASE_URL` | Địa chỉ Web UI người dùng truy cập | `http://SERVER_IP:8080` (hoặc domain HTTPS) |
| `PACKAGE_BASE_URL` | Địa chỉ PyPI index người dùng cấu hình pip | `http://SERVER_IP:8081` (hoặc domain HTTPS) |
| `GIT_SSH_HOST` | Hostname/IP cho lệnh `git clone ssh://...` | `SERVER_IP` hoặc domain |
| `GIT_SSH_PORT` | Port SSH dịch vụ Git | `2222` |
| `COOKIE_SECURE` | Cờ bảo mật cookie phiên làm việc | Đặt `true` nếu chạy HTTPS, `false` nếu HTTP staging |
| `REGISTRY_ALLOW_ANONYMOUS` | Cho phép pip không cần token | `false` (bắt buộc xác thực, trừ khi mạng nội bộ kín) |
| `BUILD_MAX_CONCURRENCY` | Số container build chạy song song | `2` (tùy CPU/RAM) |
| `BUILD_MEMORY_LIMIT` | Giới hạn RAM cho mỗi container build | `4g` |
| `BUILD_CPU_LIMIT` | Giới hạn CPU core cho mỗi container build | `2.0` |
| `FIRST_ADMIN_USERNAME` | Username tài khoản quản trị khởi tạo | `admin` |
| `FIRST_ADMIN_PASSWORD` | Mật khẩu tài khoản quản trị | Chuỗi ngẫu nhiên bảo mật |

> [!WARNING]
> Không bao giờ commit file `deploy/.env` lên Git. Đảm bảo phân quyền file an toàn: `chmod 600 deploy/.env`.

---

## 3. Quy trình Triển khai Lần đầu (Bootstrap)

### 3.1. Triển khai môi trường Staging / Dev (HTTP)

Chạy lệnh bootstrap để khởi tạo tự động toàn bộ hạ tầng:

```bash
make bootstrap
```

Lệnh trên thực hiện tuần tự các bước:
1. Khởi động PostgreSQL, Redis, MinIO và chờ cơ chế healthcheck sẵn sàng.
2. Chạy container one-shot `migrate` để thực thi `python -m app.cli bootstrap`:
   - Chạy Alembic migrations (`alembic upgrade head`).
   - Tự động tạo các bucket MinIO cần thiết (`toolhub-packages`, `toolhub-build-logs`, `toolhub-artifacts`, `toolhub-backups`).
   - Tạo tài khoản System Admin mặc định từ biến môi trường nếu chưa tồn tại.
   - Đăng ký và kích hoạt Build Runner token.
   - Thiết lập cấu trúc thư mục lưu trữ Git bare repositories.
3. Build và khởi động toàn bộ stack services: `web`, `backend`, `worker`, `builder`, `git-service`.
4. Thực thi kiểm tra sức khỏe dịch vụ (`./deploy/scripts/healthcheck.sh`).

### 3.2. Triển khai môi trường Production (HTTPS)

Môi trường Production sử dụng cấu hình ghép `deploy/docker-compose.yml` và `deploy/docker-compose.prod.yml`:
- Mở cổng chuẩn: `80` (redirect sang HTTPS) và `443` (SSL/TLS termination tại Nginx).
- Sử dụng domain chuẩn hóa (ví dụ: `tools.hawee.local` và `packages.hawee.local`).

**Các bước chuẩn bị:**
1. Chuẩn bị chứng chỉ SSL (`server.crt` và `server.key`) do hạ tầng CA công ty cấp, đặt vào thư mục `deploy/certs/`.
2. Cập nhật trong `deploy/.env`:
   ```env
   APP_ENV=production
   COOKIE_SECURE=true
   TOOLS_HOST=tools.hawee.local
   PACKAGES_HOST=packages.hawee.local
   WEB_BASE_URL=https://tools.hawee.local
   PACKAGE_BASE_URL=https://packages.hawee.local
   CERTS_DIR=./certs
   ```
3. Khởi động stack Production:
   ```bash
   make prod
   ```

### 3.3. Dữ liệu mẫu (Seed Demo Data - Tùy chọn)
Nếu cần dữ liệu mẫu (group `ai-tools`, project `pdf-parser`, user developer) để kiểm thử giao diện:

```bash
make seed
```

---

## 4. Kiểm tra & Giám sát Vận hành

### 4.1. Kiểm tra trạng thái hệ thống
Kiểm tra trạng thái các container:
```bash
make ps
```

Chạy script healthcheck chuyên sâu:
```bash
make healthcheck
```
Script kiểm tra tính toàn vẹn của:
- API endpoint: `GET /health/ready`
- Kết nối PostgreSQL, Redis, MinIO
- Kho Git bare repositories
- Cổng SSH 2222 sẵn sàng nhận kết nối
- Cổng PyPI Registry 8081 phản hồi chuẩn PEP 503

### 4.2. Giám sát Logs
Xem log tổng hợp hoặc log theo từng service cụ thể:
```bash
make logs                   # Xem log toàn bộ hệ thống
docker compose -f deploy/docker-compose.yml logs -f backend  # Log API backend
docker compose -f deploy/docker-compose.yml logs -f worker   # Log build runner
docker compose -f deploy/docker-compose.yml logs -f git-service # Log SSH & hooks
```

### 4.3. Giám sát Metrics Prometheus
Backend cung cấp endpoint `/metrics` định dạng chuẩn Prometheus (truy cập nội bộ):
- `http_requests_total`: Số lượng request API theo mã status code.
- `build_queue_depth`: Số lượng job build đang xếp hàng trong Celery/Redis.
- `build_duration_seconds`: Thời gian xử lý build container.
- `package_downloads_total`: Thống kê số lượt tải package nội bộ.
- `upstream_cache_hits_total`: Tỉ lệ cache hit khi proxy package từ PyPI.

---

## 5. Quy trình Nâng cấp & Cập nhật Phiên bản (Upgrade)

Khi có bản phát hành mã nguồn mới của HAWEE Tool Hub:

1. **Kéo mã nguồn mới nhất:**
   ```bash
   git fetch origin
   git checkout tags/v1.1.0  # hoặc commit SHA mong muốn
   ```

2. **Cập nhật tag trong `deploy/.env`:**
   ```env
   TOOLHUB_TAG=1.1.0
   ```

3. **Chạy di chuyển cơ sở dữ liệu (Database Migration):**
   ```bash
   make migrate
   ```
   Lệnh này thực thi an toàn `alembic upgrade head` thông qua container one-shot mà không làm gián đoạn các dịch vụ đang đọc DB.

4. **Rebuild và tái khởi động container:**
   ```bash
   make dev     # hoặc make prod nếu đang chạy production
   ```
   Docker Compose sẽ chỉ recreate các container có image hoặc cấu hình thay đổi.

---

## 6. Sao lưu & Phục hồi Thảm họa (Backup & Disaster Recovery)

Hệ thống cung cấp cơ chế sao lưu toàn diện trạng thái 3 trụ cột dữ liệu: Cơ sở dữ liệu quan hệ (PostgreSQL), Kho mã nguồn (Git Bare Repositories + SSH Hostkeys), và Kho lưu trữ tệp (MinIO Object Storage).

### 6.1. Cấu trúc thư mục Backup
Script `./deploy/scripts/backup.sh` tạo thư mục backup theo định dạng timestamp `/backups/YYYY-MM-DD_HHMM/`:

```text
/backups/2026-09-18_0200/
├── postgres.dump              # PostgreSQL dump dạng custom binary (-Fc)
├── git-repositories.tar.zst   # Lưu trữ nén toàn bộ bare repositories (hoặc .tar.gz)
├── git-ssh-hostkeys.tar       # SSH host keys để bảo tồn fingerprint server
├── minio/                     # Mirror các bucket packages, logs, artifacts (bỏ qua cache ngoài)
│   ├── toolhub-packages/
│   ├── toolhub-build-logs/
│   └── toolhub-artifacts/
├── minio-manifest.json        # Danh sách chi tiết các object, kích thước và etag
└── metadata.json              # Thời điểm, version app, alembic revision, mã băm SHA256
```

### 6.2. Chạy sao lưu thủ công
```bash
make backup
```
Mặc định bản backup lưu tại `/backups/`. Có thể tùy biến qua biến môi trường:
```bash
BACKUP_ROOT=/data/backups BACKUP_RETENTION_DAYS=30 ./deploy/scripts/backup.sh
```

### 6.3. Tự động hóa định kỳ (Cron Job)
Khuyến nghị thiết lập Cron job trên máy chủ chạy hàng ngày vào ban đêm (ví dụ 02:00 sáng):

```cron
# Mở crontab bằng: crontab -e
0 2 * * * cd /opt/hawee-tool-hub && /usr/bin/make backup >> /var/log/toolhub-backup.log 2>&1
```
*Script tự động dọn dẹp các bản backup cũ hơn số ngày retention quy định (mặc định 14 ngày).*

---

### 6.4. Quy trình Phục hồi Thảm họa (Restore Procedure)

> [!CAUTION]
> Thao tác phục hồi sẽ dừng các dịch vụ ghi và **GHI ĐÈ** toàn bộ cơ sở dữ liệu, kho git và object storage hiện tại bằng dữ liệu từ bản backup. Hãy thận trọng!

**Các bước thực hiện:**

1. Xác định thư mục backup cần khôi phục:
   ```bash
   ls -la /backups/
   ```

2. Thực thi lệnh restore:
   ```bash
   # Chạy với xác nhận tương tác:
   ./deploy/scripts/restore.sh /backups/2026-09-18_0200

   # Hoặc chạy không cần hỏi qua Make:
   make restore BACKUP=/backups/2026-09-18_0200
   ```

3. Quy trình thực hiện tự động của script:
   - **Bước 0**: Xác minh mã băm SHA256 của file `postgres.dump` dựa trên `metadata.json`.
   - **Bước 1**: Tạm dừng các dịch vụ ghi dữ liệu (`web`, `git-service`, `worker`, `backend`).
   - **Bước 2 (PostgreSQL)**: Ngắt kết nối active sessions, xóa và tạo lại database `toolhub`, phục hồi cấu trúc và dữ liệu bằng `pg_restore`.
   - **Bước 3 (Git)**: Xóa trắng volume `toolhub_git_repositories`, giải nén kho bare repo và phục hồi SSH host keys vào volume `toolhub_git_ssh`.
   - **Bước 4 (MinIO)**: Dùng công cụ `minio/mc` mirror đồng bộ lại các package wheels, source archives, build logs từ thư mục backup vào MinIO.
   - **Bước 5**: Khởi động lại toàn bộ stack và thực thi kiểm tra sức khỏe `healthcheck.sh`.

---

## 7. Mở rộng Kiến trúc Worker Tách biệt (Dedicated Build Runner)

Khi tần suất build tăng cao hoặc cần tăng cường an ninh bằng cách cô lập hoàn toàn môi trường chạy code tùy ý của developer khỏi cơ sở dữ liệu:

1. **Trên Server A (Core API & Storage)**:
   - Trong `deploy/.env`, cấu hình `RUNNER_TOKEN` ngẫu nhiên mạnh.
   - Không chạy service `worker` trên Server A: sửa compose hoặc chạy `docker compose stop worker`.

2. **Trên Server B (Dedicated Runner Server)**:
   - Cài đặt Docker Engine.
   - Sao chép thư mục `worker/` và `deploy/` sang Server B.
   - Tạo file cấu hình môi trường trỏ ngược về Server A:
     ```env
     TOOLHUB_API_URL=http://server-a.internal:8080/api/v1
     RUNNER_TOKEN=<RUNNER_TOKEN_CỦA_SERVER_A>
     RUNNER_NAME=runner-server-b
     BUILD_MAX_CONCURRENCY=4
     ```
   - Khởi chạy worker trên Server B:
     ```bash
     docker compose -f deploy/docker-compose-runner.yml up -d
     ```
   - Worker trên Server B sẽ gửi heartbeat định kỳ về Server A, nhận task, kéo source archive qua API và build trong container Docker cục bộ trên Server B mà không có bất kỳ quyền truy cập DB nào.

---

## 8. Xử lý Sự cố Triển khai (Troubleshooting)

| Vấn đề | Nguyên nhân khả dĩ | Hướng xử lý |
|---|---|---|
| Cổng 2222 bị từ chối kết nối (`Connection refused`) | Service `git-service` chưa khởi động hoặc xung đột cổng | Kiểm tra `docker compose ps git-service`. Kiểm tra log bằng `docker compose logs git-service`. Đảm bảo port 2222 không bị firewall chặn. |
| Container MinIO báo lỗi khi khởi động | Docker Hub chặn pull image MinIO | Repo đã chuyển sang dùng `quay.io/minio/minio`. Đảm bảo server mở firewall tới registry `quay.io`. |
| Lỗi build wheel `No space left on device` | Volume Docker bị đầy hoặc tmpfs tràn | Kiểm tra dung lượng ổ đĩa bằng `df -h`. Dọn dẹp images/containers rác bằng `docker system prune -f`. |
| Push git báo `Hook pre-receive failed` | API nội bộ không truy cập được từ container git | Kiểm tra mạng nội bộ giữa `git-service` và `backend`. Đảm bảo `INTERNAL_SERVICE_SECRET` trong `.env` đồng nhất. |
| pip install báo lỗi SSL hoặc 404 | Truy cập HTTP trên môi trường HTTPS | Đảm bảo cấu hình đúng `PACKAGE_BASE_URL`. Kiểm tra cấu hình reverse proxy Nginx trong `nginx/`. |
