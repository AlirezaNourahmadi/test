# Northflank Change Report

Date: 2026-09-06

## Objective

Fix the Northflank data plane at the root, make storage behavior explicit, keep
Railway behavior isolated on `main`, and deliver a repeatable deployment and
test flow without adding paid Northflank resources.

## Root cause findings

1. `/data` was not writable or backed by a volume. This caused state and secret
   writes to fail, but it occurred after connection close and was not the direct
   cause of short client sessions.
2. The Python relay implemented only a subset of Xray behavior. Client
   compatibility, UDP/XUDP details, and swallowed relay exceptions made it an
   unsuitable production data plane.
3. The original UUID generator produced UUID-shaped random values instead of
   explicit RFC 4122 UUID v4 values.
4. The first dynamic Xray user patch omitted the inbound port. Xray's CLI
   returned process status 0 while reporting that zero users were added. The
   supervisor now validates the operation count and recovers by restarting from
   complete desired state.
5. The initial official Xray outbound used `UseIP`. Direct inspection inside the
   Northflank pod showed that IPv6 DNS resolution works but IPv6 connections
   fail with `Network is unreachable` (`connect_ex=99`). Random IPv6 selection
   explained intermittent first-connection failures; outbound resolution now
   uses `UseIPv4`.

## Code changes

### `xray_runtime.py`

- Added official Xray process supervision.
- Added deterministic server configuration generation.
- Added pre-start `xray run -test` validation.
- Added loopback-only HandlerService and StatsService.
- Added dynamic user add/remove with result-count validation.
- Added automatic full-state restart recovery.
- Added per-user traffic delta collection.
- Added process log forwarding and graceful shutdown.

### `main.py`

- Integrated Xray startup, shutdown, monitoring, and user reconciliation.
- Changed generated Northflank links to the dedicated Xray hostname and `/ws`.
- Changed UUID generation to UUID v4.
- Added traffic quota accounting from official Xray counters.
- Added storage and Xray details to `/health`.
- Added explicit ephemeral/durable storage reporting and a startup warning when
  no persistent volume is configured.
- Made link create/update/delete persistence synchronous before success.
- Normalized legacy Northflank records to the supported official WS transport.

### `pages.py`

- Limited the Northflank creation flow to official VLESS/WebSocket.
- Fixed ALPN to the ingress-compatible `http/1.1` selection.
- Removed misleading speed/IP controls that the new data plane cannot enforce
  per user.

### Container files

- Added a multi-stage `Dockerfile` using official Xray Core `26.3.27`.
- Added a non-root runtime user and `/data` ownership.
- Added `.dockerignore`.

### Tests

- Retained four Python relay regression tests.
- Added Xray configuration and public endpoint unit tests.
- Added traffic delta tests.
- Added a regression assertion for the IPv4-only Northflank outbound strategy.
- Added an opt-in real Xray integration test that verifies both HTTPS/TCP and
  DNS/UDP through a dynamically added panel user.

## Test evidence

The following local acceptance sequence passed on 2026-09-06:

- 8 automated tests passed with the Xray integration flag enabled.
- HTTPS through local SOCKS and VLESS/WS returned HTTP `204`.
- UDP DNS through VLESS/WS returned Google A records.
- A user created after Xray startup was accepted without redeployment.
- Xray traffic counters added `4906` bytes to panel state.
- After a full FastAPI/Xray restart using the same data directory, the link was
  loaded and HTTPS again returned `204`.

## Northflank changes

- Service source remains branch `northflank`.
- Build type changed from Heroku Buildpack to Dockerfile/BuildKit.
- Added public HTTP port `10000` named `xray`.
- Xray public endpoint created as
  `xray--test--tvbmvy4f8p8m.code.run`.
- Added `XRAY_PUBLIC_HOST` environment variable.
- Set `XRAY_OUTBOUND_DOMAIN_STRATEGY=UseIPv4` in the container defaults.
- No volume was created after the Northflank UI showed a paid 6 GB minimum.
- No PostgreSQL service was created because one replica and atomic JSON state do
  not justify a database.
- Current `/data` is writable but ephemeral; configurations are lost on a
  redeploy or container replacement.

## Production acceptance

- Panel `/health` returned `ok` with Xray running.
- Public Xray endpoint completed a WebSocket `101 Switching Protocols` upgrade.
- A temporary configuration created through the production panel relayed HTTPS
  successfully with status `204`.
- UDP DNS through the same production configuration returned an A record.
- Panel accounting recorded `3655` bytes for the temporary user.
- The temporary user was deleted and its UUID was rejected afterward.

## Compatibility and migration

- Railway `main` remains at `4c48032` and was not modified.
- Existing Northflank links using `/ws/<uuid>` are legacy links. Create a new
  link from the updated panel for acceptance testing.
- Northflank-mode links use port 443, TLS, WebSocket path `/ws`, and ALPN
  `http/1.1`.
