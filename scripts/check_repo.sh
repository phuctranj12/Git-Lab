#!/usr/bin/env bash
# =============================================================================
# check_repo.sh [<đường-dẫn-thư-mục-dự-án>]
#
# Kiểm tra "khâu chuẩn bị repo" TRƯỚC khi deploy — xem HUONG_DAN_CHUAN_BI_REPO.md.
# Copy file này vào <repo>/scripts-dev/check_repo.sh của dự án mới; chạy ở CI
# (HUONG_DAN_CICD.md mục 8) hoặc tay trên máy dev.
#
#   ./scripts-dev/check_repo.sh                # kiểm thư mục hiện tại
#   ./scripts-dev/check_repo.sh TelemetryServer
#
# Exit 0 nếu không có FAIL; exit 1 nếu có; exit 2 nếu sai tham số.
# Thuần bash + git + grep — không cần cài gì thêm.
# =============================================================================
set -uo pipefail

ROOT="${1:-.}"
cd "$ROOT" 2>/dev/null || { echo "Không vào được thư mục '$ROOT'"; exit 2; }
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || { echo "'$ROOT' không nằm trong git repo"; exit 2; }

fail=0
pass() { printf '  \033[32mPASS\033[0m  %s\n' "$1"; }
warn() { printf '  \033[33mWARN\033[0m  %s\n' "$1"; }
bad()  { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; fail=1; }
skip() { printf '  ....  %s\n' "$1"; }

tracked() { git ls-files 2>/dev/null; }

echo "== .gitignore & bí mật =="
[ -f .gitignore ] && pass ".gitignore ở gốc dự án" || bad "thiếu .gitignore ở gốc dự án ($ROOT)"

if git check-ignore -q .env 2>/dev/null; then
    pass ".env được .gitignore loại trừ"
else
    bad ".env CHƯA bị .gitignore loại trừ (thêm '.env' và '.env.*' vào .gitignore)"
fi

tracked_env=$(tracked | grep -E '(^|/)\.env(\.[A-Za-z0-9_-]+)?$' | grep -vE '\.env\.example$' || true)
if [ -n "$tracked_env" ]; then
    bad "file .env bị git track (BÍ MẬT LỘ): $(echo "$tracked_env" | tr '\n' ' ')"
else
    pass "không file .env nào bị git track"
fi

if tracked | grep -qE '(^|/)\.env\.example$'; then
    pass "có .env.example (hợp đồng cấu hình)"
else
    warn "chưa có .env.example — nên có, liệt kê MỌI biến kèm giá trị mẫu vô hại"
fi

# Private key / cert không được commit; public key thì OK (xem file 3 mục 14).
leaked_key=$(tracked | grep -E '(^|/)(server\.key|.*private.*\.pem|.*_rsa|id_rsa|.*\.p12|.*\.pfx)$' || true)
[ -n "$leaked_key" ] && bad "có vẻ private key/cert bị commit: $(echo "$leaked_key" | tr '\n' ' ')" \
                      || pass "không thấy private key/cert bị commit"

echo ""
echo "== Pin phiên bản thư viện =="

# ---- Python (pip) ----
mapfile -t reqs < <(tracked | grep -E '(^|/)requirements[^/]*\.txt$' || true)
if [ "${#reqs[@]}" -eq 0 ]; then
    skip "Python: không thấy requirements*.txt"
else
    for r in "${reqs[@]}"; do
        unpinned=$(grep -vE '^\s*(#|-r |-c |-e |--)' "$r" \
                   | grep -E '[A-Za-z0-9_.]' \
                   | grep -vE '(==|@ )' || true)
        if [ -n "$unpinned" ]; then
            bad "$r: dòng chưa pin '==': $(echo "$unpinned" | tr '\n' ' ')"
        else
            pass "$r: pin '==' toàn bộ"
        fi
    done
fi

# ---- Node ----
if tracked | grep -qE '(^|/)package\.json$'; then
    n=$(tracked | grep -cE '(^|/)package-lock\.json$' || true)
    y=$(tracked | grep -cE '(^|/)yarn\.lock$' || true)
    p=$(tracked | grep -cE '(^|/)pnpm-lock\.yaml$' || true)
    total=$((n + y + p))
    if   [ "$total" -eq 0 ]; then bad "Node: có package.json nhưng KHÔNG lockfile nào được track (npm ci sẽ fail)"
    elif [ "$total" -gt 1 ]; then bad "Node: nhiều loại lockfile cùng lúc — chọn 1 package manager"
    else pass "Node: lockfile được track (đúng 1 loại)"
    fi
fi

# ---- Go ----
if tracked | grep -qE '(^|/)go\.mod$'; then
    tracked | grep -qE '(^|/)go\.sum$' && pass "Go: go.sum được track" \
                                        || bad "Go: có go.mod nhưng go.sum KHÔNG được track"
fi

# ---- Java ----
if tracked | grep -qE '(^|/)pom\.xml$'; then
    tracked | grep -qE '(^|/)mvnw$' && pass "Java: Maven wrapper (mvnw) được track" \
                                     || warn "Java: pom.xml không kèm mvnw — build phụ thuộc Maven cài sẵn trên máy build"
fi
if tracked | grep -qE '(^|/)build\.gradle(\.kts)?$'; then
    tracked | grep -qE '(^|/)gradlew$' && pass "Java: Gradle wrapper (gradlew) được track" \
                                        || warn "Java: build.gradle không kèm gradlew"
fi

echo ""
echo "== Docker base image =="
mapfile -t dfs < <(tracked | grep -E '(^|/)Dockerfile([.-][A-Za-z0-9_]+)?$' || true)
if [ "${#dfs[@]}" -eq 0 ]; then
    skip "không thấy Dockerfile"
else
    for d in "${dfs[@]}"; do
        while IFS= read -r line; do
            img=$(printf '%s\n' "$line" | awk '{for(i=1;i<=NF;i++) if(toupper($i)=="FROM"){print $(i+1); exit}}')
            case "$img" in
                ""|\$*|--*)  skip "$d: '$line' (ARG/flag — bỏ qua)";;
                scratch)     skip "$d: FROM scratch";;
                *@sha256:*)  pass "$d: $img (pin digest)";;
                *:latest)    bad  "$d: FROM $img — KHÔNG dùng ':latest'";;
                *:*)         pass "$d: $img (có tag)";;
                *)           bad  "$d: FROM $img — thiếu tag (Docker hiểu là ':latest')";;
            esac
        done < <(grep -iE '^[[:space:]]*FROM[[:space:]]' "$d")
    done
fi

echo ""
if [ "$fail" -eq 0 ]; then
    echo "==> OK — repo sẵn sàng cho bước 1 (HUONG_DAN_DEPLOY.md)."
    exit 0
else
    echo "==> Còn mục FAIL — sửa theo HUONG_DAN_CHUAN_BI_REPO.md rồi chạy lại."
    exit 1
fi
