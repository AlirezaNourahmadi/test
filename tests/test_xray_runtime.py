import asyncio
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from xray_runtime import XRAY_INBOUND_TAG, XrayRuntime


TEST_UUID = "11111111-1111-4111-8111-111111111111"


class XrayConfigTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(
            os.environ,
            {
                "XRAY_ENABLED": "1",
                "XRAY_PORT": "11000",
                "XRAY_API_PORT": "11085",
                "XRAY_WS_PATH": "/ws",
            },
            clear=False,
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_builds_official_vless_ws_config_with_private_api(self):
        runtime = XrayRuntime()

        config = runtime.build_config({TEST_UUID})

        proxy = next(item for item in config["inbounds"] if item["tag"] == XRAY_INBOUND_TAG)
        api = next(item for item in config["inbounds"] if item["tag"] == "api")
        self.assertEqual(proxy["port"], 11000)
        self.assertEqual(proxy["streamSettings"]["network"], "ws")
        self.assertEqual(proxy["streamSettings"]["wsSettings"]["path"], "/ws")
        self.assertEqual(proxy["settings"]["clients"][0]["id"], TEST_UUID)
        self.assertEqual(api["listen"], "127.0.0.1")
        self.assertEqual(config["api"]["services"], ["HandlerService", "StatsService"])

    def test_public_connection_settings_use_environment(self):
        with patch.dict(
            os.environ,
            {"XRAY_PUBLIC_HOST": "proxy.example.test", "XRAY_PUBLIC_PORT": "443"},
        ):
            runtime = XrayRuntime()

        self.assertEqual(runtime.connection_host("panel.example.test"), "proxy.example.test")
        self.assertEqual(runtime.public_port, 443)


class XrayRuntimeAsyncTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="x4g-xray-tests-")
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(
            os.environ,
            {
                "XRAY_ENABLED": "1",
                "XRAY_CONFIG_DIR": self.tmp.name,
                "XRAY_PORT": "11000",
                "XRAY_API_PORT": "11085",
            },
            clear=False,
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    async def test_collects_only_positive_traffic_deltas(self):
        runtime = XrayRuntime()
        runtime.process = AsyncMock()
        runtime.process.returncode = None
        first = {
            "stat": [
                {
                    "name": f"user>>>{runtime.user_email(TEST_UUID)}>>>traffic>>>uplink",
                    "value": "120",
                },
                {
                    "name": f"user>>>{runtime.user_email(TEST_UUID)}>>>traffic>>>downlink",
                    "value": "80",
                },
            ]
        }
        second = {
            "stat": [
                {
                    "name": f"user>>>{runtime.user_email(TEST_UUID)}>>>traffic>>>uplink",
                    "value": "150",
                },
                {
                    "name": f"user>>>{runtime.user_email(TEST_UUID)}>>>traffic>>>downlink",
                    "value": "100",
                },
            ]
        }
        runtime._run_cli = AsyncMock(
            side_effect=[
                subprocess.CompletedProcess([], 0, json.dumps(first), ""),
                subprocess.CompletedProcess([], 0, json.dumps(second), ""),
            ]
        )

        self.assertEqual(await runtime.collect_traffic_deltas(), {TEST_UUID: 200})
        self.assertEqual(await runtime.collect_traffic_deltas(), {TEST_UUID: 50})

    @unittest.skipUnless(os.environ.get("RUN_XRAY_INTEGRATION") == "1", "integration test is opt-in")
    async def test_official_xray_relays_tcp_and_udp(self):
        runtime = XrayRuntime()
        await runtime.start(set())
        self.addAsyncCleanup(runtime.stop)
        await runtime.reconcile({TEST_UUID})

        client_config = Path(self.tmp.name) / "client.json"
        client_config.write_text(
            json.dumps(
                {
                    "log": {"loglevel": "warning"},
                    "inbounds": [
                        {
                            "listen": "127.0.0.1",
                            "port": 12080,
                            "protocol": "socks",
                            "settings": {"udp": True},
                        },
                        {
                            "listen": "127.0.0.1",
                            "port": 15353,
                            "protocol": "dokodemo-door",
                            "settings": {
                                "address": "1.1.1.1",
                                "port": 53,
                                "network": "tcp,udp",
                            },
                        },
                    ],
                    "outbounds": [
                        {
                            "protocol": "vless",
                            "settings": {
                                "vnext": [
                                    {
                                        "address": "127.0.0.1",
                                        "port": 11000,
                                        "users": [{"id": TEST_UUID, "encryption": "none"}],
                                    }
                                ]
                            },
                            "streamSettings": {
                                "network": "ws",
                                "security": "none",
                                "wsSettings": {"path": "/ws"},
                            },
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        client = await asyncio.create_subprocess_exec(
            runtime.binary,
            "run",
            "-c",
            str(client_config),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self.addAsyncCleanup(self._stop_process, client)
        await asyncio.sleep(0.5)

        curl = await asyncio.create_subprocess_exec(
            "curl",
            "-sS",
            "-o",
            "/dev/null",
            "-w",
            "%{http_code}",
            "--max-time",
            "15",
            "--socks5-hostname",
            "127.0.0.1:12080",
            "https://www.google.com/generate_204",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await curl.communicate()
        self.assertEqual(curl.returncode, 0)
        self.assertEqual(stdout.decode(), "204")

        dig = await asyncio.create_subprocess_exec(
            "dig",
            "@127.0.0.1",
            "-p",
            "15353",
            "google.com",
            "A",
            "+short",
            "+time=5",
            "+tries=1",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await dig.communicate()
        self.assertEqual(dig.returncode, 0)
        self.assertTrue(stdout.strip())

    @staticmethod
    async def _stop_process(process: asyncio.subprocess.Process) -> None:
        if process.returncode is None:
            process.terminate()
            await process.wait()


if __name__ == "__main__":
    unittest.main()
