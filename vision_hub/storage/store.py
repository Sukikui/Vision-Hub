"""JPEG frame reconstruction and storage for ESP32 MQTT image transfers."""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TypeAlias

from vision_hub.mqtt.messages import (
    ImageChunkMessage,
    ImageDoneMessage,
    ImageMetaMessage,
    IncomingMqttMessage,
)


Clock: TypeAlias = Callable[[], datetime]

_DEFAULT_CONTENT_TYPES = frozenset({"image/jpeg"})
_SAFE_PATH_SEGMENT = re.compile(r"^[A-Za-z0-9_.-]+$")
_JPEG_START = b"\xff\xd8"
_JPEG_END = b"\xff\xd9"


class ImageStoreError(ValueError):
    """Raised when an image transfer cannot be stored safely."""


@dataclass(frozen=True)
class ImageStoreConfig:
    """Configuration for the image storage layer.

    Args:
        data_dir: Root directory used for temporary and final image files.
        max_image_size_bytes: Maximum accepted image size in bytes.
        max_buffered_bytes: Maximum memory used by active image transfers.
        session_timeout_s: Age after which an incomplete transfer can be
            cleaned up.
        allowed_content_types: MIME content types accepted from ESP32 nodes.
    """

    data_dir: Path
    max_image_size_bytes: int = 5_000_000
    max_buffered_bytes: int = 64_000_000
    session_timeout_s: int = 30
    allowed_content_types: Iterable[str] = field(default_factory=lambda: _DEFAULT_CONTENT_TYPES)

    def __post_init__(self) -> None:
        """Normalize and validate storage configuration values.

        Raises:
            ImageStoreError: If a configuration value is invalid.
        """

        data_dir = Path(self.data_dir)
        allowed_content_types = frozenset(item.lower() for item in self.allowed_content_types)

        if self.max_image_size_bytes <= 0:
            raise ImageStoreError("max_image_size_bytes must be greater than zero")
        if self.max_buffered_bytes <= 0:
            raise ImageStoreError("max_buffered_bytes must be greater than zero")
        if self.max_buffered_bytes < self.max_image_size_bytes:
            raise ImageStoreError("max_buffered_bytes must be greater than or equal to max_image_size_bytes")
        if self.session_timeout_s <= 0:
            raise ImageStoreError("session_timeout_s must be greater than zero")
        if not allowed_content_types:
            raise ImageStoreError("allowed_content_types must not be empty")

        object.__setattr__(self, "data_dir", data_dir)
        object.__setattr__(self, "allowed_content_types", allowed_content_types)


@dataclass(frozen=True)
class StoredFrame:
    """A fully reconstructed image stored on disk.

    Args:
        node_id: ESP32 node identifier.
        capture_id: Capture identifier from the MQTT image transfer.
        image_path: Final JPEG file path on disk.
        received_at: Local Raspberry Pi time when the image metadata arrived.
        completed_at: Local Raspberry Pi time when the image was finalized.
        total_size: Final image size in bytes.
    """

    node_id: str
    capture_id: str
    image_path: Path
    received_at: datetime
    completed_at: datetime
    total_size: int


@dataclass
class _ImageSession:
    """Mutable state for one in-progress image transfer."""

    meta: ImageMetaMessage
    received_at: datetime
    buffer: bytearray
    received_chunks: set[int] = field(default_factory=set)
    done_received: bool = False


