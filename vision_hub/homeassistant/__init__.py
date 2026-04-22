"""Home Assistant integration helpers for Vision-Hub."""

from __future__ import annotations

from typing import Any

from vision_hub.homeassistant.ids import node_slug

__all__ = [
    "HomeAssistantCommand",
    "HomeAssistantCommandName",
    "HomeAssistantError",
    "HomeAssistantMqttConfig",
    "HomeAssistantMqttDiscovery",
    "MqttPublication",
    "node_slug",
]


def __getattr__(name: str) -> Any:
    """Lazily import MQTT Discovery helpers.

    Args:
        name: Attribute requested from the package.

    Returns:
        Requested Home Assistant helper object.

    Raises:
        AttributeError: If `name` is not exported by this package.
    """

    if name in {
        "HomeAssistantCommand",
        "HomeAssistantCommandName",
        "HomeAssistantError",
        "HomeAssistantMqttConfig",
        "HomeAssistantMqttDiscovery",
        "MqttPublication",
    }:
        from vision_hub.homeassistant import discovery

        return getattr(discovery, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
