# Pinned by digest: a rebuild gets exactly the audited base image.
# Update: docker pull python:3.12-slim && docker image inspect --format "{{index .RepoDigests 0}}" python:3.12-slim
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Europe/Moscow \
    PYTHONPATH=/app

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY config ./config
COPY data ./data

# Run without root: a bug in the app must not own the container.
RUN useradd --system --uid 10001 --no-create-home app \
    && chown -R app:app /app/data
USER app

EXPOSE 8000

# Only Caddy can reach the API (it is not published), so trusting its
# X-Forwarded-For gives real client IPs to logs and the login rate limit.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*", "--no-server-header"]
