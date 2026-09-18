# Sổ tay Hướng dẫn Sử dụng HAWEE Tool Hub

Tài liệu này cung cấp hướng dẫn đầy đủ từ cơ bản đến nâng cao cho các vai trò: **Developer phát triển tool**, **Kỹ sư sử dụng tool (Consumer)**, và **Quản trị viên (Admin/Owner)** trên nền tảng **HAWEE Tool Hub**.

---

## 1. Giới thiệu & Khái niệm Cốt lõi

HAWEE Tool Hub là nền tảng quản lý mã nguồn, tự động hóa build/kiểm thử và phân phối gói thư viện Python nội bộ trong doanh nghiệp.

### Các thực thể chính:
- **Group (Nhóm)**: Đại diện cho phòng ban, nhóm sản phẩm hoặc chuyên đề (ví dụ: `ai-tools`, `cad-bim`, `infra-tools`).
- **Project (Dự án / Tool)**: Đại diện cho một kho mã nguồn chứa công cụ / thư viện (ví dụ: `pdf-parser`, `cad-extractor`).
- **Package**: Gói thư viện Python hoàn chỉnh (`.whl` và `.tar.gz`) được xuất xưởng tự động và lưu trữ trên Registry nội bộ.

### Quy ước bắt buộc về định danh:
> [!IMPORTANT]
> Toàn bộ các gói thư viện Python nội bộ bắt buộc phải có tiền tố `hawee-` (ví dụ: `hawee-pdf-parser`, `hawee-dms-client`). Quy định này nhằm chống xung đột tên gói và ngăn chặn triệt để tấn công Dependency Confusion.

### Hệ thống Phân quyền (RBAC):
- **System Admin**: Toàn quyền trên hệ thống (quản lý user, group, runner, audit logs).
- **Group Owner**: Quản lý thành viên trong group, tạo và lưu trữ project trong group.
- **Maintainer**: Phân quyền thành viên project, bật bảo vệ nhánh, kích hoạt release tag (`git push vX.Y.Z`), yank package.
- **Developer**: Push nhánh tính năng, kích hoạt validation build, xem mã nguồn và logs.
- **Viewer**: Xem thông tin project, clone mã nguồn và tải package (nếu project ở chế độ Internal).

---

## 2. Hướng dẫn dành cho Developer (Tạo & Phát hành Tool)

### 2.1. Cấu hình SSH Key trên tài khoản
Để clone và push mã nguồn qua Git SSH, bạn cần đăng ký SSH Public Key:

1. Tạo SSH Key trên máy cá nhân (nếu chưa có):
   ```bash
   ssh-keygen -t ed25519 -C "your_email@hawee.local"
   ```
2. Đăng nhập vào Web UI của HAWEE Tool Hub (`http://SERVER:8080`).
3. Nhấp vào ảnh đại diện góc trên bên phải -> Chọn **Settings** -> Tab **SSH Keys**.
4. Nhấn **Add SSH Key**, dán nội dung file `~/.ssh/id_ed25519.pub` vào ô và nhấn **Save**.
5. Kiểm tra kết nối SSH từ terminal máy cá nhân:
   ```bash
   ssh -p 2222 git@SERVER_IP
   ```
   *Kết quả thành công sẽ chào mừng username của bạn và đóng kết nối (không mở shell).*

---

### 2.2. Tạo Project mới
1. Trên giao diện Web, truy cập vào Group của bạn (ví dụ: `ai-tools`).
2. Nhấn nút **New Project**.
3. Điền thông tin:
   - **Project Name**: `pdf-parser`
   - **Visibility**: `Internal` (toàn bộ nhân viên công ty có thể xem và tải package) hoặc `Private` (chỉ thành viên dự án).
   - **Protect default branch**: Bật để chỉ Maintainer/Owner mới được push vào nhánh `main`.
4. Sau khi tạo, hệ thống sẽ cấp địa chỉ clone chuẩn:
   ```text
   ssh://git@SERVER_IP:2222/ai-tools/pdf-parser.git
   ```

---

### 2.3. Cấu trúc Thư mục Dự án Chuẩn
Một project tiêu chuẩn trên Tool Hub có cấu trúc như sau:

