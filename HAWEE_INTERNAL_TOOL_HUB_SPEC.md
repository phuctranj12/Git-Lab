# HAWEE INTERNAL TOOL HUB
## Đặc tả kiến trúc + yêu cầu triển khai V1 Production-Ready

> Mục tiêu: xây dựng một nền tảng nội bộ chạy trên server công ty để developer có thể:
>
> 1. tạo project/tool;
> 2. push source code bằng Git chuẩn;
> 3. hệ thống tự nhận sự kiện push/tag;
> 4. chạy build/test trong môi trường cô lập;
> 5. đóng gói Python thành `.whl` / `.tar.gz`;
> 6. publish package version vào Package Registry nội bộ;
> 7. cung cấp một PyPI-compatible endpoint duy nhất vừa chứa package nội bộ vừa proxy/cache package public từ PyPI;
> 8. cho project khác chỉ cần `pip install ...`;
> 9. quản lý user, group, project, quyền, version, build log, audit;
> 10. có thể triển khai hoàn toàn trên server nội bộ công ty.

---

# 1. Phạm vi triển khai

## 1.1. V1 bắt buộc

V1 phải hoàn chỉnh cho **Python package** trước.

Các chức năng bắt buộc:

- Web UI quản lý Tool/Project.
- Đăng nhập.
- User, Group, Membership.
- Project/Tool.
- Git repository nội bộ.
- SSH key cho Git.
- `git clone`, `git pull`, `git push`.
- Nhận Git push/tag event.
- Build queue.
- Build worker.
- Build Python package bằng `python -m build`.
- Kiểm tra package bằng `twine check`.
- Test cài `.whl` trong environment sạch.
- Package Registry nội bộ.
- Version theo Git tag.
- PyPI Simple API tương thích `pip`.
- Proxy/cache package public từ `pypi.org`.
- Namespace nội bộ bắt buộc theo prefix `hawee-`.
- `pip install` chỉ cần dùng **một index URL duy nhất**.
- Build log.
- Audit log.
- Personal Access Token.
- Service/CI token.
- Role-based access control.
- Dashboard và catalogue tool.
- Docker Compose để test/staging.
- Nginx reverse proxy.
- PostgreSQL.
- Redis.
- MinIO.
- Alembic migration.
- Automated tests.
- Healthcheck.
- Backup script.
- `.env.example`.
- README triển khai local và server.

## 1.2. Không làm ở V1

Không tự viết lại Git.

Không tự implement:

- thuật toán commit;
- branch storage;
- packfile;
- Git protocol từ đầu.

Phải sử dụng **Git binary chuẩn** (`git`) và **bare repository**.

Chưa bắt buộc:

- Merge Request kiểu GitLab;
- issue tracker;
- wiki;
- Kubernetes;
- container registry;
- code scanning nâng cao;
- Maven;
- NuGet;
- npm.

Tuy nhiên thiết kế DB/API phải không khóa khả năng mở rộng các package type này sau này.

---

# 2. Nguyên tắc thiết kế

## 2.1. Git là source of truth cho source code

Nền tảng chỉ quản lý metadata và quyền.

Ví dụ repository vật lý:

```text
/data/git/repositories/
├── group-a/
│   ├── boc-tach-ban-ve.git/
│   ├── pdf-parser.git/
│   └── contract-parser.git/
└── group-b/
    └── dms-client.git/
```

Mỗi repository là bare repository:

```bash
git init --bare /data/git/repositories/group-a/boc-tach-ban-ve.git
```

## 2.2. Release package không xảy ra trên mọi push

Quy ước:

```text
push main
→ validate source
→ test
→ build thử
→ KHÔNG publish stable package

push tag vX.Y.Z
→ validate
→ test
→ build
→ kiểm tra version
→ publish package X.Y.Z
```

Ví dụ:

```bash
git tag v1.4.0
git push origin v1.4.0
```

Hệ thống phải kiểm tra:

```toml
[project]
name = "hawee-boc-tach-ban-ve"
version = "1.4.0"
```

Tag `v1.4.0` phải khớp với version `1.4.0` trong `pyproject.toml`.

Nếu không khớp:

```text
BUILD FAILED
VERSION_MISMATCH
```

và tuyệt đối không publish.

## 2.3. Package nội bộ bắt buộc có prefix

Tất cả package nội bộ phải có tên:

```text
hawee-*
```

Ví dụ:

```text
hawee-boc-tach-ban-ve
hawee-pdf-parser
hawee-contract-parser
hawee-dms-client
```

Mục đích:

- tránh trùng package public;
- chống dependency confusion;
- dễ nhận biết package nội bộ;
- dễ audit;
- dễ áp policy.

Registry phải có rule:

```text
Nếu package name bắt đầu bằng "hawee-"
→ chỉ tìm trong INTERNAL REGISTRY
→ KHÔNG BAO GIỜ fallback ra PyPI.
```

Đây là yêu cầu bảo mật bắt buộc.

---

# 3. Kiến trúc tổng thể

```text
                         HAWEE INTERNAL NETWORK

┌──────────────────────────────────────────────────────────────────────┐
│                                                                      │
│  Developer PC                                                       │
│                                                                      │
│  Browser ───────────────┐                                            │
│  Git CLI ────────────┐  │                                            │
│  pip ─────────────┐  │  │                                            │
│                   │  │  │                                            │
└───────────────────┼──┼──┼────────────────────────────────────────────┘
                    │  │  │
                    ▼  ▼  ▼

              ┌──────────────────────┐
              │       NGINX          │
              │ reverse proxy + TLS  │
              └──────────┬───────────┘
                         │
          ┌──────────────┼─────────────────────────┐
          │              │                         │
          ▼              ▼                         ▼
 tools.hawee.local   packages.hawee.local    git.hawee.local
          │              │                         │
          ▼              ▼                         ▼
      React UI       Registry API             Git SSH ServiceBắt đầu làm 
          │              │                         │
          └───────┬──────┘                         │
                  ▼                                │
              FastAPI API ◄────────────────────────┘
                  │
      ┌───────────┼─────────────┬──────────────────┐
      │           │             │                  │
      ▼           ▼             ▼                  ▼
 PostgreSQL     Redis       MinIO/S3          Git Repositories
 metadata       queue       package files      bare repos
                  │
                  ▼
             Build Worker
                  │
                  ▼
       Ephemeral Build Container
                  │
        python -m build / tests
                  │
                  ▼
            Internal Registry
                  │
                  ├──────── Internal packages
                  │
                  └──────── PyPI proxy/cache
```

