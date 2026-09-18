# Chính sách & Kiến trúc Bảo mật HAWEE Tool Hub

Tài liệu này mô tả chi tiết các tầng phòng thủ, kiến trúc bảo mật chuyên sâu và các biện pháp bảo vệ chuỗi cung ứng phần mềm (Software Supply Chain Security) được tích hợp trong **HAWEE Tool Hub**.

---

## 1. Mô hình An ninh Tổng quan (Threat Model & Layered Defense)

Nền tảng HAWEE Tool Hub được thiết kế theo nguyên tắc **Zero-Trust** và **Phòng thủ đa tầng (Defense in Depth)**. Do đặc thù hệ thống cho phép developer nạp mã nguồn tùy ý và tự động biên dịch trong hạ tầng doanh nghiệp, rủi ro Thực thi mã tùy ý (Remote Code Execution - RCE) và Tấn công chuỗi cung ứng (Supply Chain Attack) được ưu tiên kiểm soát cao nhất.

```text
┌────────────────────────────────────────────────────────────────────────────┐
│ Tầng 1: Ngoại vi & Xác thực (Edge & Identity)                             │
│   • Nginx Reverse Proxy (Rate limiting, HTTPS, Header Hardening)          │
│   • Cookie HttpOnly + SameSite=Strict + Argon2id Password Hashing         │
│   • SSH AuthorizedKeysCommand + Forced Command cách ly shell               │
├────────────────────────────────────────────────────────────────────────────┤
│ Tầng 2: Kiểm soát Truy cập & Phân quyền (Authorization & Logic)           │
│   • RBAC ma trận đa cấp: Admin > Owner > Maintainer > Developer > Viewer   │
│   • Hook pre-receive: Kiểm tra quyền push, nhánh bảo vệ, release tag       │
│   • Token cá nhân phân tách Scope (read_api, write_api, read_package, ...) │
├────────────────────────────────────────────────────────────────────────────┤
│ Tầng 3: Sandbox Build Môi trường Cô lập (Container Isolation)             │
│   • Không chạy privileged Docker                                           │
│   • Gỡ bỏ toàn bộ Linux capabilities (cap_drop=ALL)                        │
│   • Chặn leo thang đặc quyền (no-new-privileges)                           │
│   • Chạy người dùng không đặc quyền (UID 1000)                             │
│   • Giới hạn tài nguyên (CPU, RAM, PID limit chống fork bomb)              │
│   • Mạng build nội bộ cách ly: KHÔNG Internet, KHÔNG chạm DB/MinIO         │
├────────────────────────────────────────────────────────────────────────────┤
│ Tầng 4: Bảo vệ Package Registry (Supply Chain & Integrity)                 │
│   • Phòng chống Dependency Confusion: namespace bắt buộc "hawee-*"         │
│   • Tính bất biến của phiên bản (Immutability): Cấm ghi đè package         │
│   • Xác thực băm toàn vẹn SHA256 cho wheel, sdist và upstream cache        │
├────────────────────────────────────────────────────────────────────────────┤
│ Tầng 5: Giám sát & Bất biến Dữ liệu (Audit & Observability)                │
│   • Trigger Database chặn UPDATE / DELETE trên bảng audit_logs            │
│   • Che giấu tự động mật khẩu/token trong toàn bộ system log               │
└────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Xác thực & Quản lý Phiên (Authentication & Session)

### 2.1. Lưu trữ và Băm Mật khẩu
- Toàn bộ mật khẩu người dùng được băm bằng thuật toán hiện đại nhất: **Argon2id** (được OWASP và tiêu chuẩn an ninh quốc tế khuyến nghị cao nhất, vượt trội hoàn toàn so với MD5/SHA/bcrypt về khả năng chống tấn công phần cứng GPU/ASIC).
- Không lưu trữ mật khẩu dưới dạng văn bản thô trong bất kỳ hoàn cảnh nào.

### 2.2. Phiên Web UI & Quản lý Token
Tuân thủ nghiêm ngặt tiêu chuẩn bảo mật phiên doanh nghiệp:
- **Cookie-based Session**: Access token JWT (HS256) được lưu trữ trong Cookie với cờ `HttpOnly`, `SameSite=Strict` và `Secure` (khi bật HTTPS). Mã JavaScript phía client (XSS) hoàn toàn không thể trích xuất cookie này.
- **Access Token ngắn hạn**: Hết hạn sau 15 phút.
- **Refresh Token Xoay vòng (Rotation) & Phát hiện Tái sử dụng (Reuse Detection)**:
  - Refresh token chỉ là chuỗi ngẫu nhiên có độ dài lớn, được băm SHA-256 trước khi lưu vào bảng `refresh_tokens`.
  - Mỗi lần cấp lại Access Token mới, Refresh Token cũ sẽ bị thu hồi và thay thế bằng Refresh Token mới.
  - Nếu hệ thống phát hiện một Refresh Token đã từng bị thu hồi được sử dụng lại (dấu hiệu lộ token), toàn bộ phiên làm việc của người dùng đó sẽ lập tức bị hủy bỏ trên mọi thiết bị.
- **Chống tấn công CSRF**: Triển khai cơ chế **Double Submit Cookie** cho toàn bộ API mutation từ trình duyệt.

### 2.3. Personal Access Token (PAT) & Service Token
- Developer và hệ thống CI/CD sử dụng Personal Access Token thay vì mật khẩu.
- Quy ước tiền tố dễ nhận diện và quét rò rỉ:
  - `thp_...`: Personal Access Token của người dùng.
  - `ths_...`: Service Token dành cho máy chủ build/CI.
- Database chỉ lưu chuỗi băm SHA-256 của token.
- Phân quyền theo **Scope**:
  - `read_api`: Chỉ xem thông tin qua REST API.
  - `write_api`: Tạo project, sửa thông tin.
  - `read_package`: Scope bắt buộc để `pip install` từ Registry.
  - `publish_package`: Quyền publish package.
  - `admin`: Yêu cầu đặc quyền để thực thi các tác vụ quản trị hệ sinh thái.

---

## 3. Bảo mật Truy cập Git & SSH

### 3.1. Cô lập Tài khoản SSH
- Hệ thống chỉ mở duy nhất 1 tài khoản Linux trên host là `git` (UID cố định).
- Người dùng **tuyệt đối không được cấp shell truy cập Linux** (`/bin/bash` hoặc `/bin/sh`).
- Cấu hình SSH Daemon sử dụng `AuthorizedKeysCommand` (`toolhub-keys`):
  - Khi client kết nối SSH bằng public key, script sẽ gửi fingerprint lên API nội bộ để tra cứu theo thời gian thực.
  - Không cần sinh hoặc đồng bộ file tĩnh `authorized_keys`.
  - Khi một key bị xóa hoặc user bị khóa trên Web UI, quyền SSH bị thu hồi ngay lập tức sau 0 giây.

### 3.2. Ép lệnh (Forced Command)
`toolhub-keys` tự động sinh cấu hình cưỡng chế cho mỗi kết nối:
```text
command="toolhub-git-auth <key_id>",no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty <public_key>
```
- Vô hiệu hóa phân bổ terminal ảo (`no-pty`).
- Vô hiệu hóa forward cổng mạng (`no-port-forwarding`) và SSH agent forwarding.
- Tiện ích `toolhub-git-auth` chỉ chấp nhận thực thi duy nhất 2 lệnh Git hợp lệ: `git-upload-pack` (fetch/clone) và `git-receive-pack` (push). Mọi lệnh khác đều bị từ chối và ghi log cảnh báo.

### 3.3. Cơ chế Hook Fail-Closed
- **Hook `pre-receive`**:
  - Kiểm tra thẩm quyền của người dùng đối với từng ref thay đổi (branch / tag).
  - Ngăn chặn push đè lịch sử (`git push --force`) vào các nhánh được bảo vệ.
  - **Tính bất biến của Release Tag**: Nếu tag trùng với một package version đã được publish trên Registry, hook sẽ lập tức từ chối thao tác sửa đổi hoặc xóa tag.
  - Nguyên tắc **Fail-Closed**: Nếu dịch vụ API nội bộ gặp sự cố hoặc timeout, hook sẽ từ chối toàn bộ lệnh push để đảm bảo an toàn.
- **Hook `post-receive`**:
  - Kích hoạt sự kiện build bất đồng bộ với cơ chế Spool dự phòng tại `/var/spool/toolhub` trong trường hợp mạng nội bộ tắc nghẽn.

---

## 4. Cô lập Môi trường Build (Container Sandbox & RCE Defense)

Quá trình build Python package từ mã nguồn bất kỳ tiềm ẩn rủi ro chạy mã độc thông qua `setup.py`, build hooks, hoặc unit tests. Hệ thống triển khai các biện pháp cô lập cực kỳ nghiêm ngặt:

### 4.1. Cấu hình Container Sandbox
Khi Celery worker nhận task build, nó khởi tạo một Docker container độc lập thông qua Docker API với các cờ hạn chế:
1. **Gỡ bỏ toàn bộ Linux Capabilities**: `cap_drop=["ALL"]`. Container không có bất kỳ đặc quyền hệ thống nào (không thể mount ổ đĩa, không can thiệp network interface, không raw socket).
2. **Chống leo thang đặc quyền**: `security_opt=["no-new-privileges:true"]`. Ngăn chặn việc chạy các binary SUID để leo thang quyền root.
3. **Chạy dưới Non-root User**: Container chạy hoàn toàn dưới user `toolhub` không đặc quyền (UID 1000, GID 1000).
4. **Giới hạn tài nguyên hệ thống (Resource Limits)**:
   - **RAM**: Khống chế cứng (mặc định 4GB, chống cạn kiệt bộ nhớ máy chủ).
   - **CPU**: Khống chế tối đa số core sử dụng (mặc định 2.0 cores).
   - **PIDs Limit**: Giới hạn số tiến trình tối đa (mặc định 512) nhằm ngăn chặn triệt để tấn công **Fork Bomb**.
   - **Timeout**: Mặc định 900 giây (15 phút). Nếu quá thời gian, container sẽ bị worker cưỡng chế tiêu diệt (`kill`).

### 4.2. Mạng Build Tách biệt (Isolated Internal Network)
- Container build được gắn vào một Docker bridge network riêng biệt mang tên `toolhub-build`.
- Mạng này được cấu hình cờ `internal: true`:
  - **Không có Gateway ra Internet**: Mã nguồn đang build không thể kết nối ra ngoài để rò rỉ dữ liệu (exfiltration) hoặc tải payload độc hại.
  - **Không kết nối Database / Storage**: Container build không thể nhìn thấy cổng PostgreSQL (5432), Redis (6379) hay MinIO (9000).
  - **Chỉ truy cập duy nhất Registry Nginx**: Container chỉ có thể kết nối tới `http://registry:8081` để cài đặt các package dependencies cần thiết.

