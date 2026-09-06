import asyncio
import json
import logging
import os
import subprocess
from pathlib import Path


logger = logging.getLogger("X4G.Xray")

XRAY_INBOUND_TAG = "vless-ws"
XRAY_USER_SUFFIX = "@x4g.local"


class XrayRuntimeError(RuntimeError):
    pass


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class XrayRuntime:
    """Owns the official Xray process and keeps its VLESS users in sync."""

    def __init__(self) -> None:
        self.enabled = _env_bool("XRAY_ENABLED")
        self.binary = os.environ.get("XRAY_BINARY", "xray")
        self.listen_host = os.environ.get("XRAY_LISTEN_HOST", "0.0.0.0")
        self.listen_port = int(os.environ.get("XRAY_PORT", "10000"))
        self.api_host = os.environ.get("XRAY_API_HOST", "127.0.0.1")
        self.api_port = int(os.environ.get("XRAY_API_PORT", "10085"))
        self.ws_path = os.environ.get("XRAY_WS_PATH", "/ws").strip() or "/ws"
        if not self.ws_path.startswith("/"):
            self.ws_path = "/" + self.ws_path
        self.public_host = os.environ.get("XRAY_PUBLIC_HOST", "").strip()
        self.public_port = int(os.environ.get("XRAY_PUBLIC_PORT", "443"))
        self.outbound_domain_strategy = (
            os.environ.get("XRAY_OUTBOUND_DOMAIN_STRATEGY", "UseIPv4").strip()
            or "UseIPv4"
        )
        self.config_dir = Path(os.environ.get("XRAY_CONFIG_DIR", "/tmp/x4g-xray"))
        self.config_path = self.config_dir / "config.json"
        self.user_patch_path = self.config_dir / "users.json"
        self.process: asyncio.subprocess.Process | None = None
        self._log_tasks: list[asyncio.Task] = []
        self._lock = asyncio.Lock()
        self._users: set[str] = set()
        self._last_counters: dict[str, int] = {}

    @property
    def api_address(self) -> str:
        return f"{self.api_host}:{self.api_port}"

    @property
    def running(self) -> bool:
        return bool(self.process and self.process.returncode is None)

    @staticmethod
    def user_email(uid: str) -> str:
        return f"{uid}{XRAY_USER_SUFFIX}"

    @staticmethod
    def uid_from_email(email: str) -> str | None:
        if not email.endswith(XRAY_USER_SUFFIX):
            return None
        return email[: -len(XRAY_USER_SUFFIX)]

    def connection_host(self, panel_host: str) -> str:
        return self.public_host or panel_host

    def build_config(self, user_ids: set[str] | list[str]) -> dict:
        clients = [
            {
                "id": uid,
                "email": self.user_email(uid),
                "level": 0,
            }
            for uid in sorted(set(user_ids))
        ]
        return {
            "log": {
                "loglevel": os.environ.get("XRAY_LOG_LEVEL", "warning"),
            },
            "api": {
                "tag": "api",
                "services": ["HandlerService", "StatsService"],
            },
            "stats": {},
            "policy": {
                "levels": {
                    "0": {
                        "statsUserUplink": True,
                        "statsUserDownlink": True,
                        "statsUserOnline": True,
                    }
                },
                "system": {
                    "statsInboundUplink": True,
                    "statsInboundDownlink": True,
                },
            },
            "inbounds": [
                {
                    "tag": "api",
                    "listen": self.api_host,
                    "port": self.api_port,
                    "protocol": "dokodemo-door",
                    "settings": {"address": self.api_host},
                },
                {
                    "tag": XRAY_INBOUND_TAG,
                    "listen": self.listen_host,
                    "port": self.listen_port,
                    "protocol": "vless",
                    "settings": {
                        "clients": clients,
                        "decryption": "none",
                    },
                    "streamSettings": {
                        "network": "ws",
                        "security": "none",
                        "wsSettings": {"path": self.ws_path},
                    },
                    "sniffing": {
                        "enabled": True,
                        "destOverride": ["http", "tls", "quic"],
                        "routeOnly": True,
                    },
                },
            ],
            "outbounds": [
                {
                    "tag": "direct",
                    "protocol": "freedom",
                    "settings": {"domainStrategy": self.outbound_domain_strategy},
                },
                {"tag": "blocked", "protocol": "blackhole"},
            ],
            "routing": {
                "domainStrategy": "AsIs",
                "rules": [
                    {
                        "type": "field",
                        "inboundTag": ["api"],
                        "outboundTag": "api",
                    }
                ],
            },
        }

    async def start(self, user_ids: set[str] | list[str]) -> None:
        if not self.enabled:
            logger.info("Official Xray runtime is disabled")
            return
        async with self._lock:
            await self._start_locked(set(user_ids))

    async def _start_locked(self, user_ids: set[str]) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        await self._write_json(self.config_path, self.build_config(user_ids))

        test = await self._run_process(
            [self.binary, "run", "-test", "-c", str(self.config_path)],
            timeout=15,
        )
        if test.returncode != 0:
            details = (test.stderr or test.stdout or "unknown validation error").strip()
            raise XrayRuntimeError(f"Xray config validation failed: {details}")

        self.process = await asyncio.create_subprocess_exec(
            self.binary,
            "run",
            "-c",
            str(self.config_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._log_tasks = [
            asyncio.create_task(self._pump_log(self.process.stdout, logging.INFO)),
            asyncio.create_task(self._pump_log(self.process.stderr, logging.WARNING)),
        ]
        try:
            await self._wait_for_api()
        except Exception:
            await self._stop_locked()
            raise
        self._users = set(user_ids)
        self._last_counters.clear()
        logger.info(
            "Official Xray started on %s:%s with %s user(s)",
            self.listen_host,
            self.listen_port,
            len(self._users),
        )

    async def stop(self) -> None:
        async with self._lock:
            await self._stop_locked()

    async def _stop_locked(self) -> None:
        process = self.process
        self.process = None
        if process and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        for task in self._log_tasks:
            task.cancel()
        if self._log_tasks:
            await asyncio.gather(*self._log_tasks, return_exceptions=True)
        self._log_tasks.clear()
        self._users.clear()
        self._last_counters.clear()

    async def reconcile(self, user_ids: set[str] | list[str]) -> None:
        if not self.enabled:
            return
        target = set(user_ids)
        async with self._lock:
            if not self.running:
                await self._stop_locked()
                await self._start_locked(target)
                return

            remove = sorted(self._users - target)
            add = sorted(target - self._users)
            try:
                if remove:
                    await self._remove_users(remove)
                if add:
                    await self._add_users(add)
                self._users = target
            except Exception:
                logger.exception("Dynamic Xray user sync failed; restarting with desired state")
                await self._stop_locked()
                await self._start_locked(target)

    async def _add_users(self, user_ids: list[str]) -> None:
        patch = {
            "inbounds": [
                {
                    "tag": XRAY_INBOUND_TAG,
                    "listen": self.listen_host,
                    "port": self.listen_port,
                    "protocol": "vless",
                    "settings": {
                        "clients": [
                            {
                                "id": uid,
                                "email": self.user_email(uid),
                                "level": 0,
                            }
                            for uid in user_ids
                        ],
                        "decryption": "none",
                    },
                }
            ]
        }
        await self._write_json(self.user_patch_path, patch)
        result = await self._run_cli(
            ["api", "adu", f"-server={self.api_address}", str(self.user_patch_path)]
        )
        if f"Added {len(user_ids)} user(s)" not in result.stdout:
            raise XrayRuntimeError(result.stdout.strip() or "Xray did not add the requested users")

    async def _remove_users(self, user_ids: list[str]) -> None:
        result = await self._run_cli(
            [
                "api",
                "rmu",
                f"-server={self.api_address}",
                f"-tag={XRAY_INBOUND_TAG}",
                *[self.user_email(uid) for uid in user_ids],
            ]
        )
        if f"Removed {len(user_ids)} user(s)" not in result.stdout:
            raise XrayRuntimeError(result.stdout.strip() or "Xray did not remove the requested users")

    async def collect_traffic_deltas(self) -> dict[str, int]:
        if not self.enabled or not self.running:
            return {}
        result = await self._run_cli(
            [
                "api",
                "statsquery",
                f"-server={self.api_address}",
                "-json",
                "-pattern=user>>>",
            ]
        )
        try:
            payload = json.loads(result.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise XrayRuntimeError("Xray returned invalid traffic JSON") from exc

        current: dict[str, int] = {}
        for item in payload.get("stat", []):
            name = item.get("name", "")
            if not name.startswith("user>>>") or ">>>traffic>>>" not in name:
                continue
            email = name.split(">>>", 2)[1]
            uid = self.uid_from_email(email)
            if uid:
                current[uid] = current.get(uid, 0) + int(item.get("value", 0))

        deltas: dict[str, int] = {}
        for uid, value in current.items():
            previous = self._last_counters.get(uid, 0)
            delta = value - previous
            if delta > 0:
                deltas[uid] = delta
        self._last_counters = current
        return deltas

    async def _run_cli(self, args: list[str]) -> subprocess.CompletedProcess:
        result = await self._run_process([self.binary, *args], timeout=10)
        if result.returncode != 0:
            details = (result.stderr or result.stdout or "unknown Xray API error").strip()
            raise XrayRuntimeError(details)
        return result

    async def _wait_for_api(self) -> None:
        deadline = asyncio.get_running_loop().time() + 10
        while asyncio.get_running_loop().time() < deadline:
            if not self.running:
                raise XrayRuntimeError("Xray stopped before its API became ready")
            try:
                _, writer = await asyncio.open_connection(self.api_host, self.api_port)
                writer.close()
                await writer.wait_closed()
                return
            except OSError:
                await asyncio.sleep(0.1)
        raise XrayRuntimeError("Timed out waiting for the Xray API")

    async def _pump_log(self, stream: asyncio.StreamReader | None, level: int) -> None:
        if stream is None:
            return
        while line := await stream.readline():
            logger.log(level, line.decode(errors="replace").rstrip())

    @staticmethod
    async def _write_json(path: Path, data: dict) -> None:
        encoded = json.dumps(data, ensure_ascii=True, indent=2) + "\n"
        tmp = path.with_suffix(path.suffix + ".tmp")
        await asyncio.to_thread(tmp.write_text, encoded, "utf-8")
        await asyncio.to_thread(tmp.replace, path)

    @staticmethod
    async def _run_process(
        args: list[str],
        timeout: float,
    ) -> subprocess.CompletedProcess:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            raise XrayRuntimeError(f"Command timed out: {' '.join(args[:3])}")
        return subprocess.CompletedProcess(
            args=args,
            returncode=process.returncode,
            stdout=stdout.decode(errors="replace"),
            stderr=stderr.decode(errors="replace"),
        )
