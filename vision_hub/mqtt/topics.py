"""MQTT topic helpers for the ESP32 Vision Node protocol."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class TopicError(ValueError):
    """Raised when a topic or topic segment is invalid."""


class IncomingTopicKind(StrEnum):
    """Known incoming topic categories published by ESP32 nodes."""

    PRESENCE = "presence"
    HEARTBEAT = "heartbeat"
    EVENT = "event"
    REPLY = "reply"
    IMAGE_META = "image_meta"
    IMAGE_CHUNK = "image_chunk"
    IMAGE_DONE = "image_done"


class CommandName(StrEnum):
    """MQTT command names accepted by ESP32 nodes."""

    PING = "ping"
    CONFIG = "config"
    REBOOT = "reboot"
    CAPTURE = "capture"


@dataclass(frozen=True)
class IncomingTopic:
    """Parsed representation of an incoming MQTT topic.

    Attributes:
        kind: Type of incoming message carried by the topic.
        node_id: ESP32 node identifier from the topic path.
        request_id: Command request id for reply topics.
        capture_id: Capture identifier for image topics.
        chunk_index: Zero-based chunk index for image chunk topics.
    """

    kind: IncomingTopicKind
    node_id: str
    request_id: str | None = None
    capture_id: str | None = None
    chunk_index: int | None = None


NODE_STATUS_ONLINE = "vision/nodes/{node_id}/status/online"
NODE_STATUS_HEARTBEAT = "vision/nodes/{node_id}/status/heartbeat"
NODE_EVENT = "vision/nodes/{node_id}/event"
NODE_COMMAND = "vision/nodes/{node_id}/cmd/{command}"
NODE_REPLY = "vision/nodes/{node_id}/reply/{request_id}"
IMAGE_META = "vision/nodes/{node_id}/image/{capture_id}/meta"
IMAGE_CHUNK = "vision/nodes/{node_id}/image/{capture_id}/chunk/{index}"
IMAGE_DONE = "vision/nodes/{node_id}/image/{capture_id}/done"

DEFAULT_SUBSCRIPTIONS: tuple[tuple[str, int], ...] = (
    ("vision/nodes/+/status/online", 1),
    ("vision/nodes/+/status/heartbeat", 0),
    ("vision/nodes/+/event", 1),
    ("vision/nodes/+/reply/+", 1),
    ("vision/nodes/+/image/+/meta", 1),
    ("vision/nodes/+/image/+/chunk/+", 0),
    ("vision/nodes/+/image/+/done", 1),
)

_SEGMENT_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")

_NODE = r"(?P<node_id>[^/]+)"
_REQUEST = r"(?P<request_id>[^/]+)"
_CAPTURE = r"(?P<capture_id>[^/]+)"

_INCOMING_PATTERNS: tuple[tuple[re.Pattern[str], IncomingTopicKind], ...] = (
    (re.compile(rf"^vision/nodes/{_NODE}/status/online$"), IncomingTopicKind.PRESENCE),
    (re.compile(rf"^vision/nodes/{_NODE}/status/heartbeat$"), IncomingTopicKind.HEARTBEAT),
    (re.compile(rf"^vision/nodes/{_NODE}/event$"), IncomingTopicKind.EVENT),
    (re.compile(rf"^vision/nodes/{_NODE}/reply/{_REQUEST}$"), IncomingTopicKind.REPLY),
    (re.compile(rf"^vision/nodes/{_NODE}/image/{_CAPTURE}/meta$"), IncomingTopicKind.IMAGE_META),
    (re.compile(rf"^vision/nodes/{_NODE}/image/{_CAPTURE}/done$"), IncomingTopicKind.IMAGE_DONE),
    (re.compile(rf"^vision/nodes/{_NODE}/image/{_CAPTURE}/chunk/(?P<chunk_index>\d+)$"), IncomingTopicKind.IMAGE_CHUNK),
)


def parse_incoming_topic(topic: str) -> IncomingTopic | None:
    """Parse an ESP32 topic into a structured topic object.

    Args:
        topic: Raw MQTT topic string.

    Returns:
        Parsed topic metadata, or `None` when the topic is not part of the
        Vision-Hub ESP32 contract.
    """

    for pattern, kind in _INCOMING_PATTERNS:
        match = pattern.match(topic)
        if match is None:
            continue

        groups = match.groupdict()
        chunk_index = groups.get("chunk_index")
        return IncomingTopic(
            kind=kind,
            node_id=groups["node_id"],
            request_id=groups.get("request_id"),
            capture_id=groups.get("capture_id"),
            chunk_index=int(chunk_index) if chunk_index is not None else None,
        )

    return None


def build_node_command_topic(node_id: str, command: CommandName | str) -> str:
    """Build a node-specific command topic.

    Args:
        node_id: Target ESP32 node identifier.
        command: Command name to send.

    Returns:
        MQTT topic for the targeted command.

    Raises:
        TopicError: If `node_id` or `command` is invalid.
    """

    node = _safe_segment(node_id, "node_id")
    command_name = _command_value(command)
    return NODE_COMMAND.format(node_id=node, command=command_name)


def validate_topic_segment(value: str, name: str) -> str:
    """Validate that a value is safe for one MQTT topic segment.

    Args:
        value: Segment value to validate.
        name: Human-readable field name for error messages.

    Returns:
        The original `value` when valid.

    Raises:
        TopicError: If the value is empty or contains unsafe characters.
    """

    return _safe_segment(value, name)


def _command_value(command: CommandName | str) -> str:
    """Resolve a command enum or string into the MQTT command value.

    Args:
        command: Command enum or raw command string.

    Returns:
        Normalized command value.

    Raises:
        TopicError: If the command is not supported.
    """

    try:
        return CommandName(command).value
    except ValueError as exc:
        allowed = ", ".join(item.value for item in CommandName)
        raise TopicError(f"unsupported command {command!r}; expected one of: {allowed}") from exc


def _safe_segment(value: str, name: str) -> str:
    """Validate a string for use inside one MQTT topic path segment.

    Args:
        value: Candidate topic segment.
        name: Human-readable field name for error messages.

    Returns:
        The original segment value.

    Raises:
        TopicError: If the segment is empty or contains unsafe characters.
    """

    if not isinstance(value, str) or not value:
        raise TopicError(f"{name} must be a non-empty string")
    if not _SEGMENT_PATTERN.fullmatch(value):
        raise TopicError(f"{name} contains characters that are unsafe in MQTT topic segments")
    return value
