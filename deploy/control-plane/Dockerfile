FROM python:3.12.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIDEOINSIGHT_RUNTIME_ROOT=/app/runtime

WORKDIR /app

COPY deploy/control-plane/requirements.txt /tmp/requirements.txt
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r /tmp/requirements.txt \
    && useradd --create-home --uid 10001 videoinsight \
    && mkdir -p /app/runtime/data \
    && chown -R videoinsight:videoinsight /app/runtime

COPY --chown=videoinsight:videoinsight project/backend /app/project/backend
COPY --chown=videoinsight:videoinsight src /app/src
COPY --chown=videoinsight:videoinsight database /app/database
COPY --chown=videoinsight:videoinsight deploy/control-plane/authorize_asr.py /app/deploy/authorize_asr.py
COPY --chown=videoinsight:videoinsight deploy/control-plane/backup_control_plane.py /app/deploy/backup_control_plane.py
COPY --chown=videoinsight:videoinsight deploy/control-plane/restore_control_plane.py /app/deploy/restore_control_plane.py
COPY --chown=videoinsight:videoinsight deploy/control-plane/verify_control_plane.py /app/deploy/verify_control_plane.py

USER videoinsight

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/ready', timeout=3)" || exit 1

CMD ["/bin/sh", "-c", "umask 077 && exec uvicorn project.backend.app.control_plane:app --host 0.0.0.0 --port 8080 --workers 1 --proxy-headers --forwarded-allow-ips '*'"]