---

# 4. Domain / endpoint đề xuất

Production:

```text
https://tools.hawee.local
https://packages.hawee.local
git@git.hawee.local:<group>/<project>.git
```

Nếu DNS nội bộ chưa có, staging có thể dùng:

```text
http://SERVER_IP:8080
http://SERVER_IP:8081
ssh://git@SERVER_IP:2222/<group>/<project>.git
```

Khuyến nghị production phải dùng DNS nội bộ và HTTPS.

---

# 5. Tech stack

## Backend

```text
Python 3.11+
FastAPI
SQLAlchemy 2.x
Alembic
Pydantic Settings
PyJWT
Argon2 password hashing
httpx
boto3/minio client
Celery
Redis
```

## Frontend

```text
React
Vite
TypeScript
React Router
TanStack Query
```

Không cần UI framework bắt buộc; có thể dùng Ant Design hoặc MUI nếu muốn.

## Database

```text
PostgreSQL 16+
```

## Queue

```text
Redis
Celery
```

## Object storage

```text
MinIO
```

Buckets:

```text
toolhub-packages
toolhub-build-logs
toolhub-artifacts
toolhub-backups
```

## Git

```text
system git binary
bare repositories
git-shell / custom SSH command wrapper
post-receive hooks
```

## Reverse proxy

```text
Nginx
```

## Container runtime

Local/staging:

```text
Docker
Docker Compose
```

Production runner khuyến nghị:

```text
Dedicated build worker host
Docker rootless hoặc Podman rootless
```

---

# 6. Cấu trúc source code

```text
hawee-tool-hub/
│
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── core/
│   │   │   ├── config.py
│   │   │   ├── security.py
│   │   │   ├── permissions.py
│   │   │   └── logging.py
│   │   ├── db/
│   │   │   ├── base.py
│   │   │   ├── session.py
│   │   │   └── models/
│   │   ├── schemas/
│   │   ├── api/
│   │   │   └── v1/
│   │   ├── services/
│   │   │   ├── auth_service.py
│   │   │   ├── git_service.py
│   │   │   ├── project_service.py
│   │   │   ├── build_service.py
│   │   │   ├── package_service.py
│   │   │   ├── registry_service.py
│   │   │   ├── pypi_proxy_service.py
│   │   │   └── audit_service.py
│   │   ├── repositories/
│   │   └── utils/
│   ├── alembic/
│   ├── tests/
│   └── requirements.txt
│
├── worker/
│   ├── app/
│   │   ├── celery_app.py
│   │   ├── tasks/
│   │   │   ├── build_python.py
│   │   │   ├── proxy_download.py
│   │   │   └── cleanup.py
│   │   └── runners/
│   │       └── python_runner.py
│   ├── Dockerfile
│   └── requirements.txt
│
├── git-service/
│   ├── bin/
│   │   ├── toolhub-git-auth
│   │   ├── create-repository
│   │   └── install-hook
│   ├── hooks/
│   │   └── post-receive
│   └── Dockerfile
│
├── frontend/
│   ├── src/
│   ├── public/
│   ├── package.json
│   └── vite.config.ts
│
├── nginx/
│   └── nginx.conf
│
├── deploy/
│   ├── docker-compose.yml
│   ├── docker-compose.prod.yml
│   ├── .env.example
│   └── scripts/
│       ├── init.sh
│       ├── backup.sh
│       ├── restore.sh
│       └── healthcheck.sh
│
├── scripts/
│   ├── bootstrap-admin.py
│   ├── seed-demo.py
│   └── run-tests.sh
│
├── docs/
│   ├── ARCHITECTURE.md
│   ├── DEPLOYMENT.md
│   ├── SECURITY.md
│   └── USER_GUIDE.md
│
├── Makefile
├── README.md
└── .gitignore
```

---

# 7. Mô hình dữ liệu

Tất cả bảng dùng:

```text
id UUID
created_at timestamptz
updated_at timestamptz
```

trừ trường hợp đặc biệt.

---

## 7.1. users

```text
users
- id UUID PK
- username varchar(100) UNIQUE NOT NULL
- email varchar(255) UNIQUE NOT NULL
- full_name varchar(255)
- password_hash text NOT NULL
- is_active boolean default true
- is_system_admin boolean default false
- last_login_at timestamptz
- created_at
- updated_at
```

Index:

```text
ux_users_username
ux_users_email
```

---

## 7.2. groups

```text
groups
- id UUID PK
- name varchar(255)
- slug varchar(100) UNIQUE
- description text
- visibility enum:
    PRIVATE
    INTERNAL
- created_by UUID FK users
- created_at
- updated_at
```

---

## 7.3. group_members

```text
group_members
- id UUID PK
- group_id UUID FK groups
- user_id UUID FK users
- role enum:
    VIEWER
    DEVELOPER
    MAINTAINER
    OWNER
- created_at
```

Constraint:

```text
UNIQUE(group_id, user_id)
```

---

## 7.4. projects

```text
projects
- id UUID PK
- group_id UUID FK groups
- name varchar(255)
- slug varchar(100)
- description text
- language enum:
    PYTHON
    GENERIC
- visibility enum:
    PRIVATE
    INTERNAL
- default_branch varchar(100) default 'main'
- package_name varchar(255)
- package_prefix_valid boolean
- repo_path text
- repository_size_bytes bigint default 0
- latest_commit_sha varchar(64)
- latest_release_version varchar(100)
- latest_release_build_id UUID nullable
- archived boolean default false
- created_by UUID FK users
- created_at
- updated_at
```

Constraints:

```text
UNIQUE(group_id, slug)
UNIQUE(package_name) WHERE archived = false
```

Policy:

```text
package_name LIKE 'hawee-%'
```

cho project Python publish package.

---

## 7.5. project_members

Chỉ dùng nếu cần override quyền từ group.

```text
project_members
- id UUID PK
- project_id UUID FK projects
- user_id UUID FK users
- role enum:
    VIEWER
    DEVELOPER
    MAINTAINER
- created_at
```

---

## 7.6. ssh_keys

