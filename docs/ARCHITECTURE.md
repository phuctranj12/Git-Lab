# Kiến trúc HAWEE Tool Hub

## 1. Thành phần

```text
 Developer PC ── Browser ──────▶ web (Nginx :80/:443)  ── /api ──▶ backend (FastAPI :8000)
              ── pip ──────────▶ web (Nginx :8081)     ── /simple, /packages ──▶ backend
              ── git (SSH) ────▶ git-service (sshd :22 → host 2222)
                                    │ toolhub-keys / toolhub-git-auth / hooks ──HTTP nội bộ──▶ backend /api/v1/internal
                                    ▼
                               volume git_repositories (bare repo) ◀── backend (tạo repo, đọc tree/log, git archive)

 backend ──▶ PostgreSQL (metadata, quyền, audit) · Redis (hàng đợi Celery, rate limit, log build trực tiếp) · MinIO (package, log, artifact)
 backend ──Celery task "toolhub.build"──▶ Redis ──▶ worker ──Docker API──▶ container build tạm (mạng toolhub-build)
 worker ──HTTP (runner token)──▶ backend: start / source / log / publish / finish / heartbeat / maintenance
 container build ──pip──▶ registry:8081 (Nginx trên mạng build) ──▶ backend ──▶ PyPI (proxy/cache)
```

| Service (compose) | Vai trò | Port ra host |
|---|---|---|
| `web` | Nginx: SPA React + reverse proxy tools/packages; alias `registry` trên mạng build | 8080, 8081 (prod: 80, 443) |
| `backend` | FastAPI: REST API, Simple API, proxy PyPI, endpoint nội bộ | không |
| `migrate` | one-shot `python -m app.cli bootstrap` trước khi backend chạy | không |
| `worker` | Celery: điều phối build, heartbeat runner, job bảo trì (beat) | không |
| `builder` | chỉ build image `toolhub-builder` cho container build | không |
| `git-service` | OpenSSH user `git` + hook | 2222 |
| `postgres`, `redis`, `minio` | hạ tầng dữ liệu | không |

## 2. Quyết định thiết kế (và khác biệt so với spec/khung công ty)

| Chủ đề | Quyết định | Lý do |
|---|---|---|
| Frontend + Nginx | Gộp một service `web` (image Nginx chứa bản build React) | Theo khung HAWEE (fe = Nginx + SPA + proxy /api); bớt một container so với spec |
| Xác thực web | Username/email + mật khẩu **Argon2id** (spec); phiên bằng **cookie HttpOnly + SameSite=Strict**, access JWT HS256 15', refresh ngẫu nhiên lưu SHA-256, xoay vòng + phát hiện tái sử dụng (chuẩn `XAC_THUC_EMAIL_HAWEE.md`); CSRF double-submit | Không tự đăng ký bằng email: spec yêu cầu admin tạo user. `AuthProvider` sẵn chỗ cắm LDAP/OIDC (V2) |
| SSH key | `AuthorizedKeysCommand` hỏi API theo fingerprint thay vì sinh file `authorized_keys` | Thu hồi key/khoá user có hiệu lực ngay, không cần đồng bộ file |
| Quyền push theo ref | Thêm hook **pre-receive** gọi `/internal/git/authorize-push` | Spec: tag = Maintainer, nhánh mặc định có thể bảo vệ; tag đã publish là bất biến. Hook fail-closed |
| Nguồn cho build | Worker tải `git archive <commit>` qua API (runner token) | Đúng commit, không cần SSH key trên runner, chạy được khi runner ở host khác |
| Worker ↔ API | Worker chỉ gọi HTTP, không chạm DB | Tách được Server B (build) khỏi Server A (spec 14, 44) |
| Token build | Mỗi build một token tạm `read_package` gắn project, thu hồi khi xong | Container build cài dependency nội bộ mà không lộ credential hệ thống (spec 29.6) |
| Log build | Trực tiếp: Redis (append); kết thúc: MinIO `toolhub-build-logs` | UI xem log realtime; lưu lâu dài rẻ |
| Package type | Cột varchar (`PYPI`) thay vì enum PG; `package_versions.package_type` | Mở rộng npm/NuGet/Maven không phải ALTER TYPE (spec 1.2) |
| Proxy PyPI | Gọi Simple API JSON (PEP 691, fallback HTML), ETag, TTL 15', phục vụ bản cũ khi PyPI lỗi; host file theo allowlist | Nhanh, chống SSRF, chạy tiếp khi mất Internet |
| MinIO image | `quay.io/minio/minio` | MinIO đã ngừng phát hành image trên Docker Hub — firewall cần mở `quay.io` |
| MCP | Không tích hợp | Không cần cho dự án này |

## 3. Luồng push → build → release

1. `git push` → sshd gọi `toolhub-keys %u %f` → API trả dòng `command="toolhub-git-auth <key_id>",no-pty,…`.
2. `toolhub-git-auth` chỉ nhận `git-upload-pack`/`git-receive-pack`, hỏi `/internal/git/authorize` (đọc/ghi), rồi
   `exec` git với env `TOOLHUB_USER_ID`, `TOOLHUB_PROJECT_ID`.