```text
hawee-pdf-parser/
├── pyproject.toml         # Cấu hình định danh, version và metadata package
├── README.md              # Tài liệu hướng dẫn sử dụng thư viện
├── hawee_pdf_parser/      # Mã nguồn chính của thư viện
│   ├── __init__.py
│   └── parser.py
└── tests/                 # Bộ kiểm thử tự động (pytest)
    └── test_parser.py
```

Ví dụ file `pyproject.toml` chuẩn:
```toml
[build-system]
requires = ["setuptools>=61.0"]
build-backend = "setuptools.build_meta"

[project]
name = "hawee-pdf-parser"
version = "1.0.0"
description = "Công cụ bóc tách dữ liệu bản vẽ kỹ thuật PDF cho kỹ sư HAWEE"
readme = "README.md"
requires-python = ">=3.10"
dependencies = [
    "pypdf>=3.0.0",
    "pdfplumber>=0.9.0"
]

[project.optional-dependencies]
dev = [
    "pytest>=7.0.0"
]
```

---

### 2.4. Khởi tạo và Đẩy Mã nguồn (Validation Build)
Clone repository trống về máy và đẩy mã nguồn:

```bash
git clone ssh://git@SERVER_IP:2222/ai-tools/pdf-parser.git
cd pdf-parser

# Khởi tạo mã nguồn và commit
git checkout -b main
git add .
git commit -m "feat: initial commit pdf parser"
git push origin main
```

**Điều gì xảy ra phía máy chủ?**
- Hệ thống tự động kích hoạt **Validation Build**:
  1. Khởi tạo container Docker cô lập và cài đặt các dependencies.
  2. Tự động chạy toàn bộ unit tests trong thư mục `tests/` bằng `pytest`.
  3. Biên dịch gói bằng `python -m build`.
  4. Kiểm tra chất lượng metadata bằng `twine check`.
  5. Cài đặt thử file `.whl` vào một môi trường ảo sạch để xác thực khả năng import.
- **Lưu ý quan trọng**: Khi push vào nhánh (kể cả `main`), hệ thống **CHỈ KIỂM THỬ VÀ XÁC THỰC, TUYỆT ĐỐI KHÔNG PUBLISH PACKAGE RA REGISTRY**.
- Bạn có thể vào Web UI -> tab **Builds** để xem stream log trực tiếp từng bước theo thời gian thực.

---

### 2.5. Phát hành Phiên bản Mới (Release Build)
Khi mã nguồn đã ổn định và sẵn sàng cung cấp cho người khác sử dụng:

1. Đảm bảo version trong file `pyproject.toml` đã được nâng (ví dụ: `version = "1.0.0"`).
2. Tạo Git tag với định dạng `vX.Y.Z` trùng khớp chính xác với version trong `pyproject.toml`:
   ```bash
   git tag v1.0.0
   git push origin v1.0.0
   ```

**Quy trình xuất xưởng tự động:**
- Hệ thống đối chiếu: Tag `v1.0.0` khớp với version `1.0.0` trong code.
- Chạy toàn bộ quy trình kiểm thử và đóng gói.
- Sau khi kiểm thử thành công, hệ thống tự động tải file `.whl` và `.tar.gz` lên MinIO Package Registry.
- Gói `hawee-pdf-parser==1.0.0` chính thức xuất hiện trên Registry và sẵn sàng để `pip install`.

> [!CAUTION]
> **Quy tắc Bất biến (Immutability):** Một khi tag `v1.0.0` đã build và publish thành công, bạn không thể xóa hoặc push đè tag này. Nếu muốn sửa đổi, hãy cập nhật mã nguồn, tăng version lên `1.0.1` và tạo tag `v1.0.1`.

---

## 3. Hướng dẫn dành cho Người Dùng Thư Viện (Consumers)

Người dùng chỉ cần cấu hình pip **MỘT LẦN DUY NHẤT**. Endpoint Registry của HAWEE Tool Hub vừa phục vụ các package nội bộ (`hawee-*`), vừa tự động đóng vai trò proxy/cache các package public từ PyPI (`numpy`, `pandas`, `requests`,...).