```text
ssh_keys
- id UUID PK
- user_id UUID FK users
- title varchar(255)
- fingerprint varchar(255) UNIQUE
- public_key text NOT NULL
- key_type varchar(50)
- last_used_at timestamptz
- revoked_at timestamptz nullable
- created_at
```

Không lưu private key.

---

## 7.7. access_tokens

```text
access_tokens
- id UUID PK
- user_id UUID nullable
- project_id UUID nullable
- name varchar(255)
- token_prefix varchar(20)
- token_hash text
- token_type enum:
    PERSONAL
    SERVICE
    RUNNER
- scopes jsonb
- expires_at timestamptz nullable
- revoked_at timestamptz nullable
- last_used_at timestamptz nullable
- created_at
```

Không lưu token plaintext.

Chỉ hiển thị token plaintext đúng **một lần khi tạo**.

Scope:

```text
read_api
write_api
read_repository
write_repository
read_package
write_package
admin
```

---

## 7.8. builds

```text
builds
- id UUID PK
- project_id UUID FK projects
- trigger_type enum:
    PUSH
    TAG
    MANUAL
- ref_name varchar(255)
- commit_sha varchar(64)
- requested_version varchar(100) nullable
- status enum:
    QUEUED
    RUNNING
    SUCCESS
    FAILED
    CANCELLED
- publish_package boolean default false
- runner_id UUID nullable
- started_at timestamptz nullable
- finished_at timestamptz nullable
- duration_seconds integer nullable
- error_code varchar(100) nullable
- error_message text nullable
- log_object_key text nullable
- created_by UUID nullable
- created_at
```

Index:

```text
(project_id, created_at desc)
(status)
(commit_sha)
```

---

## 7.9. package_versions

```text
package_versions
- id UUID PK
- project_id UUID FK projects
- package_name varchar(255)
- version varchar(100)
- normalized_name varchar(255)
- build_id UUID FK builds
- commit_sha varchar(64)
- git_tag varchar(255)
- is_yanked boolean default false
- yanked_reason text nullable
- metadata_json jsonb
- created_at
```

Constraint:

```text
UNIQUE(normalized_name, version)
```

Package đã publish không được overwrite cùng version.

---

## 7.10. package_files

```text
package_files
- id UUID PK
- package_version_id UUID FK package_versions
- filename varchar(500)
- file_type enum:
    WHEEL
    SDIST
- object_key text
- size_bytes bigint
- sha256 varchar(64)
- python_requires varchar(100) nullable
- uploaded_at timestamptz
```

---

## 7.11. upstream_packages

Cache metadata package public.

```text
upstream_packages
- id UUID PK
- normalized_name varchar(255) UNIQUE
- source enum:
    PYPI
- upstream_etag varchar(255) nullable
- last_checked_at timestamptz
- expires_at timestamptz
- raw_metadata_json jsonb nullable
```

---

## 7.12. upstream_files

```text
upstream_files
- id UUID PK
- upstream_package_id UUID FK upstream_packages
- filename varchar(500)
- version varchar(100) nullable
- upstream_url text
- object_key text nullable
- sha256 varchar(64) nullable
- size_bytes bigint nullable
- cached boolean default false
- cached_at timestamptz nullable
- last_accessed_at timestamptz nullable
```

Unique:

```text
UNIQUE(upstream_package_id, filename)
```

---

## 7.13. audit_logs

```text
audit_logs
- id UUID PK
- user_id UUID nullable
- actor_type enum:
    USER
    SERVICE
    SYSTEM
- action varchar(100)
- resource_type varchar(100)
- resource_id varchar(255)
- ip_address inet nullable
- user_agent text nullable
- metadata_json jsonb
- created_at
```

Các action bắt buộc audit:

```text
LOGIN
LOGIN_FAILED
CREATE_USER
DISABLE_USER
CREATE_GROUP
ADD_GROUP_MEMBER
REMOVE_GROUP_MEMBER
CREATE_PROJECT
DELETE_PROJECT
ARCHIVE_PROJECT
ADD_SSH_KEY
REMOVE_SSH_KEY
CREATE_TOKEN
REVOKE_TOKEN
GIT_PUSH
BUILD_CREATED
BUILD_STARTED
BUILD_FAILED
BUILD_SUCCESS
PACKAGE_PUBLISHED
PACKAGE_YANKED
PACKAGE_DOWNLOADED
UPSTREAM_PACKAGE_CACHED
```

---

## 7.14. runner_nodes

```text
runner_nodes
- id UUID PK
- name varchar(255)
- hostname varchar(255)
- status enum:
    ONLINE
    OFFLINE
    DRAINING
- labels jsonb
- last_heartbeat_at timestamptz
- max_concurrent_jobs integer
- current_jobs integer
- created_at
```

---

# 8. Phân quyền

## 8.1. System Admin

Có quyền:

- quản lý toàn bộ user;
- disable user;
- tạo/xóa group;
- xem audit;
- xem system health;
- quản lý runner;
- quản lý system settings;
- xem tất cả project.

Không dùng tài khoản admin để build.

---

## 8.2. Group Owner

Có quyền:

- quản lý group;
- thêm/xóa thành viên;
- đổi role;
- tạo project;
- archive project;
- quản lý project trong group.

---

## 8.3. Maintainer

Có quyền:

- push source;
- tạo tag;
- manual build;
- publish release;
- yank package;
- quản lý project token;
- xem toàn bộ build log.

---

## 8.4. Developer

Có quyền:

- clone/pull;
- push branch;
- push main nếu project policy cho phép;
- chạy build;
- xem build log;
- tải package.

Mặc định Developer **không được**:

- xóa project;
- xóa package version;
- quản lý user;
- tạo project access token quyền write_package nếu không được Maintainer cấp.

---

## 8.5. Viewer

Có quyền:

- xem catalogue;
- xem project;
- clone/pull nếu visibility cho phép;
- tải package nếu có quyền.

Không push.

---

# 9. Git Authentication

## 9.1. SSH user duy nhất

Server dùng Linux account:

```text
git
```

Developer clone:

```bash
git clone git@git.hawee.local:hawee-tools/boc-tach-ban-ve.git
```

## 9.2. SSH key

User upload public key qua Web UI.

Ví dụ:

```text
ssh-ed25519 AAAAC3... phuc@pc
```

App lưu fingerprint và public key.

