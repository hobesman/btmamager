FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/data
WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

# Runs as root so a freshly created ./data bind mount is writable. Set `user: "PUID:PGID"`
# in compose instead if you prefer; ./data must then be writable by that user.
VOLUME /data
EXPOSE 8091
HEALTHCHECK --interval=60s --timeout=5s CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('WEB_PORT', '8091') + '/healthz')"]

ENTRYPOINT ["btmamager"]
CMD ["web"]
