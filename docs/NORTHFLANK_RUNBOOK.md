# Northflank Deployment Runbook

## Source isolation

- Repository: `AlirezaNourahmadi/test`
- Railway branch: `main` (do not change for this deployment)
- Northflank branch: `northflank`
- Northflank project: `garibar`
- Northflank service: `test`

## Build

- Build type: Dockerfile
- Build context: `/`
- Dockerfile: `/Dockerfile`
- Runtime mode: default image ENTRYPOINT/CMD
- Image user: `10001:10001`
- Xray image/version: `ghcr.io/xtls/xray-core:26.3.27`
- Cloudflare image/version: `cloudflare/cloudflared:2026.5.2`

## Networking

| Name | Internal port | Protocol | Access | Public hostname |
|---|---:|---|---|---|
| `garibar` | 8000 | HTTP | Public | `garibar--test--tvbmvy4f8p8m.code.run` |
| `xray` | 10000 | HTTP | Public | `xray--test--tvbmvy4f8p8m.code.run` |

Northflank HTTP ports support the WebSocket upgrade used by Xray. Do not expose
the private API port `10085`.

## Environment

```text
PORT=8000
XRAY_PUBLIC_HOST=xray--test--tvbmvy4f8p8m.code.run
```

The Dockerfile supplies these defaults:

```text
DATA_DIR=/data
PERSISTENCE_MODE=ephemeral
XRAY_ENABLED=1
XRAY_PORT=10000
XRAY_API_PORT=10085
XRAY_WS_PATH=/ws
XRAY_PUBLIC_PORT=443
XRAY_OUTBOUND_DOMAIN_STRATEGY=UseIPv4
CLOUDFLARE_QUICK_TUNNEL_ENABLED=1
CLOUDFLARE_TUNNEL_ORIGIN=http://127.0.0.1:10000
CLOUDFLARE_EDGE_IP_VERSION=4
CLOUDFLARE_TUNNEL_PROTOCOL=http2
```

Optional production overrides are `ADMIN_PASSWORD`, `SECRET_KEY`,
`XRAY_LOG_LEVEL`, and `XRAY_STATS_INTERVAL`.

## Storage mode

No volume or database is attached to the current deployment. `/data` is
writable container storage, so configurations survive only while that container
exists. They are lost on redeploy or container replacement. This is the chosen
no-cost mode and is exposed as `durable: false` by `/health`.

For optional durable storage later, attach a single read/write volume at
`/data`, keep one service replica, and set `PERSISTENCE_MODE=volume`.

## Health verification

Northflank readiness probe:

```text
type=readiness
protocol=HTTP
port=8000
path=/health
initial_delay=10s
interval=15s
timeout=5s
max_failures=3
```

```bash
curl -fsS https://garibar--test--tvbmvy4f8p8m.code.run/health
```

Expected fields:

```json
{
  "status": "ok",
  "persistence": {
    "path": "/data",
    "writable": true,
    "mode": "ephemeral",
    "durable": false
  },
  "xray": {
    "enabled": true,
    "running": true,
    "listen_port": 10000,
    "public_host_configured": true,
    "outbound_domain_strategy": "UseIPv4"
  },
  "cloudflare_tunnel": {
    "enabled": true,
    "running": true,
    "ready": true,
    "public_host": "RANDOM.trycloudflare.com",
    "origin": "http://127.0.0.1:10000"
  }
}
```

Do not change the outbound strategy to `UseIP` on this deployment. The pod can
resolve IPv6 addresses but has no working IPv6 route, which causes intermittent
destination failures.

The endpoint returns HTTP 503 when storage is not writable or Xray is not
running, allowing the readiness probe to remove an unhealthy pod from routing.

## Functional verification

1. Open the panel at `/login`.
2. Wait until `/health` reports `cloudflare_tunnel.ready=true`.
3. Create a new configuration after this deployment. Old links using
   `/ws/<uuid>` target the retired Python relay path and are not the acceptance
   test for this architecture.
4. Confirm the generated host ends in `.trycloudflare.com`.
5. Import the generated link into V2Box, NPV Tunnel, v2rayNG, or Xray Core.
6. Connect and load an HTTPS page.
7. Verify a DNS lookup also succeeds through the tunnel.
8. Confirm the dashboard records traffic for the new configuration.

The Quick Tunnel hostname is ephemeral and changes after a cloudflared or
container restart. Refresh the subscription or copy the current panel link
after such a restart. Use a named Cloudflare Tunnel with a custom domain when a
stable production hostname is required.

Do not use a redeploy as a persistence acceptance test in the current no-volume
mode; losing panel state after container replacement is expected.

## Rollback

Rollback the Northflank service to commit `8ed93e3` only if the official Xray
deployment cannot start. This affects only the `northflank` branch/deployment.
Do not merge or reset the Railway `main` branch.