Authorized keys được sinh theo dạng:

```text
command="/opt/toolhub/bin/toolhub-git-auth <ssh_key_id>",
no-port-forwarding,
no-X11-forwarding,
no-agent-forwarding,
no-pty
ssh-ed25519 AAAA...
```

`toolhub-git-auth` phải:

1. xác định user từ `ssh_key_id`;
2. đọc `SSH_ORIGINAL_COMMAND`;
3. chỉ cho phép:
   - `git-upload-pack`;
   - `git-receive-pack`;
4. parse repository path;
5. gọi API nội bộ kiểm tra quyền;
6. nếu hợp lệ thì `exec` Git command;
7. nếu không thì exit non-zero.

Tuyệt đối không cho shell tương tác.

---

# 10. Git repository lifecycle

Khi tạo project:

```text
POST /api/v1/projects
```

Backend:

1. validate slug;
2. validate package prefix;
3. insert DB;
4. tạo folder;
5. chạy:

```bash
git init --bare <repo_path>
```

6. install `post-receive` hook;
7. set ownership;
8. trả clone URL.

---

# 11. Git post-receive hook

Hook không được build trực tiếp.

Hook chỉ gửi event.

Flow:

```text
git push
    ↓
bare repo update
    ↓
post-receive
    ↓
POST internal API
    ↓
create build/event
    ↓
queue
```

Payload:

```json
{
  "project_id": "uuid",
  "repository_path": "hawee-tools/boc-tach-ban-ve",
  "updates": [
    {
      "old_sha": "...",
      "new_sha": "...",
      "ref": "refs/heads/main"
    }
  ]
}
```

hoặc:

```json
{
  "ref": "refs/tags/v1.4.0"
}
```

Hook auth bằng internal service token.

Hook phải timeout ngắn, ví dụ 3-5 giây.

Nếu backend tạm thời unavailable:

- ghi event vào local spool file;
- background retry;
- không làm hỏng Git repository.

---

# 12. Build pipeline

## 12.1. Push branch

```text
git push origin main
      ↓
PUSH EVENT
      ↓
Build type = VALIDATION
      ↓
clone commit
      ↓
detect Python project
      ↓
install build dependencies
      ↓
run tests nếu có
      ↓
python -m build
      ↓
twine check
      ↓
test install wheel
      ↓
SUCCESS / FAILED
      ↓
KHÔNG publish stable package
```

## 12.2. Push tag

```text
git push origin v1.4.0
      ↓
TAG EVENT
      ↓
Build type = RELEASE
      ↓
clone exact commit
      ↓
read pyproject.toml
      ↓
package name must start with hawee-
      ↓
tag version == project.version
      ↓
tests
      ↓
python -m build
      ↓
twine check
      ↓
fresh install test
      ↓
publish internal package
      ↓
update latest_release_version
```

---

# 13. Python build contract

Mọi project Python phải có:

```text
pyproject.toml
README.md
```

Khuyến nghị:

```text
src/
  package_name/
```

Ví dụ:

```text
boc-tach-ban-ve/
├── pyproject.toml
├── README.md
├── src/
│   └── hawee_boc_tach_ban_ve/
│       ├── __init__.py
│       └── ...
└── tests/
```

`pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "hawee-boc-tach-ban-ve"
version = "1.4.0"
requires-python = ">=3.10"
dependencies = [
    "pydantic>=2",
]
```

---

# 14. Build isolation

Build không được chạy trực tiếp trong backend container.

Mỗi build phải chạy trong ephemeral container.

Pseudo:

```text
worker
  ↓
create temp workspace
  ↓
git clone exact commit
  ↓
docker run --rm
  - read-only where possible
  - no privileged
  - CPU limit
  - memory limit
  - timeout
  - package index = internal registry
  ↓
collect dist/
  ↓
destroy container
```

Production recommendation:

```text
API server != build runner host
```

Staging có thể cùng host.

Production nên tách:

```text
Server A:
- UI
- API
- PostgreSQL
- Redis
- MinIO
- Git

Server B:
- Build Worker
- Container runtime
```

---

# 15. Default build limits

Mặc định:

```text
CPU: 2 cores/job
RAM: 4 GB/job
Timeout: 15 minutes
Disk workspace: 5 GB/job
Max concurrent builds staging: 2
Max concurrent builds production: configurable
```

Không cho:

```text
--privileged
host network
mount /
mount docker socket inside user build container
```

---

# 16. Package Registry nội bộ

## 16.1. Mục tiêu

Developer chỉ cấu hình:

```text
https://packages.hawee.local/simple
```

sau đó:

```bash
pip install numpy
pip install pandas
pip install hawee-boc-tach-ban-ve
```

đều qua cùng registry.

---

# 17. PyPI Simple API

Phải hỗ trợ tối thiểu PEP 503 style HTML endpoint.

## 17.1. Root

```http
GET /simple/
```

Không bắt buộc list toàn bộ public PyPI package.

Có thể chỉ list internal package.

## 17.2. Package

```http
GET /simple/{normalized-package-name}/
```

Normalize theo Python package name rules:

```text
lowercase
run of [-_.] → -
```

Ví dụ:

```text
Hawee_PDF.Parser
→ hawee-pdf-parser
```

---

# 18. Internal package resolution

Nếu:

```text
normalized_name.startswith("hawee-")
```

thì:

```text
chỉ query DB internal package
không gọi pypi.org
```

Nếu không tồn tại:

```http
404
```

Không fallback ra upstream.

---

# 19. Public package proxy/cache

Ví dụ:

```bash
pip install numpy
```

Request:

```text
GET /simple/numpy/
```

Flow:

```text
Registry
  ↓
check metadata cache
  ↓
expired / missing?
  ↓ yes
GET https://pypi.org/simple/numpy/
  ↓
parse upstream file links
  ↓
rewrite link về packages.hawee.local
  ↓
return PEP503 HTML
```

Ví dụ rewritten link:

```text
/packages/upstream/numpy/numpy-2.x.x.whl?source=<id>
```

Khi pip tải:

```text
GET /packages/upstream/...
```

backend:

```text
cache hit?
  ↓ yes → MinIO
  ↓ no
download from PyPI
verify hash
store MinIO
store metadata
stream response
```

Lần sau dùng cache.

---

# 20. Cache policy

Default:

