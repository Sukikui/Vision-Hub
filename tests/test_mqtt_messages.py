"""Tests for MQTT message parsing and command payload builders."""

import json
import unittest

from vision_hub.mqtt.messages import (
    ImageChunkMessage,
    ImageMetaMessage,
    NodeHeartbeatMessage,
    NodeReplyMessage,
    NodeRuntimeConfigPatch,
    PayloadError,
    build_capture_command,
    build_config_command,
    parse_incoming_message,
)


class MqttMessageTest(unittest.TestCase):
    """Unit tests for MQTT payload models and builders."""

    def test_parse_heartbeat(self) -> None:
        """Parse heartbeat JSON payloads into typed messages."""

        payload = b'{"node_id":"p4-001","ip":"192.168.50.20","uptime_s":1234}'

        message = parse_incoming_message("vision/nodes/p4-001/status/heartbeat", payload)

        self.assertEqual(message, NodeHeartbeatMessage(node_id="p4-001", ip="192.168.50.20", uptime_s=1234))

    def test_rejects_topic_payload_node_mismatch(self) -> None:
        """Reject messages whose payload node id does not match the topic."""

        payload = b'{"node_id":"p4-002","ip":"192.168.50.20","uptime_s":1234}'

        with self.assertRaises(PayloadError):
            parse_incoming_message("vision/nodes/p4-001/status/heartbeat", payload)

    def test_parse_image_meta(self) -> None:
        """Parse image metadata payloads from capture topics."""

        payload = b'{"capture_id":"cap-001","content_type":"image/jpeg","total_size":48123,"chunk_size":2048,"chunk_count":24}'

        message = parse_incoming_message("vision/nodes/p4-001/image/cap-001/meta", payload)

        self.assertEqual(
            message,
            ImageMetaMessage(
                node_id="p4-001",
                capture_id="cap-001",
                content_type="image/jpeg",
                total_size=48123,
                chunk_size=2048,
                chunk_count=24,
            ),
        )

    def test_parse_image_chunk_keeps_binary_payload(self) -> None:
        """Keep image chunk payloads as raw bytes."""

        message = parse_incoming_message("vision/nodes/p4-001/image/cap-001/chunk/0", b"\xff\xd8\xff")

        self.assertEqual(message, ImageChunkMessage(node_id="p4-001", capture_id="cap-001", index=0, data=b"\xff\xd8\xff"))

    def test_parse_reply_validates_node_id(self) -> None:
        """Parse command replies and preserve the full JSON body."""

        payload = b'{"node_id":"p4-001","ok":true,"uptime_s":123}'

        message = parse_incoming_message("vision/nodes/p4-001/reply/req-42", payload)

        self.assertEqual(
            message,
            NodeReplyMessage(
                node_id="p4-001",
                request_id="req-42",
                payload={"node_id": "p4-001", "ok": True, "uptime_s": 123},
            ),
        )

    def test_rejects_reply_node_mismatch(self) -> None:
        """Reject replies whose payload node id does not match the topic."""

        payload = b'{"node_id":"p4-002","ok":true}'

        with self.assertRaises(PayloadError):
            parse_incoming_message("vision/nodes/p4-001/reply/req-42", payload)

    def test_build_capture_command(self) -> None:
        """Build capture commands with request ids and default MQTT flags."""

        command = build_capture_command("p4-001", "req-45")

        self.assertEqual(command.topic, "vision/nodes/p4-001/cmd/capture")
        self.assertEqual(json.loads(command.payload), {"request_id": "req-45"})
        self.assertEqual(command.qos, 1)
        self.assertFalse(command.retain)

    def test_build_config_command_validates_runtime_ranges(self) -> None:
        """Build config commands and validate runtime field ranges."""

        patch = NodeRuntimeConfigPatch(heartbeat_interval_s=30, motion_cooldown_ms=5000, ir_illuminator_mode="capture")

        command = build_config_command("p4-001", "req-43", patch)

        self.assertEqual(command.topic, "vision/nodes/p4-001/cmd/config")
        self.assertEqual(
            json.loads(command.payload),
            {
                "request_id": "req-43",
                "heartbeat_interval_s": 30,
                "motion_cooldown_ms": 5000,
                "ir_illuminator_mode": "capture",
            },
        )

    def test_rejects_invalid_config_patch(self) -> None:
        """Reject invalid runtime configuration patches."""

        patch = NodeRuntimeConfigPatch(heartbeat_interval_s=0)

        with self.assertRaises(PayloadError):
            build_config_command("p4-001", "req-43", patch)


if __name__ == "__main__":
    unittest.main()
