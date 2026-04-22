"""Home Assistant identifier helpers."""

from __future__ import annotations

import re


def node_slug(node_id: str) -> str:
    """Convert an ESP32 node id into a Home Assistant-safe slug.

    Args:
        node_id: ESP32 node identifier.

    Returns:
        Lowercase slug suitable for Home Assistant entity ids and unique ids.

    Raises:
        ValueError: If the node id is empty or unsafe.
    """

    if not isinstance(node_id, str) or not node_id:
        raise ValueError("node_id must be a non-empty string")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", node_id):
        raise ValueError(f"node_id contains unsafe characters: {node_id}")
    slug = re.sub(r"[^a-z0-9_]+", "_", node_id.lower())
    slug = re.sub(r"_+", "_", slug).strip("_")
    if not slug:
        raise ValueError("node_id cannot produce an empty slug")
    return slug