```text
Simple metadata TTL: 15 phút
Package binary TTL: không tự xóa
```

Binary package đã cache được coi là immutable.

Có cleanup policy tùy chọn:

```text
xoá upstream binary:
- không truy cập > 180 ngày
- và storage vượt threshold
```

Internal package không auto-delete.

---

# 21. Dependency confusion policy

Bắt buộc:

1. Internal package prefix `hawee-`.
2. Prefix này không proxy ra PyPI.
3. Package publish phải reject tên không đúng prefix.
4. Project admin không được tắt rule này ở V1.
5. Audit mọi package publication.
6. Package version không overwrite.
7. SHA256 cho mọi artifact.

---

# 22. Package file storage

MinIO key:

```text
internal/{normalized_name}/{version}/{filename}

upstream/{normalized_name}/{filename}
```

Ví dụ:

```text
internal/hawee-pdf-parser/1.4.0/hawee_pdf_parser-1.4.0-py3-none-any.whl

upstream/numpy/numpy-2.1.0-...
```

---

# 23. Package publication

Worker upload package bằng internal API:

```http
POST /api/v1/internal/packages/publish
Authorization: Bearer <runner-service-token>
```

Payload metadata:

```text
project_id
build_id
package_name
version
commit_sha
git_tag
```

Files:

```text
*.whl
*.tar.gz
```

Backend validate:

- build SUCCESS;
- token scope write_package;
- package belongs project;
- package name valid;
- version chưa tồn tại;
- SHA256;
- artifact type hợp lệ.

Sau đó:

1. upload MinIO;
2. insert package_versions;
3. insert package_files;
4. update project.latest_release_version;
5. audit.

---

# 24. pip configuration

## 24.1. Developer machine

Cấu hình một lần:

```bash
pip config set global.index-url https://packages.hawee.local/simple
```

Nếu cần auth:

```text
Một user chỉ có MỘT token read_package dùng cho tất cả package họ có quyền.
```

Không tạo token theo từng tool.

## 24.2. requirements.txt

Không ghi URL package riêng.

Ví dụ:

```text
numpy==2.1.0
pandas==2.2.3

hawee-pdf-parser==1.4.0
hawee-contract-parser==2.0.3
```

Chạy:

```bash
pip install -r requirements.txt
```

---

# 25. Package authentication

V1 hỗ trợ:

```text
Bearer/access token qua Basic Auth-compatible URL
```

Khuyến nghị cho developer:

- Personal Access Token;
- scope `read_package`;
- một token dùng chung toàn bộ registry theo permission.

Không lưu raw token trong DB.

Cho CI:

```text
Service token
scope = read_package
```

Không commit token vào source.

---

# 26. Web UI

## 26.1. Login

```text
Username
Password
```

Future:

```text
LDAP / AD / OIDC
```

V1 thiết kế auth service để có thể thêm provider sau.

---

## 26.2. Dashboard

Hiển thị:

```text
My Projects
My Groups
Recent Builds
Recent Releases
Failed Builds
Popular Internal Packages
```

---

## 26.3. Tool Catalogue

Card:

```text
┌────────────────────────────────────┐
│ Bóc tách bản vẽ                    │
│                                    │
│ Package: hawee-boc-tach-ban-ve     │
│ Latest: 1.4.0                      │
│ Python: >=3.10                     │
│ Build: SUCCESS                     │
│ Owner: AI Team                     │
│                                    │
│ pip install hawee-boc-tach-ban-ve  │
│                                    │
│ [Source] [Versions] [Builds]       │
└────────────────────────────────────┘
```

Search:

```text
name
package name
description
owner/group
```

---

## 26.4. Project Detail

Tabs:

```text
Overview
Repository
Versions
Builds
Packages
Members
Settings
```

Overview:

```text
Clone URL
Latest commit
Latest release
Install command
Description
README
```

---

## 26.5. Builds

Danh sách:

```text
#842
tag v1.4.0
SUCCESS
commit 7ac98ef
2m31s
```

Build detail:

```text
trigger
commit
ref
runner
status
timestamps
console log
artifacts
error
```

---

## 26.6. Versions

```text
1.4.0
1.3.2
1.3.1
```

Mỗi version:

```text
git tag
commit SHA
build
publish time
files
SHA256
download
yank status
```

Không cho delete cứng package version trong UI thông thường.

Chỉ hỗ trợ:

```text
YANK
```

Admin có thể hard-delete bằng maintenance flow đặc biệt có audit.

---

# 27. REST API

Prefix:

```text
/api/v1
```

## Auth

```text
POST   /auth/login
POST   /auth/refresh
POST   /auth/logout
GET    /auth/me
```

## Users

```text
GET    /users
POST   /users
GET    /users/{id}
PATCH  /users/{id}
POST   /users/{id}/disable
```

## SSH keys

```text
GET    /me/ssh-keys
POST   /me/ssh-keys
DELETE /me/ssh-keys/{id}
```

## Tokens

```text
GET    /me/tokens
POST   /me/tokens
DELETE /me/tokens/{id}
```

## Groups

```text
GET    /groups
POST   /groups
GET    /groups/{slug}
PATCH  /groups/{slug}

GET    /groups/{slug}/members
POST   /groups/{slug}/members
PATCH  /groups/{slug}/members/{user_id}
DELETE /groups/{slug}/members/{user_id}
```

## Projects

```text
GET    /projects
POST   /projects
GET    /projects/{group}/{project}
PATCH  /projects/{group}/{project}
POST   /projects/{group}/{project}/archive
```

## Builds

```text
GET    /projects/{group}/{project}/builds
GET    /builds/{id}
POST   /builds/{id}/cancel
POST   /projects/{group}/{project}/builds/manual
```

## Packages

```text
GET    /projects/{group}/{project}/packages
GET    /packages/{name}
GET    /packages/{name}/{version}
POST   /packages/{name}/{version}/yank
```

## Internal events

```text
POST   /internal/git/events
POST   /internal/runner/heartbeat
POST   /internal/packages/publish
```

Internal endpoints không expose ra public network.

---

# 28. Health endpoints

```text
GET /health/live
GET /health/ready
```

Readiness check:

```text
PostgreSQL
Redis
MinIO
Git storage writable
```

Không bắt buộc PyPI upstream phải available để API ready.

Nếu PyPI down:

