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
    "public_host_configured": true
  }
}
```

## Functional verification

1. Open the panel at `/login`.
2. Create a new configuration after this deployment. Old links using
   `/ws/<uuid>` target the retired Python relay path and are not the acceptance
   test for this architecture.
3. Import the generated link into V2Box, NPV Tunnel, v2rayNG, or Xray Core.
4. Connect and load an HTTPS page.
5. Verify a DNS lookup also succeeds through the tunnel.
6. Confirm the dashboard records traffic for the new configuration.

Do not use a redeploy as a persistence acceptance test in the current no-volume
mode; losing panel state after container replacement is expected.

## Rollback

Rollback the Northflank service to commit `8ed93e3` only if the official Xray
deployment cannot start. This affects only the `northflank` branch/deployment.
Do not merge or reset the Railway `main` branch.
