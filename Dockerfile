FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Europe/London

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY amcrawler ./amcrawler

# uid 99 / gid 100 match Unraid's nobody:users, so the bind-mounted /data is writable.
RUN (getent group 100 >/dev/null || groupadd -g 100 users) \
    && useradd -u 99 -g 100 -M -s /usr/sbin/nologin app \
    && mkdir -p /data \
    && chown 99:100 /data

USER app
VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=60s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5).status==200 else 1)"

CMD ["uvicorn", "--factory", "amcrawler.web:build_app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips=*"]