- internal package vẫn phải hoạt động;
- cached upstream package vẫn hoạt động;
- uncached external package trả lỗi upstream rõ ràng.

---

# 29. Security

## 29.1. Password

Hash:

```text
Argon2id
```

Không dùng plaintext.

Không log password/token.

---

## 29.2. JWT

Access token ngắn:

```text
15 phút
```

Refresh token:

```text
7 ngày
```

Refresh token lưu dạng hash.

---

## 29.3. CSRF/XSS

Nếu dùng cookie auth:

- HttpOnly;
- Secure;
- SameSite;
- CSRF protection.

Nếu frontend dùng Bearer access token, tránh localStorage nếu có thể.

---

## 29.4. Rate limiting

Login:

```text
5 lần/phút/IP
```

API:

```text
configurable
```

Registry download có rate limit rộng hơn.

---

## 29.5. File validation

Package upload:

- chỉ `.whl`, `.tar.gz`;
- filename validation;
- max size configurable;
- checksum SHA256;
- không trust metadata từ client.

---

## 29.6. Build security

Không:

```text
privileged container
host network
host PID
mount host root
mount PostgreSQL volume
mount Git private keys
```

Secrets build chỉ inject theo policy.

Không tự động expose system service credentials cho code build.

---

# 30. Audit policy

Audit log là append-only ở cấp app.

Không expose endpoint delete audit.

Retention:

```text
>= 365 ngày
```

Có API export CSV/JSON cho admin.

---

# 31. Logging

Backend log JSON structured:

```json
{
  "ts": "...",
  "level": "INFO",
  "request_id": "...",
  "user_id": "...",
  "action": "...",
  "message": "..."
}
```

Mỗi request có:

```text
X-Request-ID
```

Build có:

```text
build_id
```

Không log secret.

---

# 32. Docker Compose services

Staging compose:

```text
nginx
frontend
backend
worker
postgres
redis
minio
git-service
```

Optional:

```text
minio-init
migration
```

Persistent volumes:

```text
postgres_data
redis_data
minio_data
git_repositories
```

---

# 33. Environment variables

`.env.example`:

```dotenv
APP_ENV=production
APP_NAME=HAWEE Tool Hub

WEB_BASE_URL=https://tools.hawee.local
PACKAGE_BASE_URL=https://packages.hawee.local
GIT_SSH_HOST=git.hawee.local
GIT_SSH_PORT=22

DATABASE_URL=postgresql+psycopg://toolhub:CHANGE_ME@postgres:5432/toolhub
REDIS_URL=redis://redis:6379/0

JWT_SECRET=CHANGE_ME
INTERNAL_SERVICE_SECRET=CHANGE_ME

MINIO_ENDPOINT=minio:9000
MINIO_ACCESS_KEY=CHANGE_ME
MINIO_SECRET_KEY=CHANGE_ME
MINIO_SECURE=false
MINIO_BUCKET_PACKAGES=toolhub-packages
MINIO_BUCKET_LOGS=toolhub-build-logs

GIT_REPOSITORY_ROOT=/data/git/repositories

INTERNAL_PACKAGE_PREFIX=hawee-

PYPI_UPSTREAM_SIMPLE_URL=https://pypi.org/simple
PYPI_METADATA_TTL_SECONDS=900

BUILD_MAX_CONCURRENCY=2
BUILD_TIMEOUT_SECONDS=900
BUILD_MEMORY_LIMIT=4g
BUILD_CPU_LIMIT=2

PACKAGE_MAX_UPLOAD_MB=500

FIRST_ADMIN_USERNAME=admin
FIRST_ADMIN_EMAIL=admin@hawee.local
```

Không commit file `.env`.

---

# 34. Database migration

Phải dùng Alembic.

Command:

```bash
alembic upgrade head
```

Không dùng:

```python
Base.metadata.create_all()
```

trong production boot path.

---

# 35. Bootstrap

Lần đầu deploy:

```bash
make bootstrap
```

phải:

1. validate env;
2. chờ PostgreSQL;
3. migrate DB;
4. tạo MinIO bucket;
5. tạo admin nếu chưa có;
6. tạo system service token;
7. kiểm tra Git storage;
8. chạy healthcheck.

Bootstrap phải idempotent.

---

# 36. Backup

Backup tối thiểu:

```text
PostgreSQL dump
Git repositories
MinIO package data
config/secrets metadata cần thiết
```

Script:

```bash
./deploy/scripts/backup.sh
```

Output:

```text
/backups/YYYY-MM-DD_HHMM/
├── postgres.dump
├── git-repositories.tar.zst
├── minio-manifest.json
└── metadata.json
```

MinIO production có thể backup bằng replication hoặc `mc mirror`.

Restore document bắt buộc.

---

# 37. Cleanup

Scheduled job:

```text
orphan build workspace cleanup
expired token cleanup
old build log retention
upstream cache cleanup
```

Không auto-delete:

```text
internal package
git repo
audit
```

---

# 38. Test strategy

## 38.1. Unit tests

Backend:

```text
auth
permission resolution
package normalization
version/tag validation
token hashing
package prefix validation
PyPI URL rewrite
dependency confusion rule
```

Coverage target:

```text
>= 80% cho service/core
```

---

## 38.2. Integration tests

Dùng test PostgreSQL/Redis/MinIO.

Bắt buộc:

```text
create user
login
create group
add member
create project
create SSH key
create token
publish package
query simple API
download package
proxy public package
cache hit
audit
```

---

## 38.3. Git integration test

Automated:

1. create test project;
2. initialize local repo;
3. push `main`;
4. verify build created;
5. push tag `v0.1.0`;
6. verify release build;
7. verify package version exists.

---

## 38.4. End-to-end package test

Tạo package fixture:

```text
hawee-demo-tool
```

Code:

```python
def add(a, b):
    return a + b
```

Build và publish `0.1.0`.

Sau đó tạo clean venv:

```bash
python -m venv /tmp/testenv
```

Config:

```bash
pip install \
  --index-url http://packages.test/simple \
  hawee-demo-tool==0.1.0
```

Run:

```python
from hawee_demo_tool import add
assert add(1, 2) == 3
```

PASS mới coi release pipeline hoạt động.

---

## 38.5. Proxy test

Clean venv:

```bash
pip install \
  --index-url http://packages.test/simple \
  requests
```

