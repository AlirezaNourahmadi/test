import os
import tempfile
import unittest


os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="x4g-tests-"))
os.environ.setdefault("PERSISTENCE_MODE", "ephemeral")

import main
from relay_vless import UdpPacketDecoder, parse_vless_header


class UdpPacketDecoderTests(unittest.TestCase):
    def test_decodes_multiple_packets(self):
        decoder = UdpPacketDecoder()

        self.assertEqual(
            decoder.feed(b"\x00\x03one\x00\x03two"),
            [b"one", b"two"],
        )

    def test_reassembles_packet_split_across_websocket_frames(self):
        decoder = UdpPacketDecoder()

        self.assertEqual(decoder.feed(b"\x00\x05he"), [])
        self.assertEqual(decoder.feed(b"llo"), [b"hello"])

    def test_preserves_incomplete_next_packet(self):
        decoder = UdpPacketDecoder()

        self.assertEqual(decoder.feed(b"\x00\x01a\x00"), [b"a"])
        self.assertEqual(decoder.feed(b"\x01b"), [b"b"])


class VlessHeaderTests(unittest.IsolatedAsyncioTestCase):
    async def test_parses_udp_command_and_initial_packet(self):
        uuid = bytes.fromhex("3bae7199297e36e94ae739e08d849c76")
        dns_packet = b"\x12\x34dns"
        chunk = (
            b"\x00"
            + uuid
            + b"\x00"
            + b"\x02"
            + (53).to_bytes(2, "big")
            + b"\x01"
            + bytes([1, 1, 1, 1])
            + len(dns_packet).to_bytes(2, "big")
            + dns_packet
        )

        command, address, port, payload = await parse_vless_header(chunk)

        self.assertEqual(command, 2)
        self.assertEqual(address, "1.1.1.1")
        self.assertEqual(port, 53)
        self.assertEqual(UdpPacketDecoder().feed(payload), [dns_packet])


class HealthMetadataTests(unittest.IsolatedAsyncioTestCase):
    async def test_reports_writable_ephemeral_storage_as_not_durable(self):
        result = await main.health()

        self.assertTrue(result["persistence"]["writable"])
        self.assertEqual(result["persistence"]["mode"], "ephemeral")
        self.assertFalse(result["persistence"]["durable"])


if __name__ == "__main__":
    unittest.main()
