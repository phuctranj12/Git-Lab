# Image cho container build tạm thời (mỗi build một container, xoá ngay sau khi xong).
# Không chứa secret; chạy non-root; không có git/ssh key; mạng chỉ tới registry nội bộ.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=60

RUN pip install --no-cache-dir build==1.6.1 twine==7.0.0 \
 && useradd --create-home --uid 1000 builder \
 && mkdir -p /workspace/src /workspace/out /opt/toolhub \
 && chown -R builder:builder /workspace

COPY run_build.py /opt/toolhub/run_build.py

USER builder
WORKDIR /workspace
ENTRYPOINT ["python", "/opt/toolhub/run_build.py"]