### 3.1. Tạo Personal Access Token
1. Đăng nhập vào Web UI (`http://SERVER:8080`).
2. Nhấp vào ảnh đại diện -> **Settings** -> Tab **Access Tokens**.
3. Nhập tên mô tả token (ví dụ: `laptop-pip-token`).
4. Chọn Scope: tích chọn **`read_package`**.
5. Nhấn **Create Token** và sao chép mã token hiển thị (ví dụ: `thp_xxxxxxxxxxxxxxxxxxxx`).

---

### 3.2. Cấu hình pip trên máy tính cá nhân

#### Cách 1: Thiết lập qua pip config & file netrc (Khuyến nghị cao nhất)
Đây là cách thiết lập chuẩn giúp mã token không bị lưu lộ trong lịch sử dòng lệnh terminal.

**Bước 1: Cấu hình index URL của pip:**
```bash
pip config set global.index-url http://SERVER_IP:8081/simple
pip config set global.trusted-host SERVER_IP     # Chỉ cần thiết nếu dùng HTTP staging
```

**Bước 2: Cấu hình file lưu trữ thông tin xác thực (`netrc`):**
- **Trên Linux / macOS**: Mở hoặc tạo file `~/.netrc` và thêm dòng:
  ```text
  machine SERVER_IP login __token__ password thp_xxxxxxxxxxxxxxxxxxxx
  ```
  Phân quyền bảo mật file: `chmod 600 ~/.netrc`.

- **Trên Windows**: Mở file `%USERPROFILE%\_netrc` (ví dụ `C:\Users\admin\_netrc`) và thêm nội dung tương tự:
  ```text
  machine SERVER_IP login __token__ password thp_xxxxxxxxxxxxxxxxxxxx
  ```

#### Cách 2: Nhúng Token trực tiếp (Dùng tạm thời hoặc nhanh)
```bash
pip install --index-url http://__token__:thp_xxxxxxxxxxxxxxxxxxxx@SERVER_IP:8081/simple hawee-pdf-parser
```

---

### 3.3. Cài đặt Package
Sau khi cấu hình như trên, bạn có thể cài đặt đồng thời cả gói nội bộ lẫn gói ngoài public chỉ bằng các lệnh pip thông thường:

```bash
# Cài đặt thư viện nội bộ HAWEE:
pip install hawee-pdf-parser==1.0.0

# Cài đặt các thư viện open-source ngoài internet:
pip install fastapi uvicorn pandas

# Cài đặt toàn bộ danh sách trong requirements.txt:
pip install -r requirements.txt
```

---

### 3.4. Tích hợp an toàn trong Dockerfile dự án ứng dụng
Để dự án ứng dụng của bạn có thể kéo package nội bộ khi build Docker mà **không làm lộ token trong image**, hãy sử dụng tính năng Secret Mount của Docker BuildKit:

```dockerfile
# syntax=docker/dockerfile:1.4
FROM python:3.12-slim

WORKDIR /app

# Cấu hình index-url nội bộ
ENV PIP_INDEX_URL=http://packages.hawee.local/simple

COPY requirements.txt .

# Mount bí mật ~/.netrc trong thời gian chạy lệnh pip, không lưu vào layer image:
RUN --mount=type=secret,id=netrc,target=/root/.netrc \
    pip install --no-cache-dir -r requirements.txt

COPY . .
CMD ["python", "main.py"]
```

Khi build Docker image trên máy dev hoặc server CI:
```bash
docker build --secret id=netrc,src=$HOME/.netrc -t my-app:latest .
```

---

## 4. Hướng dẫn dành cho Quản trị viên (Admin & Group Owner)

### 4.1. Quản lý Người dùng & Nhóm
- **Tạo người dùng mới**: Truy cập **Admin Area** -> **Users** -> Nhấn **Create User**. Nhập thông tin và gán vai trò hệ thống (`User` hoặc `Admin`).
- **Khóa người dùng**: Nếu nhân viên nghỉ việc hoặc nghi vấn lộ mật khẩu, chuyển trạng thái tài khoản sang `Suspended`. Toàn bộ token và SSH key sẽ bị thu hồi ngay tức thì.
- **Tạo Group mới**: Truy cập **Groups** -> Nhấn **New Group** -> Đặt tên nhóm và gán Owner chịu trách nhiệm quản trị.