### 4.3. Quản lý Credential Tạm thời (Ephemeral Build Token)
- Mỗi lượt build được cấp phát một token tạm thời với scope `read_package` duy nhất, gắn chặt với ID của phiên build đó.
- Container sử dụng token này để xác thực với Registry nội bộ khi cài đặt package phụ thuộc.
- Ngay khi phiên build kết thúc (dù thành công, thất bại hay bị hủy), worker lập tức gửi yêu cầu tới API để thu hồi (revoke) vĩnh viễn token tạm này. Không có bất kỳ credential nhạy cảm nào của hệ thống bị lưu lại trong artifact hay log.

---

## 5. Bảo vệ Package Registry & Chống Tấn công Chuỗi Cung Ứng

### 5.1. Chống Tấn công Dependency Confusion (Namespace Pinning)
Tấn công Dependency Confusion xảy ra khi kẻ tấn công đăng ký một package trùng tên với thư viện nội bộ của doanh nghiệp lên public PyPI với số version rất cao, khiến công cụ build tự động kéo nhầm phiên bản độc hại về máy.

**Cơ chế bảo vệ của HAWEE Tool Hub:**
1. **Namespace bắt buộc**: Mọi tool và thư viện nội bộ bắt buộc phải có tiền tố `hawee-` (ví dụ `hawee-pdf-parser`).
2. **Khóa truy vấn ngược (No-Leak Policy)**:
   - Khi nhận request `GET /simple/<tên-package>/`, hệ thống kiểm tra tiền tố.
   - Nếu tên package bắt đầu bằng `hawee-`, registry **chỉ tra cứu duy nhất trong cơ sở dữ liệu nội bộ**.
   - Nếu package nội bộ không tồn tại, trả về HTTP `404 Not Found`. **Tuyệt đối không bao giờ chuyển tiếp (forward) truy vấn tên này ra `pypi.org`**. Điều này vừa bảo vệ khỏi việc cài nhầm mã độc, vừa ngăn chặn rò rỉ tên các dự án nội bộ của công ty ra bên ngoài.