3. `pre-receive` kiểm tra từng ref; `post-receive` gửi event (timeout 4s; lỗi → spool `/var/spool/toolhub`, tiến trình
   `toolhub-spool-flush` gửi lại mỗi 30s; `event_id` idempotent).
4. API tạo `builds` (số thứ tự theo project, khoá hàng project), commit rồi `send_task("toolhub.build")`.
   Job bảo trì 10 phút/lần gửi lại build QUEUED bị kẹt và đánh FAILED/RUNNER_ERROR build chạy quá hạn.
5. Worker: `start` (QUEUED→RUNNING nguyên tử, nhận token build) → tải source → kiểm tra pyproject (prefix,
   tên khớp project, tag == version) → tạo container (`cap_drop=ALL`, `no-new-privileges`, user 1000, CPU/RAM/PID
   limit, mạng `toolhub-build` internal) → `put_archive` source → stream log → timeout/cancel → lấy `/workspace/out`.
6. Trong container (`run_build.py`): pytest (nếu có `tests/`) → `python -m build` → `twine check` → đối chiếu
   tên/version trong wheel → venv sạch `pip install` wheel + `pip check` + import thử.
7. Release: worker `POST /internal/packages/publish` — API đọc METADATA **bên trong** wheel/sdist, kiểm tra
   prefix, version chưa tồn tại (unique DB), tính SHA256, lưu MinIO `internal/<name>/<version>/<file>`, ghi
   `package_versions`/`package_files`, cập nhật `latest_release_version`, audit.
8. Validation: artifact lưu `toolhub-artifacts` để tải kiểm tra. Worker `finish` → log vào MinIO, thu hồi token build.

## 4. Registry

| Request | Xử lý |
|---|---|
| `GET /simple/` | Danh sách package nội bộ user được xem |
| `GET /simple/<tên>/` | Tên khác dạng chuẩn PEP 503 → 301. `hawee-*` → **chỉ DB nội bộ**, không có → 404, KHÔNG gọi PyPI. Còn lại → proxy PyPI (HTML PEP 503 hoặc JSON PEP 691 theo `Accept`) với link rewrite `/packages/upstream/...#sha256=` |
| `GET /packages/internal/...` | Kiểm quyền theo project, stream MinIO, đếm lượt tải, audit `PACKAGE_DOWNLOADED` |
| `GET /packages/upstream/...` | Cache hit → MinIO (`X-Cache: CACHE`); miss → tải PyPI, verify SHA256, lưu MinIO, audit `UPSTREAM_PACKAGE_CACHED` |

Xác thực registry: Basic auth (`__token__` / access token có `read_package`) hoặc Bearer; mật khẩu đăng nhập không
dùng được cho pip. `REGISTRY_ALLOW_ANONYMOUS=true` cho phép đọc không token (chỉ trong mạng tin cậy).

## 5. Phân quyền

Cấp hiệu lực trên project = max(role trong group, role riêng của project) + Viewer ngầm định nếu **cả group và
project** đều `INTERNAL`. System Admin = cấp cao nhất. Token cá nhân bị giới hạn thêm bởi scope
(`read_api`/`write_api`/`read_package`/…; `admin` mới dùng được quyền admin qua token).

| Quyền | Viewer | Developer | Maintainer | Owner (group) | Admin |
|---|---|---|---|---|---|
| Xem project, clone, tải package | ✓ | ✓ | ✓ | ✓ | ✓ |
| Xem log build | | ✓ | ✓ | ✓ | ✓ |
| Push nhánh, chạy build | | ✓ | ✓ | ✓ | ✓ |
| Push nhánh mặc định khi bật bảo vệ | | | ✓ | ✓ | ✓ |
| Push tag (release), yank, huỷ build, token CI, cài đặt, thành viên project | | | ✓ | ✓ | ✓ |
| Archive project, quản lý thành viên group, tạo project | | | | ✓ | ✓ |
| Tạo group, quản lý user, audit, runner, xoá project chưa publish | | | | | ✓ |

## 6. Dữ liệu

Bảng (Alembic `0001`): `users`, `refresh_tokens`, `groups`, `group_members`, `projects`, `project_members`,
`ssh_keys`, `access_tokens`, `builds`, `git_push_events`, `package_versions`, `package_files`, `upstream_packages`,
`upstream_files`, `audit_logs` (trigger DB chặn UPDATE/DELETE), `runner_nodes`.

Bổ sung so với spec: `builds.number/steps_json/artifacts_json`, `projects.protect_default_branch/last_build_status/
build_counter`, `package_versions.package_type/download_count/yanked_at/yanked_by`, `access_tokens.build_id/created_by`,
`audit_logs.actor_name/request_id`, `users.auth_provider`, bảng `refresh_tokens`, `git_push_events`.

## 7. Quan sát

- Log JSON một dòng/sự kiện (`ts, level, request_id, user_id, action, …`), che `password/token/secret/authorization`.
- Mọi response có `X-Request-ID`; lỗi trả `{"error": {"code", "message", "request_id"}}` với mã chuẩn spec mục 42.
- `/metrics` (Prometheus, không expose qua Nginx): `http_requests_total`, `build_duration_seconds`,
  `build_queue_depth`, `package_downloads_total`, `upstream_cache_hits_total`, …
- `/health/live`, `/health/ready` (PostgreSQL, Redis, MinIO, Git storage — không phụ thuộc PyPI).