### 4.2. Bảo vệ Nhánh Mặc định (Branch Protection)
- Trong cài đặt từng Project -> chọn mục **Protected Branches**.
- Khi bật tính năng này trên nhánh `main`, chỉ những thành viên có quyền từ `Maintainer` trở lên mới được phép merge hoặc push code. Developer chỉ có thể push vào các nhánh tính năng (`feat/...`, `fix/...`).

### 4.3. Đánh dấu Gói lỗi (Yank Package)
Nếu một phiên bản thư viện đã phát hành ra Registry bị phát hiện lỗi logic nghiêm trọng:
1. Không thể xóa tệp để bảo toàn tính ổn định cho các hệ thống đã build trước đó.
2. Quản trị viên hoặc Maintainer truy cập vào trang chi tiết **Project** -> Tab **Packages**.
3. Chọn phiên bản lỗi (ví dụ: `1.0.0`) -> Nhấn nút **Yank Version**.
4. Nhập lý do (ví dụ: *"Chứa lỗi memory leak khi xử lý file lớn, vui lòng nâng cấp lên 1.0.1"*).
5. Sau khi Yank, `pip` sẽ tự động bỏ qua phiên bản này khi các dự án khác chạy `pip install hawee-pdf-parser`. Gói chỉ được cài nếu ai đó chỉ định đích danh `==1.0.0`.

### 4.4. Tra cứu Nhật ký Kiểm toán (Audit Logs)
- Truy cập **Admin Area** -> **Audit Logs**.
- Tìm kiếm và lọc toàn bộ sự kiện theo: Thời gian, Người thực hiện (`actor`), Hành động (`action`), Địa chỉ IP hoặc Mã dự án (`resource_id`).
- Dữ liệu này được lưu trữ bất biến và không thể bị sửa/xóa.

---

## 5. Giải đáp & Xử lý Các Lỗi Thường Gặp (FAQs)

### 1. Lỗi `BUILD FAILED: VERSION_MISMATCH`
- **Nguyên nhân**: Bạn tạo tag Git `v1.2.0` nhưng trong file `pyproject.toml` vẫn đang để `version = "1.1.0"`.
- **Khắc phục**: Sửa lại trường `version = "1.2.0"` trong `pyproject.toml`, commit lại và đẩy tag mới.

### 2. Lỗi `INVALID_PACKAGE_NAME`
- **Nguyên nhân**: Tên gói trong `pyproject.toml` không bắt đầu bằng tiền tố `hawee-` (ví dụ đặt tên là `pdf-parser`).
- **Khắc phục**: Đổi tên thành `hawee-pdf-parser` trong `pyproject.toml`.

### 3. Lỗi `TWINE_CHECK_FAILED`
- **Nguyên nhân**: Thiếu tệp `README.md` được khai báo trong `pyproject.toml` hoặc mô tả cú pháp Markdown/RST bị lỗi định dạng.
- **Khắc phục**: Kiểm tra lại file `README.md` xem đã tồn tại và hợp lệ chưa. Có thể cài đặt thư viện `twine` trên máy cá nhân để chạy thử: `python -m build && twine check dist/*`.

### 4. Lỗi `Permission denied (publickey)` khi thao tác Git
- **Nguyên nhân**: Public key chưa được thêm vào mục **SSH Keys** trên Web UI, hoặc SSH client sử dụng sai cổng kết nối.
- **Khắc phục**: 
  - Đảm bảo URL clone có kèm port 2222: `ssh://git@SERVER_IP:2222/...`.
  - Chạy `ssh -v -p 2222 git@SERVER_IP` để kiểm tra SSH client đang gửi key nào lên server.

### 5. Lỗi `HTTP 401 Unauthorized` hoặc `403 Forbidden` khi `pip install`
- **Nguyên nhân**: Chưa cấu hình file `netrc`, token bị nhập sai hoặc token không có scope `read_package`.
- **Khắc phục**: Tạo lại một Personal Access Token mới với đầy đủ quyền `read_package` và cập nhật lại file `~/.netrc` (hoặc `%USERPROFILE%\_netrc` trên Windows).
