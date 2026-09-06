FROM ghcr.io/xtls/xray-core:26.3.27 AS xray
FROM cloudflare/cloudflared:2026.5.2 AS cloudflared

FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data \
    PERSISTENCE_MODE=ephemeral \
    XRAY_BINARY=/usr/local/bin/xray \
    XRAY_CONFIG_DIR=/tmp/x4g-xray \
    XRAY_ENABLED=1 \
    XRAY_PORT=10000 \
    XRAY_API_PORT=10085 \
    XRAY_WS_PATH=/ws \
    XRAY_OUTBOUND_DOMAIN_STRATEGY=UseIPv4 \
    CLOUDFLARE_QUICK_TUNNEL_ENABLED=1 \
    CLOUDFLARE_BINARY=/usr/local/bin/cloudflared \
    CLOUDFLARE_TUNNEL_ORIGIN=http://127.0.0.1:10000 \
    CLOUDFLARE_EDGE_IP_VERSION=4 \
    CLOUDFLARE_TUNNEL_PROTOCOL=http2

RUN groupadd --gid 10001 x4g \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin x4g

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY --from=xray /usr/local/bin/xray /usr/local/bin/xray
COPY --from=cloudflared /usr/local/bin/cloudflared /usr/local/bin/cloudflared
COPY . .

RUN mkdir -p /data /tmp/x4g-xray \
    && chown -R 10001:10001 /app /data /tmp/x4g-xray

USER 10001:10001

EXPOSE 8000 10000

CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
