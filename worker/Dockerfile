FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt \
 && useradd --create-home --uid 1001 worker

COPY app ./app
COPY entrypoint.sh /entrypoint.sh
RUN chmod 755 /entrypoint.sh

# entrypoint chạy root chỉ để gắn user 'worker' vào group sở hữu docker.sock rồi hạ quyền.
ENTRYPOINT ["/entrypoint.sh"]
CMD ["celery", "-A", "app.celery_app", "worker", "--loglevel=INFO", "-Q", "builds"]
