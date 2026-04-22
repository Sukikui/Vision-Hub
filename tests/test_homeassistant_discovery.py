"""Tests for Home Assistant MQTT Discovery payloads."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml

from tools.render_homeassistant_dashboard import parse_nodes, render_dashboard
from vision_hub.homeassistant import (
    HomeAssistantCommand,
    HomeAssistantCommandName,
    HomeAssistantError,
    HomeAssistantMqttConfig,
    HomeAssistantMqttDiscovery,
)
from vision_hub.mqtt.topics import CommandName


class HomeAssistantMqttDiscoveryTest(unittest.TestCase):
    """Unit tests for Home Assistant MQTT discovery and state builders."""

    def test_node_discovery_groups_entities_under_stable_device(self) -> None:
        """Expose one ESP32 node as one Home Assistant device."""

        discovery = HomeAssistantMqttDiscovery()

        messages = discovery.node_discovery_messages("p4-001")

        self.assertEqual(len(messages), 27)
        self.assertIn("homeassistant/binary_sensor/vision_hub_p4_001_online/config", _topics(messages))
        self.assertIn("homeassistant/image/vision_hub_p4_001_latest_capture/config", _topics(messages))
        self.assertIn("homeassistant/switch/vision_hub_p4_001_motion_enabled/config", _topics(messages))
        self.assertIn("homeassistant/select/vision_hub_p4_001_ir_mode/config", _topics(messages))
        self.assertIn("homeassistant/number/vision_hub_p4_001_heartbeat_interval/config", _topics(messages))
        for message in messages:
            payload = message.payload_json
            self.assertTrue(message.retain)
            self.assertEqual(payload["availability_topic"], "vision-hub/status")
            self.assertEqual(payload["payload_available"], "online")
            self.assertEqual(payload["payload_not_available"], "offline")
            self.assertEqual(payload["device"]["identifiers"], ["vision_hub_node_p4_001"])
            self.assertEqual(payload["device"]["name"], "P4-001")
            self.assertEqual(payload["device"]["model"], "ESP32-P4 Vision Node")
            self.assertEqual(payload["device"]["via_device"], "vision_hub_rpi")

    def test_node_entities_use_separate_state_capture_and_detection_topics(self) -> None:
        """Keep Home Assistant subscribed to clean hub-level state topics."""

        discovery = HomeAssistantMqttDiscovery()
        messages = _payloads_by_default_entity_id(discovery.node_discovery_messages("p4-001"))

        self.assertEqual(messages["binary_sensor.p4_001_online"]["state_topic"], "vision-hub/nodes/p4-001/state")
        self.assertEqual(messages["sensor.p4_001_last_event"]["state_topic"], "vision-hub/nodes/p4-001/state")
        self.assertEqual(messages["binary_sensor.p4_001_capture_error"]["device_class"], "problem")
        self.assertEqual(messages["sensor.p4_001_last_capture"]["state_topic"], "vision-hub/nodes/p4-001/capture")
        self.assertEqual(messages["sensor.p4_001_last_image_size"]["unit_of_measurement"], "B")
        self.assertEqual(messages["binary_sensor.p4_001_last_capture_ok"]["state_topic"], "vision-hub/nodes/p4-001/capture")
        self.assertEqual(messages["binary_sensor.p4_001_person_detected"]["state_topic"], "vision-hub/nodes/p4-001/detection")
        self.assertEqual(messages["sensor.p4_001_inference_ms"]["unit_of_measurement"], "ms")

    def test_node_image_and_controls_are_discovered(self) -> None:
        """Expose the latest JPEG and node controls for one node."""

        discovery = HomeAssistantMqttDiscovery()
        messages = _payloads_by_default_entity_id(discovery.node_discovery_messages("p4-001"))

        image = messages["image.p4_001_latest_capture"]
        ping = messages["button.p4_001_ping"]
        capture = messages["button.p4_001_capture"]
        reboot = messages["button.p4_001_reboot"]
        motion = messages["switch.p4_001_motion_enabled"]
        ir_mode = messages["select.p4_001_ir_mode"]
        heartbeat = messages["number.p4_001_heartbeat_interval"]

        self.assertEqual(image["image_topic"], "vision-hub/nodes/p4-001/latest/image")
        self.assertEqual(image["content_type"], "image/jpeg")
        self.assertEqual(ping["command_topic"], "vision-hub/commands/p4-001/ping")
        self.assertEqual(capture["command_topic"], "vision-hub/commands/p4-001/capture")
        self.assertEqual(reboot["command_topic"], "vision-hub/commands/p4-001/reboot")
        self.assertEqual(reboot["device_class"], "restart")
        self.assertEqual(motion["command_topic"], "vision-hub/commands/p4-001/motion_enabled/set")
        self.assertIn("motion_enabled", motion["value_template"])
        self.assertEqual(ir_mode["command_topic"], "vision-hub/commands/p4-001/ir_mode/set")
        self.assertEqual(ir_mode["options"], ["off", "on", "capture"])
        self.assertEqual(heartbeat["command_topic"], "vision-hub/commands/p4-001/heartbeat_interval/set")
        self.assertEqual(heartbeat["min"], 1)
        self.assertEqual(heartbeat["max"], 3600)

    def test_hub_discovery_exposes_system_entities(self) -> None:
        """Expose Raspberry Pi hub state as one Home Assistant device."""

        discovery = HomeAssistantMqttDiscovery()

        messages = _payloads_by_default_entity_id(discovery.hub_discovery_messages())

        self.assertEqual(len(messages), 19)
        self.assertEqual(messages["binary_sensor.vision_hub_online"]["state_topic"], "vision-hub/status")
        self.assertNotIn("availability_topic", messages["binary_sensor.vision_hub_online"])
        self.assertEqual(messages["binary_sensor.mosquitto_available"]["state_topic"], "vision-hub/system/state")
        self.assertEqual(messages["binary_sensor.vision_hub_storage_pressure"]["device_class"], "problem")
        self.assertEqual(messages["sensor.vision_hub_storage_free"]["unit_of_measurement"], "B")
        self.assertEqual(messages["sensor.vision_hub_capture_count"]["state_class"], "measurement")
        self.assertEqual(messages["sensor.vision_hub_retention_deleted_bytes"]["unit_of_measurement"], "B")
        self.assertEqual(messages["sensor.vision_hub_memory_used"]["unit_of_measurement"], "%")
        self.assertEqual(messages["sensor.vision_hub_cpu_temperature"]["device_class"], "temperature")
        for entity_id, payload in messages.items():
            self.assertEqual(payload["device"]["identifiers"], ["vision_hub_rpi"], entity_id)
            self.assertEqual(payload["device"]["name"], "Vision-Hub", entity_id)

    def test_state_update_publications_are_retained_json(self) -> None:
        """Publish clean retained JSON states for Home Assistant entities."""

        discovery = HomeAssistantMqttDiscovery()

        hub_state = discovery.build_hub_state_update({"mqtt_connected": True, "storage_free_bytes": 123})
        node_state = discovery.build_node_state_update("p4-001", {"online": True, "ip": "192.168.50.20"})
        capture = discovery.build_node_capture_update("p4-001", {"last_capture_id": "cap-1", "last_capture_ok": True})
        detection = discovery.build_node_detection_update("p4-001", {"person_detected": True, "person_count": 2})

        self.assertEqual(hub_state.topic, "vision-hub/system/state")
        self.assertEqual(hub_state.payload_json, {"mqtt_connected": True, "storage_free_bytes": 123})
        self.assertTrue(hub_state.retain)
        self.assertEqual(node_state.topic, "vision-hub/nodes/p4-001/state")
        self.assertEqual(node_state.payload_json, {"online": True, "ip": "192.168.50.20"})
        self.assertTrue(node_state.retain)
        self.assertEqual(capture.topic, "vision-hub/nodes/p4-001/capture")
        self.assertEqual(capture.payload_json, {"last_capture_id": "cap-1", "last_capture_ok": True})
        self.assertTrue(capture.retain)
        self.assertEqual(detection.topic, "vision-hub/nodes/p4-001/detection")
        self.assertEqual(detection.payload_json, {"person_detected": True, "person_count": 2})
        self.assertTrue(detection.retain)

    def test_availability_and_image_updates_are_retained(self) -> None:
        """Publish retained hub availability and latest JPEG payloads."""

        discovery = HomeAssistantMqttDiscovery()

        availability = discovery.build_hub_availability(online=True)
        image = discovery.build_node_image_update("p4-001", b"\xff\xd8jpeg\xff\xd9")

        self.assertEqual(availability.topic, "vision-hub/status")
        self.assertEqual(availability.payload, b"online")
        self.assertTrue(availability.retain)
        self.assertEqual(image.topic, "vision-hub/nodes/p4-001/latest/image")
        self.assertEqual(image.payload, b"\xff\xd8jpeg\xff\xd9")
        self.assertTrue(image.retain)

    def test_parse_homeassistant_button_commands(self) -> None:
        """Parse clean Home Assistant buttons into node command requests."""

        discovery = HomeAssistantMqttDiscovery()

        ping = discovery.parse_command("vision-hub/commands/p4-001/ping", b"PRESS")
        capture = discovery.parse_command("vision-hub/commands/p4-001/capture", b"PRESS")
        reboot = discovery.parse_command("vision-hub/commands/p4-001/reboot", b"PRESS")

        self.assertEqual(ping, HomeAssistantCommand("p4-001", HomeAssistantCommandName.PING, b"PRESS"))
        assert ping is not None
        self.assertEqual(ping.esp_command_name, CommandName.PING)
        assert capture is not None
        self.assertEqual(capture.esp_command_name, CommandName.CAPTURE)
        assert reboot is not None
        self.assertEqual(reboot.esp_command_name, CommandName.REBOOT)

    def test_parse_homeassistant_config_controls(self) -> None:
        """Parse clean Home Assistant config controls into ESP32 config patches."""

        discovery = HomeAssistantMqttDiscovery()

        motion = discovery.parse_command("vision-hub/commands/p4-001/motion_enabled/set", b"OFF")
        ir_mode = discovery.parse_command("vision-hub/commands/p4-001/ir_mode/set", b"capture")
        heartbeat = discovery.parse_command("vision-hub/commands/p4-001/heartbeat_interval/set", b"30")

        self.assertEqual(motion, HomeAssistantCommand("p4-001", HomeAssistantCommandName.MOTION_ENABLED, b"OFF", False))
        assert motion is not None
        self.assertEqual(motion.esp_command_name, CommandName.CONFIG)
        self.assertEqual(motion.to_config_patch(), {"motion_detection_enabled": False})
        assert ir_mode is not None
        self.assertEqual(ir_mode.to_config_patch(), {"ir_illuminator_mode": "capture"})
        assert heartbeat is not None
        self.assertEqual(heartbeat.to_config_patch(), {"heartbeat_interval_s": 30})

    def test_parse_command_ignores_unrelated_topics(self) -> None:
        """Return None for topics outside the Home Assistant command namespace."""

        discovery = HomeAssistantMqttDiscovery()

        self.assertIsNone(discovery.parse_command("vision/nodes/p4-001/cmd/capture", b"PRESS"))

    def test_parse_command_rejects_bad_payload_and_command_shape(self) -> None:
        """Reject malformed Home Assistant command publications."""

        discovery = HomeAssistantMqttDiscovery()

        with self.assertRaisesRegex(HomeAssistantError, "button payload must be PRESS"):
            discovery.parse_command("vision-hub/commands/p4-001/capture", b"NOPE")
        with self.assertRaisesRegex(HomeAssistantError, "must use the setting/set command topic"):
            discovery.parse_command("vision-hub/commands/p4-001/motion_enabled", b"ON")
        with self.assertRaisesRegex(HomeAssistantError, "must use the button command topic"):
            discovery.parse_command("vision-hub/commands/p4-001/capture/set", b"PRESS")
        with self.assertRaisesRegex(HomeAssistantError, "between 1 and 3600"):
            discovery.parse_command("vision-hub/commands/p4-001/heartbeat_interval/set", b"0")

    def test_custom_prefixes_are_applied(self) -> None:
        """Support custom MQTT prefixes without changing entity payloads."""

        discovery = HomeAssistantMqttDiscovery(
            HomeAssistantMqttConfig(
                discovery_prefix="/ha/",
                state_prefix="/hub/state/",
                command_prefix="/hub/cmd/",
            )
        )

        message = discovery.node_discovery_messages("p4-001")[0]
        command = discovery.parse_command("hub/cmd/p4-001/reboot", b"PRESS")

        self.assertEqual(message.topic, "ha/binary_sensor/vision_hub_p4_001_online/config")
        self.assertEqual(message.payload_json["state_topic"], "hub/state/nodes/p4-001/state")
        assert command is not None
        self.assertEqual(command.command, HomeAssistantCommandName.REBOOT)


class HomeAssistantDashboardTest(unittest.TestCase):
    """Unit tests for generated Home Assistant dashboard YAML."""

    def test_render_dashboard_contains_camera_cards_for_each_node(self) -> None:
        """Generate a valid dashboard with latest image cards for nodes."""

        nodes = parse_nodes("p4-001,p4-002")

        dashboard = yaml.safe_load(render_dashboard(nodes))

        self.assertEqual(len(dashboard["views"]), 5)
        cameras = dashboard["views"][1]
        self.assertEqual(cameras["title"], "Cameras")
        self.assertEqual(cameras["cards"][0]["cards"][0]["entity"], "image.p4_001_latest_capture")
        self.assertEqual(cameras["cards"][1]["cards"][0]["entity"], "image.p4_002_latest_capture")
        self.assertIn("button.p4_001_capture", cameras["cards"][0]["cards"][4]["entities"])

    def test_render_dashboard_contains_system_and_capture_views(self) -> None:
        """Generate system health and capture archive dashboard views."""

        dashboard = yaml.safe_load(render_dashboard(parse_nodes("p4-001")))
        views_by_path = {view["path"]: view for view in dashboard["views"]}

        self.assertIn("sensor.vision_hub_storage_free", views_by_path["overview"]["cards"][1]["entities"])
        self.assertIn("sensor.vision_hub_retention_deleted_files", views_by_path["system"]["cards"][2]["entities"])
        self.assertIn("Media > captures", views_by_path["captures"]["cards"][0]["content"])

    def test_render_dashboard_rejects_duplicate_nodes(self) -> None:
        """Reject duplicate nodes before rendering a dashboard."""

        with self.assertRaisesRegex(ValueError, "duplicate node id"):
            parse_nodes("p4-001,p4-001")

    def test_render_dashboard_writes_valid_yaml_file(self) -> None:
        """Render dashboard YAML to a file path."""

        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "vision-hub.yaml"
            output.write_text(render_dashboard(parse_nodes("p4-001")), encoding="utf-8")

            loaded = yaml.safe_load(output.read_text(encoding="utf-8"))

        self.assertEqual(loaded["views"][1]["cards"][0]["cards"][0]["entity"], "image.p4_001_latest_capture")


def _topics(messages: tuple) -> set[str]:
    """Return the topic set for a tuple of MQTT publications."""

    return {message.topic for message in messages}


def _payloads_by_default_entity_id(messages: tuple) -> dict[str, dict]:
    """Index MQTT Discovery payloads by expected Home Assistant entity id."""

    return {message.payload_json["default_entity_id"]: message.payload_json for message in messages}


if __name__ == "__main__":
    unittest.main()