class ImageAssembler:
    """Reconstruct JPEG images from MQTT meta/chunk/done messages.

    The assembler stores active transfers in memory to avoid chunk-by-chunk
    writes on Raspberry Pi microSD storage. When all expected chunks are
    present and the transfer is marked done, it validates the JPEG and writes
    the final file to the capture directory once.
    """

    def __init__(self, config: ImageStoreConfig, clock: Clock | None = None) -> None:
        """Create an image assembler.

        Args:
            config: Storage configuration.
            clock: Optional clock used for deterministic tests. The default is
                the Raspberry Pi local wall clock.
        """

        self._config = config
        self._clock = clock or _local_now
        self._sessions: dict[tuple[str, str], _ImageSession] = {}

    def handle(self, message: IncomingMqttMessage) -> StoredFrame | None:
        """Handle one parsed MQTT message.

        Args:
            message: Parsed MQTT message from `vision_hub.mqtt`.

        Returns:
            A `StoredFrame` when an image is finalized, otherwise `None`.

        Raises:
            ImageStoreError: If the image transfer violates the storage
                contract.
        """

        if isinstance(message, ImageMetaMessage):
            self._handle_meta(message)
            return None
        if isinstance(message, ImageChunkMessage):
            return self._handle_chunk(message)
        if isinstance(message, ImageDoneMessage):
            return self._handle_done(message)
        return None

    def cleanup_expired(self, now: datetime | None = None) -> int:
        """Remove incomplete image sessions older than the configured timeout.

        Args:
            now: Reference time. Defaults to the assembler clock.

        Returns:
            Number of sessions removed.
        """

        reference_time = now or self._clock()
        expired_keys = [
            key
            for key, session in self._sessions.items()
            if (reference_time - session.received_at).total_seconds() > self._config.session_timeout_s
        ]

        for key in expired_keys:
            self._sessions.pop(key)

        return len(expired_keys)

    def _handle_meta(self, message: ImageMetaMessage) -> None:
        """Start a new image transfer session from its metadata."""

        node_id = _safe_path_segment(message.node_id, "node_id")
        capture_id = _safe_path_segment(message.capture_id, "capture_id")
        content_type = message.content_type.lower()
        key = (node_id, capture_id)

        if key in self._sessions:
            raise ImageStoreError(f"image session already exists for node={node_id} capture={capture_id}")
        if content_type not in self._config.allowed_content_types:
            raise ImageStoreError(f"unsupported image content type: {message.content_type}")
        if message.total_size <= 0:
            raise ImageStoreError("total_size must be greater than zero")
        if message.total_size > self._config.max_image_size_bytes:
            raise ImageStoreError("image exceeds max_image_size_bytes")
        if message.chunk_count <= 0:
            raise ImageStoreError("chunk_count must be greater than zero")

        expected_chunk_count = math.ceil(message.total_size / message.chunk_size)
        if message.chunk_count != expected_chunk_count:
            raise ImageStoreError(
                f"chunk_count mismatch: expected {expected_chunk_count}, got {message.chunk_count}"
            )
        if self._buffered_bytes() + message.total_size > self._config.max_buffered_bytes:
            raise ImageStoreError("active image transfers exceed max_buffered_bytes")

        self._sessions[key] = _ImageSession(
            meta=message,
            received_at=self._clock(),
            buffer=bytearray(message.total_size),
        )

    def _handle_chunk(self, message: ImageChunkMessage) -> StoredFrame | None:
        """Write one binary image chunk to its in-memory session buffer."""

        node_id = _safe_path_segment(message.node_id, "node_id")
        capture_id = _safe_path_segment(message.capture_id, "capture_id")
        session = self._required_session(node_id=node_id, capture_id=capture_id)
        meta = session.meta

        if message.index < 0 or message.index >= meta.chunk_count:
            raise ImageStoreError(f"chunk index out of range: {message.index}")

        expected_size = _expected_chunk_size(
            total_size=meta.total_size,
            chunk_size=meta.chunk_size,
            chunk_count=meta.chunk_count,
            index=message.index,
        )
        if len(message.data) != expected_size:
            raise ImageStoreError(
                f"chunk {message.index} has invalid size: expected {expected_size}, got {len(message.data)}"
            )

        start = message.index * meta.chunk_size
        end = start + len(message.data)
        session.buffer[start:end] = message.data

        session.received_chunks.add(message.index)

        if session.done_received and not _missing_chunks(session):
            return self._finalize_session(node_id=node_id, capture_id=capture_id, session=session)
        return None

    def _handle_done(self, message: ImageDoneMessage) -> StoredFrame | None:
        """Complete an image transfer once the ESP32 sends `done`."""

        node_id = _safe_path_segment(message.node_id, "node_id")
        capture_id = _safe_path_segment(message.capture_id, "capture_id")
        session = self._required_session(node_id=node_id, capture_id=capture_id)

        if not message.ok:
            self._discard_session(node_id=node_id, capture_id=capture_id)
            raise ImageStoreError(f"image transfer failed for node={node_id} capture={capture_id}")
        if message.chunk_count != session.meta.chunk_count:
            raise ImageStoreError(
                f"done chunk_count mismatch: expected {session.meta.chunk_count}, got {message.chunk_count}"
            )

        session.done_received = True
        missing_chunks = _missing_chunks(session)
        if missing_chunks:
            raise ImageStoreError(f"missing chunks for capture={capture_id}: {missing_chunks}")

        return self._finalize_session(node_id=node_id, capture_id=capture_id, session=session)

    def _finalize_session(self, node_id: str, capture_id: str, session: _ImageSession) -> StoredFrame:
        """Validate an in-memory capture and write the final `.jpg` file."""

        meta = session.meta
        actual_size = len(session.buffer)
        if actual_size != meta.total_size:
            raise ImageStoreError(f"final image size mismatch: expected {meta.total_size}, got {actual_size}")
        if not _is_jpeg_bytes(session.buffer):
            raise ImageStoreError("final image is not a valid JPEG file")

        final_path = self._final_path(node_id=node_id, capture_id=capture_id, received_at=session.received_at)
        final_path.parent.mkdir(parents=True, exist_ok=True)
        if final_path.exists():
            raise ImageStoreError(f"final image already exists: {final_path}")

        final_path.write_bytes(session.buffer)
        self._sessions.pop((node_id, capture_id), None)

        return StoredFrame(
            node_id=node_id,
            capture_id=capture_id,
            image_path=final_path,
            received_at=session.received_at,
            completed_at=self._clock(),
            total_size=meta.total_size,
        )

    def _discard_session(self, node_id: str, capture_id: str) -> None:
        """Delete one incomplete in-memory session."""

        self._sessions.pop((node_id, capture_id), None)

    def _required_session(self, node_id: str, capture_id: str) -> _ImageSession:
        """Return an active image session or raise a storage error."""

        try:
            return self._sessions[(node_id, capture_id)]
        except KeyError as exc:
            raise ImageStoreError(f"no image session for node={node_id} capture={capture_id}") from exc

    def _final_path(self, node_id: str, capture_id: str, received_at: datetime) -> Path:
        """Build the final JPEG path for one completed image transfer."""

        timestamp = _format_human_timestamp(received_at)
        return (
            self._config.data_dir
            / "captures"
            / node_id
            / f"{received_at.year:04d}"
            / f"{received_at.month:02d}"
            / f"{received_at.day:02d}"
            / f"{timestamp}_{capture_id}.jpg"
        )

    def _buffered_bytes(self) -> int:
        """Return total bytes currently reserved by active transfer buffers."""

        return sum(session.meta.total_size for session in self._sessions.values())


