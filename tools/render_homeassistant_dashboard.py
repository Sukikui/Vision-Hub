"""Render the Vision-Hub Home Assistant dashboard YAML."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vision_hub.homeassistant.ids import node_slug


@dataclass(frozen=True)
class DashboardNode:
    """Home Assistant dashboard metadata for one ESP32 node.

    Attributes:
        node_id: Firmware node identifier.
        slug: Home Assistant entity id slug derived from `node_id`.
        title: Human-readable card title.
    """

    node_id: str
    slug: str
    title: str


def main() -> None:
    """Parse CLI arguments and render the dashboard file."""

    parser = argparse.ArgumentParser(description="Render the Vision-Hub Home Assistant dashboard")
    parser.add_argument("--node-ids", default="", help="comma-separated ESP32 node ids")
    parser.add_argument("--output", required=True, help="dashboard YAML output path")
    args = parser.parse_args()

    nodes = parse_nodes(args.node_ids)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_dashboard(nodes), encoding="utf-8")


def parse_nodes(value: str) -> list[DashboardNode]:
    """Parse a comma-separated ESP32 node list.

    Args:
        value: Comma-separated node ids.

    Returns:
        Dashboard node metadata in the input order.

    Raises:
        ValueError: If the same node id is provided more than once.
    """

    nodes: list[DashboardNode] = []
    seen: set[str] = set()
    for raw_node_id in value.split(","):
        node_id = raw_node_id.strip()
        if not node_id:
            continue
        if node_id in seen:
            raise ValueError(f"duplicate node id: {node_id}")
        seen.add(node_id)
        slug = node_slug(node_id)
        nodes.append(DashboardNode(node_id=node_id, slug=slug, title=node_id.upper()))
    return nodes


def render_dashboard(nodes: list[DashboardNode]) -> str:
    """Render the complete Vision-Hub YAML dashboard.

    Args:
        nodes: ESP32 nodes that should receive dashboard cards.

    Returns:
        Home Assistant dashboard YAML text.
    """

    lines = [
        "views:",
        *indent(_overview_view(), 2),
        *indent(_cameras_view(nodes), 2),
        *indent(_node_diagnostics_view(nodes), 2),
        *indent(_system_view(), 2),
        *indent(_captures_view(), 2),
        "",
    ]
    return "\n".join(lines)


def indent(lines: list[str], spaces: int) -> list[str]:
    """Indent non-empty YAML lines.

    Args:
        lines: Lines to indent.
        spaces: Number of leading spaces to add.

    Returns:
        Indented lines.
    """

    prefix = " " * spaces
    return [f"{prefix}{line}" if line else line for line in lines]


def _overview_view() -> list[str]:
    """Render the high-level Vision-Hub overview view."""

    return [
        "- title: Overview",
        "  path: overview",
        "  icon: mdi:view-dashboard",
        "  cards:",
        "    - type: glance",
        "      title: Vision-Hub status",
        "      entities:",
        "        - binary_sensor.vision_hub_online",
        "        - binary_sensor.vision_hub_mqtt_connected",
        "        - binary_sensor.mosquitto_available",
        "        - binary_sensor.vision_hub_inference_ready",
        "        - binary_sensor.vision_hub_storage_pressure",
        "    - type: entities",
        "      title: Storage",
        "      entities:",
        "        - sensor.vision_hub_storage_free",
        "        - sensor.vision_hub_storage_used_percent",
        "        - sensor.vision_hub_capture_count",
        "        - sensor.vision_hub_last_capture",
        "    - type: entities",
        "      title: Raspberry Pi",
        "      entities:",
        "        - sensor.vision_hub_cpu_temperature",
        "        - sensor.vision_hub_memory_used",
        "        - sensor.vision_hub_load",
        "        - sensor.vision_hub_admin_ip",
        "        - sensor.vision_hub_field_ip",
    ]


def _cameras_view(nodes: list[DashboardNode]) -> list[str]:
    """Render the operational camera view."""

    lines = [
        "- title: Cameras",
        "  path: cameras",
        "  icon: mdi:cctv",
        "  cards:",
    ]
    if not nodes:
        lines.extend(_empty_nodes_card())
        return lines

    for node in nodes:
        lines.extend(indent(_node_camera_card(node), 4))
    return lines


def _node_diagnostics_view(nodes: list[DashboardNode]) -> list[str]:
    """Render the detailed per-node diagnostics view."""

    lines = [
        "- title: Node diagnostics",
        "  path: node-diagnostics",
        "  icon: mdi:clipboard-pulse",
        "  cards:",
    ]
    if not nodes:
        lines.extend(_empty_nodes_card())
        return lines

    for node in nodes:
        lines.extend(indent(_node_diagnostics_card(node), 4))
    return lines


def _system_view() -> list[str]:
    """Render the Raspberry Pi and service health view."""

    return [
        "- title: System",
        "  path: system",
        "  icon: mdi:raspberry-pi",
        "  cards:",
        "    - type: entities",
        "      title: Vision-Hub process",
        "      entities:",
        "        - binary_sensor.vision_hub_online",
        "        - sensor.vision_hub_version",
        "        - sensor.vision_hub_uptime",
        "    - type: entities",
        "      title: Services",
        "      entities:",
        "        - binary_sensor.vision_hub_mqtt_connected",
        "        - binary_sensor.mosquitto_available",
        "        - binary_sensor.vision_hub_inference_ready",
        "    - type: entities",
        "      title: Storage retention",
        "      entities:",
        "        - binary_sensor.vision_hub_storage_pressure",
        "        - sensor.vision_hub_last_age_cleanup",
        "        - sensor.vision_hub_retention_deleted_files",
        "        - sensor.vision_hub_retention_deleted_bytes",
        "    - type: entities",
        "      title: Raspberry Pi",
        "      entities:",
        "        - sensor.vision_hub_cpu_temperature",
        "        - sensor.vision_hub_memory_used",
        "        - sensor.vision_hub_load",
        "        - sensor.vision_hub_admin_ip",
        "        - sensor.vision_hub_field_ip",
    ]


def _captures_view() -> list[str]:
    """Render the capture archive view."""

    return [
        "- title: Captures",
        "  path: captures",
        "  icon: mdi:folder-image",
        "  cards:",
        "    - type: markdown",
        "      title: Capture archive",
        "      content: |",
        "        Open **Media > captures** to browse stored JPEG captures by node and date.",
        "        The archive is mounted read-only from `/media/vision-hub-captures`.",
    ]


def _node_camera_card(node: DashboardNode) -> list[str]:
    """Render one operational card stack for an ESP32 node."""

    slug = node.slug
    return [
        "- type: vertical-stack",
        f"  title: {node.title}",
        "  cards:",
        "    - type: picture-entity",
        f"      title: {node.title}",
        f"      entity: image.{slug}_latest_capture",
        "      show_state: false",
        "      show_name: true",
        "    - type: glance",
        "      title: Detection",
        "      entities:",
        f"        - binary_sensor.{slug}_online",
        f"        - binary_sensor.{slug}_motion",
        f"        - binary_sensor.{slug}_person_detected",
        f"        - sensor.{slug}_person_count",
        f"        - sensor.{slug}_person_confidence",
        "    - type: entities",
        "      title: Capture",
        "      entities:",
        f"        - sensor.{slug}_last_capture",
        f"        - binary_sensor.{slug}_last_capture_ok",
        f"        - sensor.{slug}_last_image_size",
        f"        - binary_sensor.{slug}_capture_error",
        "    - type: entities",
        "      title: Node",
        "      entities:",
        f"        - sensor.{slug}_ip",
        f"        - sensor.{slug}_uptime",
        f"        - sensor.{slug}_last_seen",
        f"        - sensor.{slug}_last_event",
        "    - type: entities",
        "      title: Actions",
        "      entities:",
        f"        - button.{slug}_ping",
        f"        - button.{slug}_capture",
        f"        - button.{slug}_reboot",
        f"        - switch.{slug}_motion_enabled",
        f"        - select.{slug}_ir_mode",
        f"        - number.{slug}_heartbeat_interval",
    ]


def _node_diagnostics_card(node: DashboardNode) -> list[str]:
    """Render one diagnostic card for an ESP32 node."""

    slug = node.slug
    return [
        "- type: entities",
        f"  title: {node.title}",
        "  entities:",
        f"    - sensor.{slug}_last_boot_event",
        f"    - sensor.{slug}_last_config_update",
        f"    - sensor.{slug}_last_capture_id",
        f"    - sensor.{slug}_last_capture_path",
        f"    - sensor.{slug}_last_inference",
        f"    - sensor.{slug}_inference_ms",
        f"    - sensor.{slug}_last_inference_image_path",
        f"    - binary_sensor.{slug}_capture_error",
    ]


def _empty_nodes_card() -> list[str]:
    """Render a placeholder card when no nodes are configured."""

    return [
        "    - type: markdown",
        "      title: No ESP32 nodes configured",
        "      content: Configure `VISION_HUB_NODE_IDS` to generate node cards.",
    ]


if __name__ == "__main__":
    main()
