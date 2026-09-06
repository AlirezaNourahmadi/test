import os
import unittest
from unittest.mock import patch

from cloudflare_tunnel import CloudflareTunnelRuntime


class CloudflareTunnelRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(
            os.environ,
            {
                "CLOUDFLARE_QUICK_TUNNEL_ENABLED": "1",
                "CLOUDFLARE_BINARY": "/usr/local/bin/cloudflared",
                "CLOUDFLARE_TUNNEL_ORIGIN": "http://127.0.0.1:11000",
                "CLOUDFLARE_EDGE_IP_VERSION": "4",
                "CLOUDFLARE_TUNNEL_PROTOCOL": "http2",
            },
            clear=False,
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_extracts_quick_tunnel_hostname(self):
        line = (
            "INF +------------------------------------------------------+ "
            "https://Calm-Unit-Test.trycloudflare.com"
        )

        self.assertEqual(
            CloudflareTunnelRuntime.extract_public_host(line),
            "calm-unit-test.trycloudflare.com",
        )

    def test_builds_ipv4_http2_quick_tunnel_command(self):
        runtime = CloudflareTunnelRuntime()

        self.assertEqual(
            runtime.command(),
            [
                "/usr/local/bin/cloudflared",
                "tunnel",
                "--no-autoupdate",
                "--edge-ip-version",
                "4",
                "--protocol",
                "http2",
                "--url",
                "http://127.0.0.1:11000",
            ],
        )

    def test_prefers_published_tunnel_host(self):
        runtime = CloudflareTunnelRuntime()
        runtime._public_host = "edge.trycloudflare.com"
        runtime._connected = True
        runtime.process = type("RunningProcess", (), {"returncode": None})()

        self.assertEqual(
            runtime.connection_host("direct.example.test"),
            "edge.trycloudflare.com",
        )

    def test_falls_back_to_direct_host_until_tunnel_is_ready(self):
        runtime = CloudflareTunnelRuntime()

        self.assertEqual(
            runtime.connection_host("direct.example.test"),
            "direct.example.test",
        )

    def test_published_host_is_not_ready_before_edge_registration(self):
        runtime = CloudflareTunnelRuntime()
        runtime._public_host = "pending.trycloudflare.com"
        runtime.process = type("RunningProcess", (), {"returncode": None})()

        self.assertFalse(runtime.ready)
        self.assertEqual(
            runtime.connection_host("direct.example.test"),
            "direct.example.test",
        )

    def test_falls_back_when_published_tunnel_process_has_stopped(self):
        runtime = CloudflareTunnelRuntime()
        runtime._public_host = "stale.trycloudflare.com"
        runtime.process = type("StoppedProcess", (), {"returncode": 1})()

        self.assertEqual(
            runtime.connection_host("direct.example.test"),
            "direct.example.test",
        )


if __name__ == "__main__":
    unittest.main()