def _local_now() -> datetime:
    """Return the local timezone-aware wall clock time."""

    return datetime.now().astimezone()


def _safe_path_segment(value: str, name: str) -> str:
    """Validate an MQTT identifier before using it as a filesystem segment."""

    if not value or not _SAFE_PATH_SEGMENT.fullmatch(value) or value in {".", ".."}:
        raise ImageStoreError(f"{name} is not safe for filesystem storage")
    return value


def _expected_chunk_size(total_size: int, chunk_size: int, chunk_count: int, index: int) -> int:
    """Return the expected payload size for one chunk index."""

    if index == chunk_count - 1:
        return total_size - (chunk_size * (chunk_count - 1))
    return chunk_size


def _missing_chunks(session: _ImageSession) -> list[int]:
    """Return sorted chunk indexes not yet received for a session."""

    expected = set(range(session.meta.chunk_count))
    return sorted(expected - session.received_chunks)


def _is_jpeg_bytes(data: bytes | bytearray) -> bool:
    """Check whether bytes have JPEG start and end markers."""

    return len(data) >= 4 and data.startswith(_JPEG_START) and data.endswith(_JPEG_END)


def _format_human_timestamp(value: datetime) -> str:
    """Format a datetime as a filename-safe human timestamp with milliseconds."""

    return value.strftime("%Y-%m-%d_%H-%M-%S") + f".{value.microsecond // 1000:03d}"