### 5.2. Tính Bất biến của Package (Package Immutability)
- Một khi phiên bản package (ví dụ `hawee-auth==1.0.0`) đã được publish thành công vào Registry, nó trở thành **bất biến**.
- Hệ thống chặn mọi hành vi ghi đè file `.whl` hoặc `.tar.gz` cùng số version.
- Khóa Unique Constraint ở tầng cơ sở dữ liệu PostgreSQL đảm bảo tính nhất quán tuyệt đối.
- Nếu một phiên bản có lỗi nghiêm trọng, người quản trị sử dụng tính năng **Yank** (đánh dấu phiên bản bị lỗi theo chuẩn PEP 592): `pip` sẽ bỏ qua version này khi phân giải phụ thuộc tự động, trừ khi developer chỉ định chính xác phiên bản lỗi đó.

### 5.3. Xác minh Tính toàn vẹn SHA256
- Mọi file package tải lên hoặc được cache từ upstream PyPI đều được tính toán và lưu trữ mã băm SHA256.
- Đường dẫn tải về trong Simple API luôn kèm fragment hash: `<a href="/packages/...#sha256=abc...">`.
- Khi proxy file từ public PyPI, hệ thống chỉ tải từ danh sách host đáng tin cậy (`files.pythonhosted.org`), tính toán lại SHA256 và so khớp với metadata gốc trước khi ghi vào MinIO cache.