Verify:

1. lần đầu lấy upstream;
2. artifact được cache;
3. ngắt mock upstream;
4. cài lại được từ cache.

---

## 38.6. Dependency confusion test

Tạo request:

```text
/simple/hawee-package-khong-ton-tai/
```

Expected:

```text
404
```

Assert:

```text
KHÔNG gọi pypi.org.
```

Đây là test bắt buộc.

---

## 38.7. Permission tests

Viewer:

```text
clone allowed tùy policy
push denied
publish denied
```

Developer:

```text
push allowed
delete project denied
```

Maintainer:

```text
release allowed
yank allowed
```

Admin:

```text
system management allowed
```

---

# 39. Acceptance criteria V1

Chỉ coi dự án hoàn thành khi tất cả điều kiện sau đạt.

### User

- [ ] Tạo user.
- [ ] Login.
- [ ] Disable user.
- [ ] Add SSH key.
- [ ] Create read_package token.

### Group

- [ ] Tạo group.
- [ ] Add member.
- [ ] Role enforcement chính xác.

### Project

- [ ] Tạo project.
- [ ] Bare Git repo được tạo.
- [ ] Clone URL hiển thị.
- [ ] `git clone` thành công.
- [ ] `git push` thành công khi có quyền.
- [ ] push bị từ chối khi không có quyền.

### Build

- [ ] Push main tạo validation build.
- [ ] Tag tạo release build.
- [ ] Version mismatch fail.
- [ ] Build timeout hoạt động.
- [ ] Log xem được.

### Package

- [ ] `.whl` được build.
- [ ] `twine check` pass.
- [ ] Fresh install test pass.
- [ ] Package upload MinIO.
- [ ] Metadata lưu PostgreSQL.
- [ ] Không overwrite version.
- [ ] SHA256 đúng.

### Registry

- [ ] `/simple/hawee-demo-tool/` hoạt động.
- [ ] `pip install hawee-demo-tool` hoạt động.
- [ ] `pip install requests` qua cùng index hoạt động.
- [ ] upstream file được cache.
- [ ] cache vẫn dùng được khi upstream tạm unavailable.
- [ ] `hawee-*` không fallback PyPI.

### UI

- [ ] Catalogue.
- [ ] Search tool.
- [ ] Project detail.
- [ ] Version history.
- [ ] Build history.
- [ ] Copy install command.
- [ ] Member management.

### Ops

- [ ] Docker Compose startup.
- [ ] Migration.
- [ ] Healthcheck.
- [ ] Backup script.
- [ ] Restore document.
- [ ] `.env.example`.
- [ ] No secrets committed.

---

# 40. Developer experience mong muốn

Người tạo tool:

```bash
git clone git@git.hawee.local:ai-tools/pdf-parser.git

cd pdf-parser

# code...

git add .
git commit -m "feat: improve parser"
git push origin main
```

UI:

```text
Validation build: SUCCESS
```

Release:

```bash
git tag v1.2.0
git push origin v1.2.0
```

UI:

```text
hawee-pdf-parser
Latest: 1.2.0
Build: SUCCESS
```

Người dùng tool:

```bash
pip install hawee-pdf-parser==1.2.0
```

hoặc requirements:

```text
numpy==2.1.0
pandas==2.2.3
hawee-pdf-parser==1.2.0
```

Chỉ cần một registry:

```text
https://packages.hawee.local/simple
```

---

# 41. Luồng end-to-end

```text
Developer
    │
    │ git push main
    ▼
Git repository
    │
    │ post-receive
    ▼
FastAPI internal event
    │
    ▼
PostgreSQL: create build
    │
    ▼
Redis queue
    │
    ▼
Worker
    │
    ▼
Ephemeral container
    │
    ├── clone exact commit
    ├── test
    ├── python -m build
    ├── twine check
    └── install wheel test
    │
    ▼
Validation success
```

Release:

```text
Developer
    │
    │ git push tag v1.4.0
    ▼
Git
    ▼
post-receive
    ▼
Build Worker
    ▼
Validate:
tag == pyproject version
package prefix == hawee-
    ▼
Build/test
    ▼
MinIO
    ▼
PostgreSQL package metadata
    ▼
Package Registry
    ▼
pip install hawee-xxx==1.4.0
```

External dependency:

```text
pip install numpy
    ↓
packages.hawee.local
    ↓
not internal
    ↓
cache?
    ├── yes → return MinIO
    └── no  → PyPI
                ↓
              verify
                ↓
              MinIO
                ↓
              return
```

---

# 42. Error codes chuẩn

Build:

```text
GIT_CLONE_FAILED
INVALID_PYPROJECT
INVALID_PACKAGE_NAME
INTERNAL_PREFIX_REQUIRED
VERSION_MISMATCH
TEST_FAILED
BUILD_FAILED
TWINE_CHECK_FAILED
INSTALL_TEST_FAILED
PACKAGE_VERSION_EXISTS
BUILD_TIMEOUT
RUNNER_ERROR
```

Registry:

```text
PACKAGE_NOT_FOUND
PACKAGE_ACCESS_DENIED
UPSTREAM_UNAVAILABLE
UPSTREAM_HASH_MISMATCH
CACHE_STORAGE_ERROR
```

Auth:

```text
INVALID_CREDENTIALS
TOKEN_EXPIRED
TOKEN_REVOKED
INSUFFICIENT_SCOPE
PERMISSION_DENIED
```

---

# 43. Observability

Metrics nên có:

```text
http_requests_total
http_request_duration_seconds
builds_total
build_duration_seconds
build_queue_depth
build_failures_total
package_downloads_total
upstream_cache_hits_total
upstream_cache_misses_total
upstream_download_bytes
internal_package_storage_bytes
```

V1 có thể expose:

```text
/metrics
```

để sau này gắn Prometheus.

---

# 44. Production deployment recommendation

## Staging

Có thể một server:

```text
Nginx
API
UI
PostgreSQL
Redis
MinIO
Git
Worker
```

## Production

Khuyến nghị ít nhất:

```text
Server 1:
- Nginx
- UI
- API
- PostgreSQL
- Redis
- MinIO
- Git

Server 2:
- Build Worker
- Container runtime
```

Lý do:

```text
code developer có thể không đáng tin tuyệt đối
build package có thể CPU/RAM cao
không để build ảnh hưởng DB/Git/API
```

