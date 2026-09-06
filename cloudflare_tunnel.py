import asyncio
import logging
import os
import re


logger = logging.getLogger("X4G.CloudflareTunnel")

TRYCLOUDFLARE_URL = re.compile(
    r"https://(?P<host>[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.trycloudflare\.com)",
    re.IGNORECASE,
)


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class CloudflareTunnelRuntime:
    """Runs a no-account TryCloudflare tunnel in front of the local Xray listener."""

    def __init__(self) -> None:
        self.enabled = _env_bool("CLOUDFLARE_QUICK_TUNNEL_ENABLED")
        self.binary = os.environ.get("CLOUDFLARE_BINARY", "cloudflared")
        self.origin_url = os.environ.get(
            "CLOUDFLARE_TUNNEL_ORIGIN", "http://127.0.0.1:10000"
        ).strip()
        self.edge_ip_version = os.environ.get(
            "CLOUDFLARE_EDGE_IP_VERSION", "4"
        ).strip() or "4"
        self.protocol = os.environ.get(
            "CLOUDFLARE_TUNNEL_PROTOCOL", "http2"
        ).strip() or "http2"
        self.startup_timeout = max(
            5.0, float(os.environ.get("CLOUDFLARE_TUNNEL_STARTUP_TIMEOUT", "30"))
        )
        self.monitor_interval = max(
            5.0, float(os.environ.get("CLOUDFLARE_TUNNEL_MONITOR_INTERVAL", "10"))
        )
        self.process: asyncio.subprocess.Process | None = None
        self._public_host = ""
        self._connected = False
        self._reader_tasks: list[asyncio.Task] = []
        self._monitor_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._stopping = False

    @property
    def running(self) -> bool:
        return bool(self.process and self.process.returncode is None)

    @property
    def public_host(self) -> str:
        return self._public_host

    @property
    def ready(self) -> bool:
        return self.running and self._connected and bool(self.public_host)

    def connection_host(self, fallback: str) -> str:
        return self.public_host if self.ready else fallback

    @staticmethod
    def extract_public_host(line: str) -> str | None:
        match = TRYCLOUDFLARE_URL.search(line)
        return match.group("host").lower() if match else None

    def command(self) -> list[str]:
        return [
            self.binary,
            "tunnel",
            "--no-autoupdate",
            "--edge-ip-version",
            self.edge_ip_version,
            "--protocol",
            self.protocol,
            "--url",
            self.origin_url,
        ]

    async def start(self) -> None:
        if not self.enabled:
            logger.info("Cloudflare Quick Tunnel is disabled")
            return
        self._stopping = False
        try:
            await self._launch()
        except Exception as exc:
            logger.warning("Cloudflare Quick Tunnel startup failed: %s", exc)
        self._monitor_task = asyncio.create_task(self._monitor())

    async def stop(self) -> None:
        self._stopping = True
        if self._monitor_task:
            self._monitor_task.cancel()
            await asyncio.gather(self._monitor_task, return_exceptions=True)
            self._monitor_task = None
        async with self._lock:
            await self._stop_process()

    async def _monitor(self) -> None:
        while True:
            try:
                await asyncio.sleep(self.monitor_interval)
                if not self.ready:
                    await self._launch()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Cloudflare Quick Tunnel recovery failed: %s", exc)

    async def _launch(self) -> None:
        async with self._lock:
            if self._stopping or self.ready:
                return
            await self._stop_process()
            self._public_host = ""
            self._connected = False

            loop = asyncio.get_running_loop()
            ready: asyncio.Future[str] = loop.create_future()
            self.process = await asyncio.create_subprocess_exec(
                *self.command(),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self._reader_tasks = [
                asyncio.create_task(self._pump(self.process.stdout, ready)),
                asyncio.create_task(self._pump(self.process.stderr, ready)),
            ]
            wait_task = asyncio.create_task(self.process.wait())
            try:
                done, _ = await asyncio.wait(
                    {ready, wait_task},
                    timeout=self.startup_timeout,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if ready in done:
                    self._public_host = ready.result()
                    logger.info(
                        "Cloudflare Quick Tunnel ready at https://%s",
                        self._public_host,
                    )
                    return
                if wait_task in done:
                    raise RuntimeError(
                        f"cloudflared exited with status {wait_task.result()}"
                    )
                raise TimeoutError("cloudflared did not publish a URL in time")
            except Exception:
                await self._stop_process()
                raise
            finally:
                if not wait_task.done():
                    wait_task.cancel()
                    await asyncio.gather(wait_task, return_exceptions=True)

    async def _pump(
        self,
        stream: asyncio.StreamReader | None,
        ready: asyncio.Future[str],
    ) -> None:
        if stream is None:
            return
        while True:
            raw = await stream.readline()
            if not raw:
                return
            line = raw.decode(errors="replace").strip()
            if not line:
                continue
            host = self.extract_public_host(line)
            if host and not ready.done():
                ready.set_result(host)
            if "Registered tunnel connection" in line:
                self._connected = True
            logger.info("cloudflared: %s", line)

    async def _stop_process(self) -> None:
        process = self.process
        self.process = None
        if process and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        for task in self._reader_tasks:
            task.cancel()
        if self._reader_tasks:
            await asyncio.gather(*self._reader_tasks, return_exceptions=True)
        self._reader_tasks.clear()
        self._public_host = ""
        self._connected = False
