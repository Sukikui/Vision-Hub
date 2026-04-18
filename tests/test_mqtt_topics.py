import unittest

from vision_hub.mqtt.topics import CommandName, IncomingTopicKind, TopicError, build_node_command_topic, parse_incoming_topic


class MqttTopicTest(unittest.TestCase):
    def test_parse_image_chunk_topic(self) -> None:
        topic = parse_incoming_topic("vision/nodes/p4-001/image/cap-42/chunk/7")

        self.assertIsNotNone(topic)
        assert topic is not None
        self.assertEqual(topic.kind, IncomingTopicKind.IMAGE_CHUNK)
        self.assertEqual(topic.node_id, "p4-001")
        self.assertEqual(topic.capture_id, "cap-42")
        self.assertEqual(topic.chunk_index, 7)

    def test_builds_node_command_topic(self) -> None:
        self.assertEqual(
            build_node_command_topic("p4-001", CommandName.CAPTURE),
            "vision/nodes/p4-001/cmd/capture",
        )

    def test_rejects_unknown_command(self) -> None:
        with self.assertRaises(TopicError):
            build_node_command_topic("p4-001", "flash")


if __name__ == "__main__":
    unittest.main()