---

# 45. Nginx routing

Ví dụ:

```text
tools.hawee.local
  /              → frontend
  /api/          → backend

packages.hawee.local
  /simple/       → backend registry
  /packages/     → backend registry

git.hawee.local
  SSH port 22/2222 → git-service
```

TLS:

```text
certificate nội bộ của công ty
```

Các máy developer phải trust internal CA.

---

# 46. Server readiness checklist

Trước production:

- [ ] Ubuntu LTS.
- [ ] static IP.
- [ ] DNS nội bộ.
- [ ] TLS certificate.
- [ ] NTP.
- [ ] đủ disk.
- [ ] backup destination.
- [ ] firewall.
- [ ] Docker/Podman.
- [ ] monitoring.
- [ ] SMTP nếu cần email notification.
- [ ] outbound HTTPS tới `pypi.org` nếu dùng proxy live.
- [ ] hoặc upstream mirror strategy nếu server không Internet.

---

# 47. Nếu server không Internet

Registry vẫn phục vụ:

```text
internal packages
cached public packages
```

Nhưng package public chưa cache sẽ không tải được.

Future option:

```text
sync approved PyPI packages từ một máy có Internet
```

hoặc:

```text
một upstream proxy server ở DMZ
```

---

# 48. Future V2

Sau khi V1 ổn định:

```text
LDAP/AD
OIDC/SSO
NuGet
npm
Maven
manual approval trước release
release notes
project templates
webhook notifications
email
dependency allowlist
malware scanning
SBOM
license policy
HA deployment
object storage replication
```

---

# 49. Không được làm

Claude Code / implementation agent KHÔNG được:

1. viết mock thay cho chức năng chính;
2. để TODO ở luồng production;
3. build trực tiếp trên API container;
4. lưu token plaintext trong DB;
5. commit `.env`;
6. dùng `Base.metadata.create_all()` thay migration production;
7. fallback package `hawee-*` ra PyPI;
8. overwrite package version đã publish;
9. chạy user build container với privileged mode;
10. cho SSH shell tương tác cho `git` user;
11. bỏ qua permission test;
12. bỏ qua end-to-end `pip install` test.

---

# 50. Yêu cầu cho Claude Code khi triển khai

Hãy coi tài liệu này là **implementation contract**.

Thứ tự thực hiện:

```text
Phase 1
Database + auth + RBAC

Phase 2
Group + project + Git bare repo + SSH authorization

Phase 3
Git post-receive event + queue + build worker

Phase 4
Python package build/release

Phase 5
MinIO package storage + Simple API

Phase 6
PyPI proxy/cache

Phase 7
React UI

Phase 8
Security hardening

Phase 9
Automated tests

Phase 10
Docker Compose + deployment docs + backup
```

Sau mỗi phase:

```text
run migration
run tests
fix all failures
không tiếp tục nếu test phase hiện tại chưa pass
```

Cuối cùng bắt buộc chạy:

```bash
make test
make e2e
make smoke-test
```

Expected:

```text
ALL TESTS PASSED
```

---

# 51. Makefile commands mong muốn

```bash
make dev
make stop
make logs
make migrate
make seed
make test
make test-unit
make test-integration
make e2e
make smoke-test
make backup
make restore
make bootstrap
```

---

# 52. Smoke test production

Script `smoke-test.sh`:

1. login admin;
2. health ready;
3. create temporary group;
4. create temporary project;
5. push fixture repo;
6. tag `v0.0.1`;
7. wait build;
8. assert SUCCESS;
9. create fresh venv;
10. configure internal index;
11. install internal package;
12. import package;
13. install public package `requests`;
14. verify proxy/cache;
15. delete/archive temporary test project;
16. print summary.

Output cuối:

```text
================================================
HAWEE TOOL HUB SMOKE TEST
================================================
API                PASS
PostgreSQL         PASS
Redis              PASS
MinIO              PASS
Git push           PASS
Build worker       PASS
Internal package   PASS
pip install        PASS
PyPI proxy         PASS
Cache              PASS
RBAC               PASS
Audit              PASS
================================================
READY FOR DEPLOYMENT
================================================
```

---

# 53. Definition of Done

Không được đánh dấu hoàn thành chỉ vì Web UI chạy.

Hệ thống chỉ được coi là DONE khi luồng thực tế sau chạy thành công:

```text
User tạo project
        ↓
git clone
        ↓
developer code
        ↓
git push
        ↓
build validation
        ↓
git tag v1.0.0
        ↓
release build
        ↓
.whl
        ↓
internal registry
        ↓
project khác:
pip install hawee-tool==1.0.0
        ↓
import thành công
```

và đồng thời:

```text
pip install requests
```

cũng đi qua **cùng registry nội bộ**, tải/cache từ PyPI thành công.

---

# 54. Ghi chú triển khai thực tế

Tài liệu này đủ chi tiết để dùng làm đặc tả triển khai cho Claude Code, nhưng đây là một hệ thống hạ tầng có Git, auth, build isolation, package registry và proxy cache nên khi đưa lên server thật vẫn phải:

- kiểm tra port;
- DNS;
- TLS;
- firewall;
- quyền filesystem;
- dung lượng;
- outbound Internet;
- Docker/Podman;
- chính sách IT của công ty.

Không được giả định môi trường production giống local 100%.

Mọi thay đổi production phải được chạy qua:

```text
staging
→ smoke test
→ backup
→ production
```

---

# 55. Kết quả cuối mong muốn

Người dùng cuối chỉ thấy:

```text
HAWEE TOOL HUB
```

Tìm tool:

```text
PDF Parser
Bóc tách bản vẽ
Contract Parser
DMS Client
...
```

Mỗi tool có:

```text
Owner
Description
Latest version
Build status
README
Install command
Versions
Source
```

Developer publish:

```bash
git push
git tag v1.2.0
git push origin v1.2.0
```

Người dùng cài:

```bash
pip install hawee-pdf-parser==1.2.0
```

Project requirements:

```text
numpy==2.1.0
pandas==2.2.3
hawee-pdf-parser==1.2.0
hawee-contract-parser==2.0.0
```

Toàn bộ đều đi qua:

```text
https://packages.hawee.local/simple
```

Đây là mục tiêu chính của HAWEE Internal Tool Hub V1.
