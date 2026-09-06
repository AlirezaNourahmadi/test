# Application Flows

## 1. Service startup

1. Northflank mounts the persistent volume at `/data`.
2. FastAPI loads `x4g_state.json` and the persistent secret.
3. Legacy Northflank links are normalized to VLESS/WebSocket, public port 443,
   and ALPN `http/1.1`.
4. The panel generates an Xray configuration containing every allowed UUID.
5. `xray run -test` validates the generated configuration.
6. Xray starts on port `10000`; its private API starts on loopback port `10085`.
7. FastAPI starts its traffic/quota monitor.
8. `/health` returns `ok` only when storage is writable and Xray is running.

## 2. Administrator login

1. Administrator opens `/login` on the panel endpoint.
2. The submitted password is hashed with the persistent secret.
3. A valid login creates an HTTP-only session cookie.
4. Protected dashboard and API routes accept the session cookie.

## 3. Create configuration

1. Administrator enters a label, expiry, quota, and TLS fingerprint.
2. The panel generates an RFC 4122 UUID v4.
3. Link metadata is written atomically to `/data/x4g_state.json`.
4. The UUID is added to Xray through the private HandlerService.
5. The response contains a VLESS link using the Xray public hostname and `/ws`.
6. The subscription endpoints continue to live on the panel hostname.

## 4. Client connection

1. The client resolves `xray--test--tvbmvy4f8p8m.code.run`.
2. It establishes TLS to Northflank on public port 443.
3. Northflank forwards the WebSocket request to container port `10000`.
4. Xray validates the VLESS UUID.
5. Xray relays TCP or UDP traffic through its `freedom` outbound.
6. Per-user uplink/downlink counters are maintained by Xray.

## 5. Traffic accounting and quota

1. Every 15 seconds the panel reads per-user Xray counters.
2. Only positive deltas are added to `used_bytes`.
3. Updated totals are persisted to `/data`.
4. If a user reaches the configured quota, the UUID is removed from Xray.
5. Resetting usage makes the UUID eligible and installs it again.

## 6. Expiry and activation

1. The monitor computes the allowed UUID set from active state, expiry, and
   quota.
2. Disabling or expiring a link removes its UUID from Xray.
3. Re-enabling an eligible link adds the UUID back without redeploying.

## 7. Delete configuration

1. The panel removes metadata from its in-memory state.
2. The new state is persisted.
3. The corresponding Xray user is removed through HandlerService.
4. Subsequent connection attempts fail UUID authentication.

## 8. Restart or redeploy

1. FastAPI collects final traffic deltas during graceful shutdown.
2. State is persisted and Xray is stopped.
3. The replacement container mounts the same volume.
4. Stored links and password state are loaded.
5. Xray starts with all currently allowed UUIDs.

## 9. Operational health check

`GET /health` reports:

- overall status (`ok` or `degraded`)
- `/data` path and write availability
- Xray enabled/running state
- Xray internal listen port
- whether the public Xray hostname is configured

