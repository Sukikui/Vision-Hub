"""MQTT Discovery contract between Vision-Hub and Home Assistant."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from vision_hub.homeassistant.ids import node_slug
from vision_hub.mqtt.topics import CommandName, TopicError, validate_topic_segment


class HomeAssistantError(ValueError):
    """Raised when a Home Assistant MQTT payload or topic is invalid."""


class HomeAssistantCommandName(StrEnum):
    """Commands accepted from Home Assistant through clean hub topics."""

    PING = "ping"
    CAPTURE = "capture"
    REBOOT = "reboot"
    MOTION_ENABLED = "motion_enabled"
    IR_MODE = "ir_mode"
    HEARTBEAT_INTERVAL = "heartbeat_interval"


JsonObject = dict[str, Any]

_BUTTON_COMMANDS = {
    HomeAssistantCommandName.PING,
    HomeAssistantCommandName.CAPTURE,
    HomeAssistantCommandName.REBOOT,
}
_CONFIG_COMMANDS = {
    HomeAssistantCommandName.MOTION_ENABLED,
    HomeAssistantCommandName.IR_MODE,
    HomeAssistantCommandName.HEARTBEAT_INTERVAL,
}


@dataclass(frozen=True)
class HomeAssistantMqttConfig:
    """Home Assistant MQTT topic and device identity configuration.

    Attributes:
        discovery_prefix: Home Assistant MQTT discovery prefix.
        state_prefix: Prefix used by Vision-Hub for clean HA-facing states.
        command_prefix: Prefix used by Home Assistant controls to command the hub.
        hub_device_identifier: Stable Home Assistant device identifier for the RPi.
        hub_name: Home Assistant device name for the Raspberry Pi hub.
        manufacturer: Manufacturer name shown in Home Assistant device metadata.
        hub_model: Model string shown for the Raspberry Pi hub.
        node_model: Model string shown for ESP32 vision nodes.
    """

    discovery_prefix: str = "homeassistant"
    state_prefix: str = "vision-hub"
    command_prefix: str = "vision-hub/commands"
    hub_device_identifier: str = "vision_hub_rpi"
    hub_name: str = "Vision-Hub"
    manufacturer: str = "Vision-Hub"
    hub_model: str = "Raspberry Pi Edge Hub"
    node_model: str = "ESP32-P4 Vision Node"

    def __post_init__(self) -> None:
        """Normalize topic prefixes and validate static device identifiers."""

        object.__setattr__(self, "discovery_prefix", _normalize_topic_prefix(self.discovery_prefix, "discovery_prefix"))
        object.__setattr__(self, "state_prefix", _normalize_topic_prefix(self.state_prefix, "state_prefix"))
        object.__setattr__(self, "command_prefix", _normalize_topic_prefix(self.command_prefix, "command_prefix"))
        object.__setattr__(self, "hub_device_identifier", _safe_identifier(self.hub_device_identifier, "hub_device_identifier"))


@dataclass(frozen=True)
class MqttPublication:
    """MQTT publication prepared by the Home Assistant integration layer.

    Attributes:
        topic: MQTT topic to publish on.
        payload: Raw MQTT payload bytes.
        qos: MQTT QoS level.
        retain: Whether the broker should retain the payload.
    """

    topic: str
    payload: bytes
    qos: int = 0
    retain: bool = False

    @property
    def payload_text(self) -> str:
        """Decode the publication payload as UTF-8 text.

        Returns:
            Decoded payload string.
        """

        return self.payload.decode("utf-8")

    @property
    def payload_json(self) -> JsonObject:
        """Decode the publication payload as a JSON object.

        Returns:
            Decoded JSON object.

        Raises:
            HomeAssistantError: If the payload is not a JSON object.
        """

        decoded = json.loads(self.payload_text)
        if not isinstance(decoded, dict):
            raise HomeAssistantError("MQTT publication payload is not a JSON object")
        return decoded


@dataclass(frozen=True)
class HomeAssistantCommand:
    """Command received from a Home Assistant MQTT control.

    Attributes:
        node_id: Target ESP32 node identifier.
        command: Clean Home Assistant command name.
        payload: Raw MQTT payload.
        value: Parsed command value for config-style controls.
    """

    node_id: str
    command: HomeAssistantCommandName
    payload: bytes
    value: bool | int | str | None = None

    @property
    def esp_command_name(self) -> CommandName:
        """Return the matching ESP32 firmware command name.

        Returns:
            Command enum accepted by the ESP32 MQTT command builders.
        """

        if self.command in _CONFIG_COMMANDS:
            return CommandName.CONFIG
        return CommandName(self.command.value)

    def to_config_patch(self) -> JsonObject:
        """Convert a config-style HA command into a firmware config patch.

        Returns:
            JSON patch field understood by the ESP32 runtime config command.

        Raises:
            HomeAssistantError: If the command is not a config-style command.
        """

        if self.command == HomeAssistantCommandName.MOTION_ENABLED:
            return {"motion_detection_enabled": self.value}
        if self.command == HomeAssistantCommandName.IR_MODE:
            return {"ir_illuminator_mode": self.value}
        if self.command == HomeAssistantCommandName.HEARTBEAT_INTERVAL:
            return {"heartbeat_interval_s": self.value}
        raise HomeAssistantError(f"{self.command.value} is not a config command")


@dataclass(frozen=True)
class _EntityDefinition:
    """Internal MQTT discovery entity description.

    Attributes:
        component: Home Assistant MQTT component name.
        suffix: Unique suffix used in the entity unique id.
        name: Human-readable Home Assistant entity name.
        default_entity_id: Preferred Home Assistant entity id.
        payload: Component-specific discovery payload.
        use_hub_availability: Whether the entity follows hub availability.
    """

    component: str
    suffix: str
    name: str
    default_entity_id: str
    payload: JsonObject
    use_hub_availability: bool = True


class HomeAssistantMqttDiscovery:
    """Build Home Assistant MQTT Discovery and state publications.

    The class exposes Vision-Hub data as clean Home Assistant MQTT entities.
    ESP32 firmware topics stay internal to the hub; Home Assistant only sees
    normalized node state, capture state, inference state, latest image
    payloads, and clean command topics.
    """

    def __init__(self, config: HomeAssistantMqttConfig | None = None) -> None:
        """Initialize the Home Assistant MQTT builder.

        Args:
            config: Optional topic and identity configuration.
        """

        self.config = config or HomeAssistantMqttConfig()

    @property
    def hub_availability_topic(self) -> str:
        """Return the retained topic that marks the hub online or offline."""

        return f"{self.config.state_prefix}/status"

    @property
    def hub_state_topic(self) -> str:
        """Return the retained JSON topic for Raspberry Pi system state."""

        return f"{self.config.state_prefix}/system/state"

    def node_state_topic(self, node_id: str) -> str:
        """Return the retained JSON state topic for one ESP32 node.

        Args:
            node_id: ESP32 node identifier.

        Returns:
            Clean Home Assistant-facing node state topic.
        """

        return f"{self.config.state_prefix}/nodes/{_safe_segment(node_id, 'node_id')}/state"

    def node_capture_topic(self, node_id: str) -> str:
        """Return the retained JSON capture topic for one ESP32 node.

        Args:
            node_id: ESP32 node identifier.

        Returns:
            Clean capture state topic consumed by Home Assistant entities.
        """

        return f"{self.config.state_prefix}/nodes/{_safe_segment(node_id, 'node_id')}/capture"

    def node_detection_topic(self, node_id: str) -> str:
        """Return the retained JSON detection topic for one ESP32 node.

        Args:
            node_id: ESP32 node identifier.

        Returns:
            Clean detection topic consumed by Home Assistant entities.
        """

        return f"{self.config.state_prefix}/nodes/{_safe_segment(node_id, 'node_id')}/detection"

    def node_image_topic(self, node_id: str) -> str:
        """Return the retained latest JPEG topic for one ESP32 node.

        Args:
            node_id: ESP32 node identifier.

        Returns:
            MQTT Image topic carrying the full latest JPEG payload.
        """

        return f"{self.config.state_prefix}/nodes/{_safe_segment(node_id, 'node_id')}/latest/image"

    def node_command_topic(self, node_id: str, command: HomeAssistantCommandName | str) -> str:
        """Return a Home Assistant command topic for one node.

        Args:
            node_id: ESP32 node identifier.
            command: Clean Home Assistant command name.

        Returns:
            MQTT topic used by a Home Assistant control entity.

        Raises:
            HomeAssistantError: If the command is unsupported.
        """

        command_name = _command_name(command)
        safe_node_id = _safe_segment(node_id, "node_id")
        if command_name in _BUTTON_COMMANDS:
            return f"{self.config.command_prefix}/{safe_node_id}/{command_name.value}"
        return f"{self.config.command_prefix}/{safe_node_id}/{command_name.value}/set"

    def hub_discovery_messages(self) -> tuple[MqttPublication, ...]:
        """Build MQTT Discovery publications for Raspberry Pi hub entities.

        Returns:
            Retained MQTT discovery publications for the hub device.
        """

        device = self._hub_device()
        definitions = (
            _binary_entity(
                suffix="online",
                name="Online",
                default_entity_id="binary_sensor.vision_hub_online",
                state_topic=self.hub_availability_topic,
                value_template=None,
                payload_on="online",
                payload_off="offline",
                device_class="connectivity",
                use_hub_availability=False,
            ),
            _binary_entity(
                suffix="mqtt_connected",
                name="MQTT connected",
                default_entity_id="binary_sensor.vision_hub_mqtt_connected",
                state_topic=self.hub_state_topic,
                field="mqtt_connected",
                device_class="connectivity",
            ),
            _binary_entity(
                suffix="mosquitto_available",
                name="Mosquitto available",
                default_entity_id="binary_sensor.mosquitto_available",
                state_topic=self.hub_state_topic,
                field="mosquitto_available",
                device_class="connectivity",
            ),
            _binary_entity(
                suffix="inference_ready",
                name="Inference ready",
                default_entity_id="binary_sensor.vision_hub_inference_ready",
                state_topic=self.hub_state_topic,
                field="inference_ready",
            ),
            _binary_entity(
                suffix="storage_pressure",
                name="Storage pressure",
                default_entity_id="binary_sensor.vision_hub_storage_pressure",
                state_topic=self.hub_state_topic,
                field="storage_pressure",
                device_class="problem",
            ),
            _sensor_entity(
                suffix="storage_free",
                name="Storage free",
                default_entity_id="sensor.vision_hub_storage_free",
                state_topic=self.hub_state_topic,
                field="storage_free_bytes",
                device_class="data_size",
                state_class="measurement",
                unit="B",
            ),
            _sensor_entity(
                suffix="storage_used_percent",
                name="Storage used",
                default_entity_id="sensor.vision_hub_storage_used_percent",
                state_topic=self.hub_state_topic,
                field="storage_used_percent",
                state_class="measurement",
                unit="%",
                icon="mdi:harddisk",
            ),
            _sensor_entity(
                suffix="capture_count",
                name="Capture count",
                default_entity_id="sensor.vision_hub_capture_count",
                state_topic=self.hub_state_topic,
                field="capture_count",
                state_class="measurement",
                icon="mdi:image-multiple",
            ),
            _timestamp_entity(
                suffix="last_capture",
                name="Last capture",
                default_entity_id="sensor.vision_hub_last_capture",
                state_topic=self.hub_state_topic,
                field="last_capture",
            ),
            _timestamp_entity(
                suffix="last_age_cleanup",
                name="Last age cleanup",
                default_entity_id="sensor.vision_hub_last_age_cleanup",
                state_topic=self.hub_state_topic,
                field="last_age_cleanup",
            ),
            _sensor_entity(
                suffix="retention_deleted_files",
                name="Retention deleted files",
                default_entity_id="sensor.vision_hub_retention_deleted_files",
                state_topic=self.hub_state_topic,
                field="retention_deleted_files",
                state_class="measurement",
                entity_category="diagnostic",
                icon="mdi:delete-sweep",
            ),
            _sensor_entity(
                suffix="retention_deleted_bytes",
                name="Retention deleted bytes",
                default_entity_id="sensor.vision_hub_retention_deleted_bytes",
                state_topic=self.hub_state_topic,
                field="retention_deleted_bytes",
                device_class="data_size",
                state_class="measurement",
                unit="B",
                entity_category="diagnostic",
            ),
            _sensor_entity(
                suffix="uptime",
                name="Uptime",
                default_entity_id="sensor.vision_hub_uptime",
                state_topic=self.hub_state_topic,
                field="vision_hub_uptime_s",
                device_class="duration",
                state_class="measurement",
                unit="s",
                entity_category="diagnostic",
            ),
            _sensor_entity(
                suffix="version",
                name="Version",
                default_entity_id="sensor.vision_hub_version",
                state_topic=self.hub_state_topic,
                field="vision_hub_version",
                entity_category="diagnostic",
                icon="mdi:source-branch",
            ),
            _sensor_entity(
                suffix="rpi_cpu_temperature",
                name="CPU temperature",
                default_entity_id="sensor.vision_hub_cpu_temperature",
                state_topic=self.hub_state_topic,
                field="rpi_cpu_temperature_c",
                device_class="temperature",
                state_class="measurement",
                unit="°C",
            ),
            _sensor_entity(
                suffix="rpi_memory_used",
                name="Memory used",
                default_entity_id="sensor.vision_hub_memory_used",
                state_topic=self.hub_state_topic,
                field="rpi_memory_used_percent",
                state_class="measurement",
                unit="%",
                icon="mdi:memory",
            ),
            _sensor_entity(
                suffix="rpi_load",
                name="Load",
                default_entity_id="sensor.vision_hub_load",
                state_topic=self.hub_state_topic,
                field="rpi_load_1m",
                state_class="measurement",
                entity_category="diagnostic",
                icon="mdi:gauge",
            ),
            _sensor_entity(
                suffix="rpi_admin_ip",
                name="Admin IP",
                default_entity_id="sensor.vision_hub_admin_ip",
                state_topic=self.hub_state_topic,
                field="rpi_admin_ip",
                entity_category="diagnostic",
                icon="mdi:wifi",
            ),
            _sensor_entity(
                suffix="rpi_field_ip",
                name="Field IP",
                default_entity_id="sensor.vision_hub_field_ip",
                state_topic=self.hub_state_topic,
                field="rpi_field_ip",
                entity_category="diagnostic",
                icon="mdi:ethernet",
            ),
        )
        return tuple(self._discovery_publication(entity, self._hub_slug(), device) for entity in definitions)

    def node_discovery_messages(self, node_id: str) -> tuple[MqttPublication, ...]:
        """Build MQTT Discovery publications for one ESP32 node.

        Args:
            node_id: ESP32 node identifier.

        Returns:
            Retained MQTT discovery publications for node entities.
        """

        safe_node_id = _safe_segment(node_id, "node_id")
        slug = node_slug(safe_node_id)
        device = self._node_device(safe_node_id, slug)
        state_topic = self.node_state_topic(safe_node_id)
        capture_topic = self.node_capture_topic(safe_node_id)
        detection_topic = self.node_detection_topic(safe_node_id)
        definitions = (
            _binary_entity(
                suffix="online",
                name="Online",
                default_entity_id=f"binary_sensor.{slug}_online",
                state_topic=state_topic,
                field="online",
                device_class="connectivity",
            ),
            _sensor_entity(
                suffix="ip",
                name="IP address",
                default_entity_id=f"sensor.{slug}_ip",
                state_topic=state_topic,
                field="ip",
                entity_category="diagnostic",
                icon="mdi:ip-network",
            ),
            _sensor_entity(
                suffix="uptime",
                name="Uptime",
                default_entity_id=f"sensor.{slug}_uptime",
                state_topic=state_topic,
                field="uptime_s",
                device_class="duration",
                state_class="measurement",
                unit="s",
                entity_category="diagnostic",
            ),
            _timestamp_entity(
                suffix="last_seen",
                name="Last seen",
                default_entity_id=f"sensor.{slug}_last_seen",
                state_topic=state_topic,
                field="last_seen",
            ),
            _binary_entity(
                suffix="motion",
                name="Motion",
                default_entity_id=f"binary_sensor.{slug}_motion",
                state_topic=state_topic,
                field="motion_detected",
                device_class="motion",
            ),
            _sensor_entity(
                suffix="last_event",
                name="Last event",
                default_entity_id=f"sensor.{slug}_last_event",
                state_topic=state_topic,
                field="last_event",
                entity_category="diagnostic",
                icon="mdi:message-alert",
            ),
            _timestamp_entity(
                suffix="last_boot_event",
                name="Last boot event",
                default_entity_id=f"sensor.{slug}_last_boot_event",
                state_topic=state_topic,
                field="last_boot_event",
            ),
            _timestamp_entity(
                suffix="last_config_update",
                name="Last config update",
                default_entity_id=f"sensor.{slug}_last_config_update",
                state_topic=state_topic,
                field="last_config_update",
            ),
            _binary_entity(
                suffix="capture_error",
                name="Capture error",
                default_entity_id=f"binary_sensor.{slug}_capture_error",
                state_topic=state_topic,
                field="capture_error",
                device_class="problem",
            ),
            _timestamp_entity(
                suffix="last_capture",
                name="Last capture",
                default_entity_id=f"sensor.{slug}_last_capture",
                state_topic=capture_topic,
                field="last_capture",
            ),
            _sensor_entity(
                suffix="last_capture_id",
                name="Last capture ID",
                default_entity_id=f"sensor.{slug}_last_capture_id",
                state_topic=capture_topic,
                field="last_capture_id",
                entity_category="diagnostic",
                icon="mdi:identifier",
            ),
            _sensor_entity(
                suffix="last_image_size",
                name="Last image size",
                default_entity_id=f"sensor.{slug}_last_image_size",
                state_topic=capture_topic,
                field="last_image_size_bytes",
                device_class="data_size",
                state_class="measurement",
                unit="B",
                entity_category="diagnostic",
            ),
            _binary_entity(
                suffix="last_capture_ok",
                name="Last capture OK",
                default_entity_id=f"binary_sensor.{slug}_last_capture_ok",
                state_topic=capture_topic,
                field="last_capture_ok",
            ),
            _sensor_entity(
                suffix="last_capture_path",
                name="Last capture path",
                default_entity_id=f"sensor.{slug}_last_capture_path",
                state_topic=capture_topic,
                field="last_capture_path",
                entity_category="diagnostic",
                icon="mdi:file-image",
            ),
            _binary_entity(
                suffix="person_detected",
                name="Person detected",
                default_entity_id=f"binary_sensor.{slug}_person_detected",
                state_topic=detection_topic,
                field="person_detected",
                device_class="occupancy",
            ),
            _sensor_entity(
                suffix="person_count",
                name="Person count",
                default_entity_id=f"sensor.{slug}_person_count",
                state_topic=detection_topic,
                field="person_count",
                state_class="measurement",
                icon="mdi:account-group",
            ),
            _sensor_entity(
                suffix="person_confidence",
                name="Person confidence",
                default_entity_id=f"sensor.{slug}_person_confidence",
                state_topic=detection_topic,
                value_template=_score_percent_template("best_score"),
                state_class="measurement",
                unit="%",
                icon="mdi:target",
            ),
            _timestamp_entity(
                suffix="last_inference",
                name="Last inference",
                default_entity_id=f"sensor.{slug}_last_inference",
                state_topic=detection_topic,
                field="last_inference",
            ),
            _sensor_entity(
                suffix="inference_ms",
                name="Inference latency",
                default_entity_id=f"sensor.{slug}_inference_ms",
                state_topic=detection_topic,
                field="inference_ms",
                state_class="measurement",
                unit="ms",
                entity_category="diagnostic",
                icon="mdi:speedometer",
            ),
            _sensor_entity(
                suffix="last_inference_image_path",
                name="Last inference image path",
                default_entity_id=f"sensor.{slug}_last_inference_image_path",
                state_topic=detection_topic,
                field="last_image_path",
                entity_category="diagnostic",
                icon="mdi:file-search",
            ),
            _image_entity(
                suffix="latest_capture",
                name="Latest capture",
                default_entity_id=f"image.{slug}_latest_capture",
                image_topic=self.node_image_topic(safe_node_id),
            ),
            _button_entity(
                suffix="ping",
                name="Ping",
                default_entity_id=f"button.{slug}_ping",
                command_topic=self.node_command_topic(safe_node_id, HomeAssistantCommandName.PING),
                icon="mdi:lan-connect",
            ),
            _button_entity(
                suffix="capture",
                name="Capture",
                default_entity_id=f"button.{slug}_capture",
                command_topic=self.node_command_topic(safe_node_id, HomeAssistantCommandName.CAPTURE),
                icon="mdi:camera",
            ),
            _button_entity(
                suffix="reboot",
                name="Reboot",
                default_entity_id=f"button.{slug}_reboot",
                command_topic=self.node_command_topic(safe_node_id, HomeAssistantCommandName.REBOOT),
                device_class="restart",
                entity_category="config",
            ),
            _switch_entity(
                suffix="motion_enabled",
                name="Motion enabled",
                default_entity_id=f"switch.{slug}_motion_enabled",
                state_topic=state_topic,
                command_topic=self.node_command_topic(safe_node_id, HomeAssistantCommandName.MOTION_ENABLED),
                field="motion_enabled",
            ),
            _select_entity(
                suffix="ir_mode",
                name="IR mode",
                default_entity_id=f"select.{slug}_ir_mode",
                state_topic=state_topic,
                command_topic=self.node_command_topic(safe_node_id, HomeAssistantCommandName.IR_MODE),
                field="ir_mode",
                options=("off", "on", "capture"),
            ),
            _number_entity(
                suffix="heartbeat_interval",
                name="Heartbeat interval",
                default_entity_id=f"number.{slug}_heartbeat_interval",
                state_topic=state_topic,
                command_topic=self.node_command_topic(safe_node_id, HomeAssistantCommandName.HEARTBEAT_INTERVAL),
                field="heartbeat_interval_s",
                minimum=1,
                maximum=3600,
                step=1,
                unit="s",
            ),
        )
        return tuple(self._discovery_publication(entity, slug, device) for entity in definitions)

    def all_discovery_messages(self, node_ids: Sequence[str]) -> tuple[MqttPublication, ...]:
        """Build discovery publications for the hub and a set of nodes.

        Args:
            node_ids: Known ESP32 node identifiers.

        Returns:
            Retained MQTT discovery publications.
        """

        messages = list(self.hub_discovery_messages())
        for node_id in node_ids:
            messages.extend(self.node_discovery_messages(node_id))
        return tuple(messages)

    def build_hub_availability(self, online: bool) -> MqttPublication:
        """Build the retained hub availability publication.

        Args:
            online: Whether Vision-Hub is currently online.

        Returns:
            Retained MQTT publication for the hub availability topic.
        """

        return MqttPublication(
            topic=self.hub_availability_topic,
            payload=b"online" if online else b"offline",
            retain=True,
        )

    def build_hub_state_update(self, state: Mapping[str, Any]) -> MqttPublication:
        """Build the retained Raspberry Pi system state publication.

        Args:
            state: JSON-serializable system state fields.

        Returns:
            Retained MQTT publication for Home Assistant hub entities.
        """

        return MqttPublication(topic=self.hub_state_topic, payload=_encode_json_object(state), retain=True)

    def build_node_state_update(self, node_id: str, state: Mapping[str, Any]) -> MqttPublication:
        """Build the retained state publication for one ESP32 node.

        Args:
            node_id: ESP32 node identifier.
            state: JSON-serializable node state fields.

        Returns:
            Retained MQTT publication for Home Assistant node entities.
        """

        return MqttPublication(topic=self.node_state_topic(node_id), payload=_encode_json_object(state), retain=True)

    def build_node_capture_update(self, node_id: str, capture: Mapping[str, Any]) -> MqttPublication:
        """Build the retained capture publication for one ESP32 node.

        Args:
            node_id: ESP32 node identifier.
            capture: JSON-serializable capture state fields.

        Returns:
            Retained MQTT publication for capture entities.
        """

        return MqttPublication(topic=self.node_capture_topic(node_id), payload=_encode_json_object(capture), retain=True)

    def build_node_detection_update(self, node_id: str, detection: Mapping[str, Any]) -> MqttPublication:
        """Build the retained detection publication for one ESP32 node.

        Args:
            node_id: ESP32 node identifier.
            detection: JSON-serializable inference fields.

        Returns:
            Retained MQTT publication for person detection entities.
        """

        return MqttPublication(topic=self.node_detection_topic(node_id), payload=_encode_json_object(detection), retain=True)

    def build_node_image_update(self, node_id: str, jpeg_bytes: bytes) -> MqttPublication:
        """Build the retained latest-image publication for one ESP32 node.

        Args:
            node_id: ESP32 node identifier.
            jpeg_bytes: Complete JPEG bytes to expose through MQTT Image.

        Returns:
            Retained binary MQTT publication for the latest node image.

        Raises:
            HomeAssistantError: If `jpeg_bytes` is not bytes-like.
        """

        if not isinstance(jpeg_bytes, bytes):
            raise HomeAssistantError("jpeg_bytes must be bytes")
        return MqttPublication(topic=self.node_image_topic(node_id), payload=jpeg_bytes, retain=True)

    def parse_command(self, topic: str, payload: bytes) -> HomeAssistantCommand | None:
        """Parse a Home Assistant command/control publication.

        Args:
            topic: MQTT topic published by Home Assistant.
            payload: Raw MQTT payload.

        Returns:
            Parsed command, or `None` when the topic is outside the HA command
            namespace.

        Raises:
            HomeAssistantError: If the command topic or payload is invalid.
        """

        prefix = f"{self.config.command_prefix}/"
        if not topic.startswith(prefix):
            return None

        parts = topic[len(prefix) :].split("/")
        if len(parts) == 2:
            return self._parse_button_command(parts[0], parts[1], payload)
        if len(parts) == 3 and parts[2] == "set":
            return self._parse_config_command(parts[0], parts[1], payload)
        raise HomeAssistantError("Home Assistant command topic must end with node_id/command or node_id/setting/set")

    def _parse_button_command(self, node_id: str, command: str, payload: bytes) -> HomeAssistantCommand:
        """Parse a button-style Home Assistant command.

        Args:
            node_id: Node id extracted from the topic.
            command: Command segment extracted from the topic.
            payload: Raw MQTT payload.

        Returns:
            Parsed Home Assistant command.
        """

        safe_node_id = _safe_segment(node_id, "node_id")
        command_name = _command_name(command)
        if command_name not in _BUTTON_COMMANDS:
            raise HomeAssistantError(f"{command_name.value} must use the setting/set command topic")
        if payload != b"PRESS":
            raise HomeAssistantError("Home Assistant button payload must be PRESS")
        return HomeAssistantCommand(node_id=safe_node_id, command=command_name, payload=payload)

    def _parse_config_command(self, node_id: str, command: str, payload: bytes) -> HomeAssistantCommand:
        """Parse a config-style Home Assistant control command.

        Args:
            node_id: Node id extracted from the topic.
            command: Setting segment extracted from the topic.
            payload: Raw MQTT payload.

        Returns:
            Parsed Home Assistant config command.
        """

        safe_node_id = _safe_segment(node_id, "node_id")
        command_name = _command_name(command)
        if command_name not in _CONFIG_COMMANDS:
            raise HomeAssistantError(f"{command_name.value} must use the button command topic")

        value = _parse_config_value(command_name, payload)
        return HomeAssistantCommand(node_id=safe_node_id, command=command_name, payload=payload, value=value)

    def _discovery_publication(self, entity: _EntityDefinition, device_slug: str, device: JsonObject) -> MqttPublication:
        """Build one retained MQTT Discovery publication.

        Args:
            entity: Entity definition to expose.
            device_slug: Slug used in the discovery object id.
            device: Home Assistant device registry payload.

        Returns:
            Retained discovery publication.
        """

        unique_id = f"vision_hub_{device_slug}_{entity.suffix}"
        payload = {
            **entity.payload,
            "name": entity.name,
            "unique_id": unique_id,
            "default_entity_id": entity.default_entity_id,
            "device": device,
        }
        if entity.use_hub_availability:
            payload["availability_topic"] = self.hub_availability_topic
            payload["payload_available"] = "online"
            payload["payload_not_available"] = "offline"
        topic = f"{self.config.discovery_prefix}/{entity.component}/{unique_id}/config"
        return MqttPublication(topic=topic, payload=_encode_json_object(payload), retain=True)

    def _hub_device(self) -> JsonObject:
        """Build the Home Assistant device registry payload for the RPi hub."""

        return {
            "identifiers": [self.config.hub_device_identifier],
            "name": self.config.hub_name,
            "manufacturer": self.config.manufacturer,
            "model": self.config.hub_model,
        }

    def _node_device(self, node_id: str, slug: str) -> JsonObject:
        """Build the Home Assistant device registry payload for one node.

        Args:
            node_id: ESP32 node identifier.
            slug: Stable slug derived from the node id.

        Returns:
            Device registry payload grouped under the Raspberry Pi hub.
        """

        return {
            "identifiers": [f"vision_hub_node_{slug}"],
            "name": node_id.upper(),
            "manufacturer": self.config.manufacturer,
            "model": self.config.node_model,
            "via_device": self.config.hub_device_identifier,
        }

    def _hub_slug(self) -> str:
        """Return the slug used by hub entity unique IDs."""

        return _safe_identifier(self.config.hub_device_identifier, "hub_device_identifier")


def _binary_entity(
    *,
    suffix: str,
    name: str,
    default_entity_id: str,
    state_topic: str,
    field: str | None = None,
    value_template: str | None = None,
    payload_on: str = "ON",
    payload_off: str = "OFF",
    device_class: str | None = None,
    use_hub_availability: bool = True,
) -> _EntityDefinition:
    """Build an MQTT binary sensor discovery definition."""

    payload: JsonObject = {
        "state_topic": state_topic,
        "payload_on": payload_on,
        "payload_off": payload_off,
    }
    if value_template is not None:
        payload["value_template"] = value_template
    elif field is not None:
        payload["value_template"] = _bool_template(field)
    if device_class is not None:
        payload["device_class"] = device_class
    return _EntityDefinition("binary_sensor", suffix, name, default_entity_id, payload, use_hub_availability)


def _sensor_entity(
    *,
    suffix: str,
    name: str,
    default_entity_id: str,
    state_topic: str,
    field: str | None = None,
    value_template: str | None = None,
    device_class: str | None = None,
    state_class: str | None = None,
    unit: str | None = None,
    entity_category: str | None = None,
    icon: str | None = None,
) -> _EntityDefinition:
    """Build an MQTT sensor discovery definition."""

    payload: JsonObject = {
        "state_topic": state_topic,
        "value_template": value_template if value_template is not None else _field_template(field),
    }
    if device_class is not None:
        payload["device_class"] = device_class
    if state_class is not None:
        payload["state_class"] = state_class
    if unit is not None:
        payload["unit_of_measurement"] = unit
    if entity_category is not None:
        payload["entity_category"] = entity_category
    if icon is not None:
        payload["icon"] = icon
    return _EntityDefinition("sensor", suffix, name, default_entity_id, payload)


def _timestamp_entity(
    *,
    suffix: str,
    name: str,
    default_entity_id: str,
    state_topic: str,
    field: str,
) -> _EntityDefinition:
    """Build a timestamp MQTT sensor discovery definition."""

    return _sensor_entity(
        suffix=suffix,
        name=name,
        default_entity_id=default_entity_id,
        state_topic=state_topic,
        field=field,
        device_class="timestamp",
        entity_category="diagnostic",
    )


def _image_entity(*, suffix: str, name: str, default_entity_id: str, image_topic: str) -> _EntityDefinition:
    """Build an MQTT image discovery definition."""

    payload = {
        "image_topic": image_topic,
        "content_type": "image/jpeg",
    }
    return _EntityDefinition("image", suffix, name, default_entity_id, payload)


def _button_entity(
    *,
    suffix: str,
    name: str,
    default_entity_id: str,
    command_topic: str,
    icon: str | None = None,
    device_class: str | None = None,
    entity_category: str | None = None,
) -> _EntityDefinition:
    """Build an MQTT button discovery definition."""

    payload: JsonObject = {
        "command_topic": command_topic,
        "payload_press": "PRESS",
    }
    if icon is not None:
        payload["icon"] = icon
    if device_class is not None:
        payload["device_class"] = device_class
    if entity_category is not None:
        payload["entity_category"] = entity_category
    return _EntityDefinition("button", suffix, name, default_entity_id, payload)


def _switch_entity(
    *,
    suffix: str,
    name: str,
    default_entity_id: str,
    state_topic: str,
    command_topic: str,
    field: str,
) -> _EntityDefinition:
    """Build an MQTT switch discovery definition."""

    payload = {
        "state_topic": state_topic,
        "command_topic": command_topic,
        "value_template": _bool_template(field),
        "payload_on": "ON",
        "payload_off": "OFF",
        "entity_category": "config",
    }
    return _EntityDefinition("switch", suffix, name, default_entity_id, payload)


def _select_entity(
    *,
    suffix: str,
    name: str,
    default_entity_id: str,
    state_topic: str,
    command_topic: str,
    field: str,
    options: Sequence[str],
) -> _EntityDefinition:
    """Build an MQTT select discovery definition."""

    payload = {
        "state_topic": state_topic,
        "command_topic": command_topic,
        "value_template": _field_template(field),
        "options": list(options),
        "entity_category": "config",
    }
    return _EntityDefinition("select", suffix, name, default_entity_id, payload)


def _number_entity(
    *,
    suffix: str,
    name: str,
    default_entity_id: str,
    state_topic: str,
    command_topic: str,
    field: str,
    minimum: int,
    maximum: int,
    step: int,
    unit: str,
) -> _EntityDefinition:
    """Build an MQTT number discovery definition."""

    payload = {
        "state_topic": state_topic,
        "command_topic": command_topic,
        "value_template": _field_template(field),
        "min": minimum,
        "max": maximum,
        "step": step,
        "mode": "box",
        "unit_of_measurement": unit,
        "entity_category": "config",
    }
    return _EntityDefinition("number", suffix, name, default_entity_id, payload)


def _field_template(field: str | None) -> str:
    """Return a Jinja template reading a JSON field."""

    if field is None:
        raise HomeAssistantError("field is required when value_template is omitted")
    return f"{{{{ value_json.{field} }}}}"


def _bool_template(field: str) -> str:
    """Return a Jinja template mapping a JSON boolean to ON/OFF."""

    return f"{{{{ 'ON' if value_json.{field} else 'OFF' }}}}"


def _score_percent_template(field: str) -> str:
    """Return a Jinja template mapping a 0..1 score to percent."""

    return f"{{{{ (value_json.{field} * 100) | round(1) if value_json.{field} is number else none }}}}"


def _parse_config_value(command: HomeAssistantCommandName, payload: bytes) -> bool | int | str:
    """Parse an MQTT payload for a config-style command.

    Args:
        command: Command whose payload must be parsed.
        payload: Raw MQTT payload.

    Returns:
        Parsed command value.

    Raises:
        HomeAssistantError: If the payload does not match the command contract.
    """

    try:
        value = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HomeAssistantError("Home Assistant config payload must be UTF-8") from exc

    if command == HomeAssistantCommandName.MOTION_ENABLED:
        if value == "ON":
            return True
        if value == "OFF":
            return False
        raise HomeAssistantError("motion_enabled payload must be ON or OFF")

    if command == HomeAssistantCommandName.IR_MODE:
        if value not in {"off", "on", "capture"}:
            raise HomeAssistantError("ir_mode payload must be off, on, or capture")
        return value

    if command == HomeAssistantCommandName.HEARTBEAT_INTERVAL:
        try:
            interval = int(value)
        except ValueError as exc:
            raise HomeAssistantError("heartbeat_interval payload must be an integer") from exc
        if interval < 1 or interval > 3600:
            raise HomeAssistantError("heartbeat_interval payload must be between 1 and 3600")
        return interval

    raise HomeAssistantError(f"unsupported config command {command.value}")


def _encode_json_object(payload: Mapping[str, Any]) -> bytes:
    """Encode a JSON object with deterministic formatting.

    Args:
        payload: JSON-serializable mapping.

    Returns:
        Compact UTF-8 JSON payload bytes.

    Raises:
        HomeAssistantError: If `payload` is not a mapping.
    """

    if not isinstance(payload, Mapping):
        raise HomeAssistantError("payload must be a JSON object")
    return json.dumps(dict(payload), separators=(",", ":"), sort_keys=True).encode("utf-8")


def _normalize_topic_prefix(value: str, name: str) -> str:
    """Normalize a multi-segment MQTT topic prefix.

    Args:
        value: Candidate topic prefix.
        name: Human-readable field name.

    Returns:
        Prefix without leading or trailing slashes.

    Raises:
        HomeAssistantError: If the prefix contains empty segments.
    """

    if not isinstance(value, str) or not value.strip("/"):
        raise HomeAssistantError(f"{name} must be a non-empty MQTT topic prefix")
    normalized = value.strip("/")
    if "//" in normalized:
        raise HomeAssistantError(f"{name} contains an empty MQTT topic segment")
    for segment in normalized.split("/"):
        _safe_segment(segment, name)
    return normalized


def _safe_segment(value: str, name: str) -> str:
    """Validate one MQTT topic segment and convert errors to HA errors.

    Args:
        value: Candidate MQTT topic segment.
        name: Human-readable field name.

    Returns:
        Validated segment value.

    Raises:
        HomeAssistantError: If the segment is unsafe.
    """

    try:
        return validate_topic_segment(value, name)
    except TopicError as exc:
        raise HomeAssistantError(str(exc)) from exc


def _safe_identifier(value: str, name: str) -> str:
    """Validate a Home Assistant identifier slug.

    Args:
        value: Candidate identifier.
        name: Human-readable field name.

    Returns:
        Validated identifier.

    Raises:
        HomeAssistantError: If the identifier is unsafe.
    """

    if not isinstance(value, str) or not value:
        raise HomeAssistantError(f"{name} must be a non-empty string")
    if not re.fullmatch(r"[A-Za-z0-9_]+", value):
        raise HomeAssistantError(f"{name} must contain only letters, numbers, and underscores")
    return value


def _command_name(command: HomeAssistantCommandName | str) -> HomeAssistantCommandName:
    """Resolve a clean Home Assistant command name.

    Args:
        command: Command enum or raw command string.

    Returns:
        Supported command enum.

    Raises:
        HomeAssistantError: If the command is unsupported.
    """

    try:
        return HomeAssistantCommandName(command)
    except ValueError as exc:
        allowed = ", ".join(item.value for item in HomeAssistantCommandName)
        raise HomeAssistantError(f"unsupported Home Assistant command {command!r}; expected one of: {allowed}") from exc
