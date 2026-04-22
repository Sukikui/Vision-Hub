"""MQTT payload models and builders for the ESP32 Vision Node contract."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, TypeAlias

from vision_hub.mqtt.topics import (
    CommandName,
    IncomingTopicKind,
    TopicError,
    build_node_command_topic,
    parse_incoming_topic,
    validate_topic_segment,
)


class PayloadError(ValueError):
    """Raised when an MQTT payload does not match the ESP32 contract."""


class NodeStatus(StrEnum):
    """Presence states published on node online/offline topics."""

    ONLINE = "online"
    OFFLINE = "offline"


JsonObject: TypeAlias = dict[str, Any]


@dataclass(frozen=True)
class NodePresenceMessage:
    """Node presence payload parsed from `status/online`.

    Attributes:
        node_id: ESP32 node identifier.
        state: Online or offline state.
    """

    node_id: str
    state: NodeStatus


@dataclass(frozen=True)
class NodeHeartbeatMessage:
    """Heartbeat payload parsed from `status/heartbeat`.

    Attributes:
        node_id: ESP32 node identifier.
        ip: Current IPv4 address reported by the node.
        uptime_s: Node uptime in seconds.
    """

    node_id: str
    ip: str
    uptime_s: int


@dataclass(frozen=True)
class NodeEventMessage:
    """Application event payload parsed from `event`.

    Attributes:
        node_id: ESP32 node identifier.
        event: Event name emitted by the node.
        timestamp_ms: Event timestamp in milliseconds from the node.
    """

    node_id: str
    event: str
    timestamp_ms: int


@dataclass(frozen=True)
class NodeReplyMessage:
    """Command reply payload parsed from `reply/{request_id}`.

    Attributes:
        node_id: ESP32 node identifier.
        request_id: Request id from the reply topic.
        payload: Full JSON reply body.
    """

    node_id: str
    request_id: str
    payload: JsonObject


@dataclass(frozen=True)
class ImageMetaMessage:
    """Image metadata payload parsed from `image/{capture_id}/meta`.

    Attributes:
        node_id: ESP32 node identifier.
        capture_id: Capture identifier.
        content_type: MIME content type of the image payload.
        total_size: Total image size in bytes.
        chunk_size: Nominal chunk size in bytes.
        chunk_count: Number of chunks expected for the image.
    """

    node_id: str
    capture_id: str
    content_type: str
    total_size: int
    chunk_size: int
    chunk_count: int


@dataclass(frozen=True)
class ImageChunkMessage:
    """Binary image chunk parsed from `image/{capture_id}/chunk/{index}`.

    Attributes:
        node_id: ESP32 node identifier.
        capture_id: Capture identifier.
        index: Chunk index from the topic.
        data: Raw binary chunk payload.
    """

    node_id: str
    capture_id: str
    index: int
    data: bytes


@dataclass(frozen=True)
class ImageDoneMessage:
    """Image completion payload parsed from `image/{capture_id}/done`.

    Attributes:
        node_id: ESP32 node identifier.
        capture_id: Capture identifier.
        chunk_count: Number of chunks sent by the node.
        ok: Whether the node considers the transfer complete and valid.
    """

    node_id: str
    capture_id: str
    chunk_count: int
    ok: bool


IncomingMqttMessage: TypeAlias = (
    NodePresenceMessage
    | NodeHeartbeatMessage
    | NodeEventMessage
    | NodeReplyMessage
    | ImageMetaMessage
    | ImageChunkMessage
    | ImageDoneMessage
)


@dataclass(frozen=True)
class OutgoingCommand:
    """MQTT command ready to be published to an ESP32 node.

    Attributes:
        topic: MQTT topic to publish on.
        payload: UTF-8 encoded JSON command payload.
        qos: MQTT QoS level.
        retain: Whether the broker should retain the message.
    """

    topic: str
    payload: bytes
    qos: int = 1
    retain: bool = False

    @property
    def payload_text(self) -> str:
        """Decode the command payload as UTF-8 text.

        Returns:
            JSON payload string.
        """

        return self.payload.decode("utf-8")


@dataclass(frozen=True)
class NodeRuntimeConfigPatch:
    """Partial runtime configuration update for an ESP32 node.

    Attributes:
        heartbeat_interval_s: Optional heartbeat interval in seconds.
        motion_detection_enabled: Optional PIR motion enable flag.
        motion_warmup_ms: Optional PIR warm-up duration in milliseconds.
        motion_cooldown_ms: Optional PIR cooldown duration in milliseconds.
        ir_illuminator_mode: Optional IR illuminator mode.
    """

    heartbeat_interval_s: int | None = None
    motion_detection_enabled: bool | None = None
    motion_warmup_ms: int | None = None
    motion_cooldown_ms: int | None = None
    ir_illuminator_mode: str | None = None

    def to_payload(self) -> JsonObject:
        """Convert the patch into a validated MQTT JSON object.

        Returns:
            JSON object containing only fields that are present in the patch.

        Raises:
            PayloadError: If a field value is outside the firmware contract.
        """

        payload: JsonObject = {}
        if self.heartbeat_interval_s is not None:
            payload["heartbeat_interval_s"] = _bounded_int(self.heartbeat_interval_s, "heartbeat_interval_s", minimum=1, maximum=3600)
        if self.motion_detection_enabled is not None:
            if not isinstance(self.motion_detection_enabled, bool):
                raise PayloadError("motion_detection_enabled must be a boolean")
            payload["motion_detection_enabled"] = self.motion_detection_enabled
        if self.motion_warmup_ms is not None:
            payload["motion_warmup_ms"] = _bounded_int(self.motion_warmup_ms, "motion_warmup_ms", minimum=0, maximum=120000)
        if self.motion_cooldown_ms is not None:
            payload["motion_cooldown_ms"] = _bounded_int(self.motion_cooldown_ms, "motion_cooldown_ms", minimum=0, maximum=60000)
        if self.ir_illuminator_mode is not None:
            if self.ir_illuminator_mode not in {"off", "on", "capture"}:
                raise PayloadError("ir_illuminator_mode must be off, on, or capture")
            payload["ir_illuminator_mode"] = self.ir_illuminator_mode
        return payload


def parse_incoming_message(topic: str, payload: bytes) -> IncomingMqttMessage:
    """Parse and validate one incoming MQTT message.

    Args:
        topic: MQTT topic on which the message was received.
        payload: Raw MQTT payload bytes.

    Returns:
        Typed message object matching the topic kind.

    Raises:
        PayloadError: If the topic is unsupported or the payload violates the
            ESP32 firmware contract.
    """

    parsed_topic = parse_incoming_topic(topic)
    if parsed_topic is None:
        raise PayloadError(f"unsupported MQTT topic: {topic}")

    if parsed_topic.kind == IncomingTopicKind.IMAGE_CHUNK:
        if parsed_topic.capture_id is None or parsed_topic.chunk_index is None:
            raise PayloadError("image chunk topic is missing capture_id or chunk index")
        return ImageChunkMessage(
            node_id=parsed_topic.node_id,
            capture_id=parsed_topic.capture_id,
            index=parsed_topic.chunk_index,
            data=payload,
        )

    if parsed_topic.kind == IncomingTopicKind.REPLY:
        if parsed_topic.request_id is None:
            raise PayloadError("reply topic is missing request_id")
        body = _json_object(payload)
        node_id = _required_str(body, "node_id")
        _require_topic_match(parsed_topic.node_id, node_id, "node_id")
        return NodeReplyMessage(
            node_id=node_id,
            request_id=parsed_topic.request_id,
            payload=body,
        )

    body = _json_object(payload)

    if parsed_topic.kind == IncomingTopicKind.PRESENCE:
        node_id = _required_str(body, "node_id")
        _require_topic_match(parsed_topic.node_id, node_id, "node_id")
        try:
            state = NodeStatus(_required_str(body, "state"))
        except ValueError as exc:
            raise PayloadError("state must be online or offline") from exc
        return NodePresenceMessage(node_id=node_id, state=state)

    if parsed_topic.kind == IncomingTopicKind.HEARTBEAT:
        node_id = _required_str(body, "node_id")
        _require_topic_match(parsed_topic.node_id, node_id, "node_id")
        return NodeHeartbeatMessage(
            node_id=node_id,
            ip=_required_str(body, "ip"),
            uptime_s=_bounded_int(_required_int(body, "uptime_s"), "uptime_s", minimum=0),
        )

    if parsed_topic.kind == IncomingTopicKind.EVENT:
        node_id = _required_str(body, "node_id")
        _require_topic_match(parsed_topic.node_id, node_id, "node_id")
        return NodeEventMessage(
            node_id=node_id,
            event=_required_str(body, "event"),
            timestamp_ms=_bounded_int(_required_int(body, "timestamp_ms"), "timestamp_ms", minimum=0),
        )

    if parsed_topic.kind == IncomingTopicKind.IMAGE_META:
        if parsed_topic.capture_id is None:
            raise PayloadError("image meta topic is missing capture_id")
        capture_id = _required_str(body, "capture_id")
        _require_topic_match(parsed_topic.capture_id, capture_id, "capture_id")
        return ImageMetaMessage(
            node_id=parsed_topic.node_id,
            capture_id=capture_id,
            content_type=_required_str(body, "content_type"),
            total_size=_bounded_int(_required_int(body, "total_size"), "total_size", minimum=0),
            chunk_size=_bounded_int(_required_int(body, "chunk_size"), "chunk_size", minimum=1),
            chunk_count=_bounded_int(_required_int(body, "chunk_count"), "chunk_count", minimum=0),
        )

    if parsed_topic.kind == IncomingTopicKind.IMAGE_DONE:
        if parsed_topic.capture_id is None:
            raise PayloadError("image done topic is missing capture_id")
        capture_id = _required_str(body, "capture_id")
        _require_topic_match(parsed_topic.capture_id, capture_id, "capture_id")
        return ImageDoneMessage(
            node_id=parsed_topic.node_id,
            capture_id=capture_id,
            chunk_count=_bounded_int(_required_int(body, "chunk_count"), "chunk_count", minimum=0),
            ok=_required_bool(body, "ok"),
        )

    raise PayloadError(f"unhandled topic kind: {parsed_topic.kind}")


def build_ping_command(node_id: str, request_id: str) -> OutgoingCommand:
    """Build a targeted ping command.

    Args:
        node_id: Target ESP32 node identifier.
        request_id: Request id used to correlate the reply.

    Returns:
        MQTT command object ready to publish.
    """

    return _build_node_request_command(node_id, CommandName.PING, request_id)


def build_capture_command(node_id: str, request_id: str) -> OutgoingCommand:
    """Build a targeted image capture command.

    Args:
        node_id: Target ESP32 node identifier.
        request_id: Request id used to correlate the reply.

    Returns:
        MQTT command object ready to publish.
    """

    return _build_node_request_command(node_id, CommandName.CAPTURE, request_id)


def build_reboot_command(node_id: str, request_id: str) -> OutgoingCommand:
    """Build a targeted reboot command.

    Args:
        node_id: Target ESP32 node identifier.
        request_id: Request id used to correlate the reply.

    Returns:
        MQTT command object ready to publish.
    """

    return _build_node_request_command(node_id, CommandName.REBOOT, request_id)


def build_config_command(node_id: str, request_id: str, patch: NodeRuntimeConfigPatch) -> OutgoingCommand:
    """Build a targeted runtime configuration command.

    Args:
        node_id: Target ESP32 node identifier.
        request_id: Request id used to correlate the reply.
        patch: Runtime configuration values to update.

    Returns:
        MQTT command object ready to publish.

    Raises:
        PayloadError: If the patch or request id is invalid.
    """

    payload = patch.to_payload()
    payload["request_id"] = _required_topic_id(request_id, "request_id")
    return OutgoingCommand(
        topic=build_node_command_topic(node_id, CommandName.CONFIG),
        payload=_encode_json(payload),
    )


def _build_node_request_command(node_id: str, command: CommandName, request_id: str) -> OutgoingCommand:
    """Build a targeted request-style command payload.

    Args:
        node_id: Target ESP32 node identifier.
        command: Command enum to publish.
        request_id: Request id used to correlate the reply.

    Returns:
        MQTT command object ready to publish.
    """

    return _build_request_command(build_node_command_topic(node_id, command), request_id)


def _build_request_command(topic: str, request_id: str) -> OutgoingCommand:
    """Build a command whose payload only contains `request_id`.

    Args:
        topic: MQTT topic to publish on.
        request_id: Request id used to correlate replies.

    Returns:
        MQTT command object ready to publish.

    Raises:
        PayloadError: If `request_id` is not safe for the firmware contract.
    """

    return OutgoingCommand(
        topic=topic,
        payload=_encode_json({"request_id": _required_topic_id(request_id, "request_id")}),
    )


def _json_object(payload: bytes) -> JsonObject:
    """Decode a UTF-8 JSON object payload.

    Args:
        payload: Raw MQTT payload bytes.

    Returns:
        Decoded JSON object.

    Raises:
        PayloadError: If the payload is not UTF-8, not JSON, or not an object.
    """

    try:
        decoded = json.loads(payload.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise PayloadError("payload is not valid UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise PayloadError(f"payload is not valid JSON: {exc.msg}") from exc

    if not isinstance(decoded, dict):
        raise PayloadError("payload must be a JSON object")
    return decoded


def _encode_json(payload: JsonObject) -> bytes:
    """Encode a JSON object using the compact firmware command format.

    Args:
        payload: JSON object to encode.

    Returns:
        UTF-8 JSON bytes with deterministic key order.
    """

    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _required_str(payload: JsonObject, name: str) -> str:
    """Read a required non-empty string from a JSON object.

    Args:
        payload: JSON object to read from.
        name: Field name to read.

    Returns:
        Field value.

    Raises:
        PayloadError: If the field is missing or not a non-empty string.
    """

    value = payload.get(name)
    if not isinstance(value, str) or not value:
        raise PayloadError(f"{name} must be a non-empty string")
    return value


def _required_topic_id(value: str, name: str) -> str:
    """Validate a payload id that is also used inside an MQTT topic.

    Args:
        value: Candidate identifier.
        name: Human-readable field name for error messages.

    Returns:
        Validated identifier.

    Raises:
        PayloadError: If the value is not a safe topic segment.
    """

    try:
        return validate_topic_segment(value, name)
    except TopicError as exc:
        raise PayloadError(f"{name} must be a safe topic segment") from exc


def _required_int(payload: JsonObject, name: str) -> int:
    """Read a required integer from a JSON object.

    Args:
        payload: JSON object to read from.
        name: Field name to read.

    Returns:
        Field value.

    Raises:
        PayloadError: If the field is missing, boolean, or not an integer.
    """

    value = payload.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise PayloadError(f"{name} must be an integer")
    return value


def _required_bool(payload: JsonObject, name: str) -> bool:
    """Read a required boolean from a JSON object.

    Args:
        payload: JSON object to read from.
        name: Field name to read.

    Returns:
        Field value.

    Raises:
        PayloadError: If the field is missing or not a boolean.
    """

    value = payload.get(name)
    if not isinstance(value, bool):
        raise PayloadError(f"{name} must be a boolean")
    return value


def _bounded_int(value: int, name: str, *, minimum: int, maximum: int | None = None) -> int:
    """Validate that an integer is within an inclusive range.

    Args:
        value: Candidate integer value.
        name: Human-readable field name for error messages.
        minimum: Inclusive lower bound.
        maximum: Optional inclusive upper bound.

    Returns:
        The original integer value.

    Raises:
        PayloadError: If the value is not an integer or is outside the range.
    """

    if isinstance(value, bool) or not isinstance(value, int):
        raise PayloadError(f"{name} must be an integer")
    if value < minimum:
        raise PayloadError(f"{name} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise PayloadError(f"{name} must be <= {maximum}")
    return value


def _require_topic_match(topic_value: str, payload_value: str, name: str) -> None:
    """Require an identifier in the payload to match the MQTT topic.

    Args:
        topic_value: Identifier extracted from the MQTT topic.
        payload_value: Identifier extracted from the JSON payload.
        name: Human-readable field name for error messages.

    Raises:
        PayloadError: If both values differ.
    """

    if topic_value != payload_value:
        raise PayloadError(f"{name} in payload does not match MQTT topic")
