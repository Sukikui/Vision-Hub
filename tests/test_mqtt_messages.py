import json
import unittest

from vision_hub.mqtt.messages import (
    ImageChunkMessage,
    ImageMetaMessage,
    NodeHeartbeatMessage,
    NodeReplyMessage,
    NodeRuntimeConfigPatch,
    PayloadError,
    build_broadcast_config_command,
    build_capture_command,
    build_config_command,
    parse_incoming_message,
)


class MqttMessageTest(unittest.TestCase):
    def test_parse_heartbeat(self) -> None:
        payload = b'{"node_id":"p4-001","ip":"192.168.50.20","uptime_s":1234}'

        message = parse_incoming_message("vision/nodes/p4-001/status/heartbeat", payload)

        self.assertEqual(message, NodeHeartbeatMessage(node_id="p4-001", ip="192.168.50.20", uptime_s=1234))

    def test_rejects_topic_payload_node_mismatch(self) -> None:
        payload = b'{"node_id":"p4-002","ip":"192.168.50.20","uptime_s":1234}'

        with self.assertRaises(PayloadError):
            parse_incoming_message("vision/nodes/p4-001/status/heartbeat", payload)

    def test_parse_image_meta(self) -> None:
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
        message = parse_incoming_message("vision/nodes/p4-001/image/cap-001/chunk/0", b"\xff\xd8\xff")

        self.assertEqual(message, ImageChunkMessage(node_id="p4-001", capture_id="cap-001", index=0, data=b"\xff\xd8\xff"))

    def test_parse_reply_validates_node_id(self) -> None:
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
        payload = b'{"node_id":"p4-002","ok":true}'

        with self.assertRaises(PayloadError):
            parse_incoming_message("vision/nodes/p4-001/reply/req-42", payload)

    def test_build_capture_command(self) -> None:
        command = build_capture_command("p4-001", "req-45")

        self.assertEqual(command.topic, "vision/nodes/p4-001/cmd/capture")
        self.assertEqual(json.loads(command.payload), {"request_id": "req-45"})
        self.assertEqual(command.qos, 1)
        self.assertFalse(command.retain)

    def test_build_config_command_validates_runtime_ranges(self) -> None:
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
        patch = NodeRuntimeConfigPatch(heartbeat_interval_s=0)

        with self.assertRaises(PayloadError):
            build_config_command("p4-001", "req-43", patch)

    def test_build_broadcast_config_command(self) -> None:
        patch = NodeRuntimeConfigPatch(motion_detection_enabled=True)

        command = build_broadcast_config_command("req-99", patch)

        self.assertEqual(command.topic, "vision/broadcast/cmd/config")
        self.assertEqual(json.loads(command.payload), {"request_id": "req-99", "motion_detection_enabled": True})


if __name__ == "__main__":
    unittest.main()