---

## 6. Audit Logging & Chống Can thiệp Dữ liệu (Tamper-evident Auditing)

### 6.1. Ghi nhận Sự kiện Trọng yếu
Mọi hành động nhạy cảm đều được ghi lại vào bảng `audit_logs`:
- Đăng nhập thành công, đăng nhập thất bại.
- Tạo, thu hồi SSH Key, Personal Access Token.
- Tạo, sửa, phân quyền Project / Group.
- Nhận Git push event, kích hoạt build, hủy build.
- Publish package, yank package, tải package nội bộ.
- Thay đổi cấu hình bảo vệ nhánh.

Mỗi bản ghi audit chứa đầy đủ: `actor_id`, `actor_name`, `action`, `resource_type`, `resource_id`, `ip_address`, `user_agent`, `request_id`, `details` (JSON) và `created_at`.

### 6.2. Tính Bất biến ở Tầng Cơ sở Dữ liệu (Database Triggers)
Để ngăn chặn kẻ tấn công (hoặc quản trị viên) xóa dấu vết xâm nhập:
- Cơ sở dữ liệu PostgreSQL được cài đặt trigger tự động trên bảng `audit_logs`:
  ```sql
  -- Chặn mọi thao tác sửa đổi hoặc xóa trên bảng audit_logs:
  CREATE OR REPLACE FUNCTION prevent_audit_tampering()
  RETURNS TRIGGER AS $$
  BEGIN
      RAISE EXCEPTION 'Audit logs are append-only and cannot be updated or deleted.';
  END;
  $$ LANGUAGE plpgsql;
  ```
- Dữ liệu audit là **Append-Only** (chỉ cho phép thêm mới, tuyệt đối không thể sửa hoặc xóa).

### 6.3. Làm sạch Log Tự động (Sensitive Data Masking)
Bộ ghi log ứng dụng (Logger) được tích hợp middleware tự động quét và che giấu các trường nhạy cảm:
- Các chuỗi khóa `password`, `token`, `secret`, `authorization`, `cookie` đều được che tự động thành `***` trước khi xuất ra console hoặc ghi vào tệp log.

---

## 7. Quy trình Ứng phó Sự cố An ninh (Incident Response)

Nếu phát hiện nghi vấn rò rỉ mã nguồn, lộ token hoặc phát hiện package chứa mã độc:

1. **Khóa tài khoản và thu hồi quyền lập tức**:
   - Truy cập Web UI -> **Admin Panel** -> **Users** -> Chọn trạng thái `Suspended` cho tài khoản liên quan.
   - Toàn bộ phiên web, SSH Key và Access Token của tài khoản đó sẽ mất hiệu lực ngay lập tức.
2. **Yank phiên bản package bị ảnh hưởng**:
   - Truy cập trang chi tiết Project -> **Packages** -> Bấm **Yank Version** và nhập lý do sự cố để cảnh báo các hệ thống phụ thuộc.
3. **Tra cứu phạm vi ảnh hưởng qua Audit Log**:
   - Sử dụng API hoặc DB query để lọc ra toàn bộ IP và thời điểm tải package bị ảnh hưởng:
     ```sql
     SELECT created_at, actor_name, ip_address, details
     FROM audit_logs
     WHERE action = 'PACKAGE_DOWNLOADED' AND resource_id = '<VERSION_ID>'
     ORDER BY created_at DESC;
     ```
4. **Báo cáo sự cố an ninh**:
   - Gửi thông báo tới Đội ngũ An toàn thông tin / Quản trị hệ thống qua email nội bộ `security@hawee.local`.
