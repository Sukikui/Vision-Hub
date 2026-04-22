"""MQTT boundary for ESP32 vision nodes."""

from __future__ import annotations

from typing import Any

__all__ = [
    "CommandName",
    "ImageChunkMessage",
    "ImageDoneMessage",
    "ImageMetaMessage",
    "IncomingMqttMessage",
    "IncomingTopic",
    "IncomingTopicKind",
    "MqttClient",
    "MqttConnectionConfig",
    "NodeEventMessage",
    "NodeHeartbeatMessage",
    "NodePresenceMessage",
    "NodeReplyMessage",
    "NodeRuntimeConfigPatch",
    "NodeStatus",
    "OutgoingCommand",
    "PayloadError",
    "TopicError",
    "build_capture_command",
    "build_config_command",
    "build_node_command_topic",
    "build_ping_command",
    "build_reboot_command",
    "parse_incoming_message",
    "parse_incoming_topic",
]

_CLIENT_EXPORTS = {
    "MqttClient",
    "MqttConnectionConfig",
}

_MESSAGE_EXPORTS = {
    "ImageChunkMessage",
    "ImageDoneMessage",
    "ImageMetaMessage",
    "IncomingMqttMessage",
    "NodeEventMessage",
    "NodeHeartbeatMessage",
    "NodePresenceMessage",
    "NodeReplyMessage",
    "NodeRuntimeConfigPatch",
    "NodeStatus",
    "OutgoingCommand",
    "PayloadError",
    "build_capture_command",
    "build_config_command",
    "build_ping_command",
    "build_reboot_command",
    "parse_incoming_message",
}

_TOPIC_EXPORTS = {
    "CommandName",
    "IncomingTopic",
    "IncomingTopicKind",
    "TopicError",
    "build_node_command_topic",
    "parse_incoming_topic",
}


def __getattr__(name: str) -> Any:
    """Lazily import MQTT helpers.

    Args:
        name: Attribute requested from the package.

    Returns:
        Requested MQTT helper object.

    Raises:
        AttributeError: If `name` is not exported by this package.
    """

    if name in _CLIENT_EXPORTS:
        from vision_hub.mqtt import client

        return getattr(client, name)
    if name in _MESSAGE_EXPORTS:
        from vision_hub.mqtt import messages

        return getattr(messages, name)
    if name in _TOPIC_EXPORTS:
        from vision_hub.mqtt import topics

        return getattr(topics, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
