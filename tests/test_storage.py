"""Tests for ESP32 image chunk reconstruction and storage."""

from __future__ import annotations

import tempfile
import unittest
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

from vision_hub.storage import ImageAssembler, ImageStoreConfig, ImageStoreError
from vision_hub.mqtt.messages import ImageChunkMessage, ImageDoneMessage, ImageMetaMessage, NodeHeartbeatMessage


JPEG_BYTES = b"\xff\xd8jpeg\xff\xd9"
RECEIVED_AT = datetime(2026, 4, 21, 14, 38, 12, 423456, tzinfo=timezone(timedelta(hours=2)))
COMPLETED_AT = datetime(2026, 4, 21, 14, 38, 13, 19_000, tzinfo=timezone(timedelta(hours=2)))


class ImageAssemblerTest(unittest.TestCase):
    """Unit tests for the filesystem-backed image assembler."""

    def test_stores_complete_jpeg_in_node_date_directory(self) -> None:
        """Assemble a complete transfer and store it as a human-named JPEG."""

        with tempfile.TemporaryDirectory() as temp_dir:
            assembler = _assembler(Path(temp_dir))

            self.assertIsNone(assembler.handle(_meta()))
            self.assertIsNone(assembler.handle(_chunk(0, JPEG_BYTES[:4])))
            self.assertIsNone(assembler.handle(_chunk(1, JPEG_BYTES[4:])))
            stored = assembler.handle(_done())

            expected_path = (
                Path(temp_dir)
                / "captures"
                / "p4-001"
                / "2026"
                / "04"
                / "21"
                / "2026-04-21_14-38-12.423_cap-abc123.jpg"
            )

            self.assertIsNotNone(stored)
            assert stored is not None
            self.assertEqual(stored.node_id, "p4-001")
            self.assertEqual(stored.capture_id, "cap-abc123")
            self.assertEqual(stored.image_path, expected_path)
            self.assertEqual(stored.received_at, RECEIVED_AT)
            self.assertEqual(stored.completed_at, COMPLETED_AT)
            self.assertEqual(stored.total_size, len(JPEG_BYTES))
            self.assertEqual(expected_path.read_bytes(), JPEG_BYTES)
            self.assertFalse((Path(temp_dir) / "tmp" / "p4-001" / "cap-abc123.part").exists())

    def test_stores_chunks_received_out_of_order(self) -> None:
        """Write chunks by index so MQTT delivery order does not matter."""

        with tempfile.TemporaryDirectory() as temp_dir:
            assembler = _assembler(Path(temp_dir))

            assembler.handle(_meta())
            assembler.handle(_chunk(1, JPEG_BYTES[4:]))
            assembler.handle(_chunk(0, JPEG_BYTES[:4]))
            stored = assembler.handle(_done())

            self.assertIsNotNone(stored)
            assert stored is not None
            self.assertEqual(stored.image_path.read_bytes(), JPEG_BYTES)

    def test_late_chunk_can_complete_after_done_was_seen(self) -> None:
        """Keep an incomplete session alive if `done` arrives before a chunk."""

        with tempfile.TemporaryDirectory() as temp_dir:
            assembler = _assembler(Path(temp_dir))

            assembler.handle(_meta())
            assembler.handle(_chunk(0, JPEG_BYTES[:4]))
            with self.assertRaisesRegex(ImageStoreError, "missing chunks"):
                assembler.handle(_done())

            stored = assembler.handle(_chunk(1, JPEG_BYTES[4:]))

            self.assertIsNotNone(stored)
            assert stored is not None
            self.assertEqual(stored.image_path.read_bytes(), JPEG_BYTES)

    def test_rejects_missing_session_for_chunk(self) -> None:
        """Reject chunks that arrive without prior image metadata."""

        with tempfile.TemporaryDirectory() as temp_dir:
            assembler = _assembler(Path(temp_dir))

            with self.assertRaisesRegex(ImageStoreError, "no image session"):
                assembler.handle(_chunk(0, JPEG_BYTES[:4]))

    def test_rejects_chunk_index_out_of_range(self) -> None:
        """Reject chunks outside the expected transfer range."""

        with tempfile.TemporaryDirectory() as temp_dir:
            assembler = _assembler(Path(temp_dir))
            assembler.handle(_meta())

            with self.assertRaisesRegex(ImageStoreError, "chunk index out of range"):
                assembler.handle(_chunk(2, b""))

    def test_rejects_invalid_chunk_size(self) -> None:
        """Reject chunks whose payload length does not match the metadata."""

        with tempfile.TemporaryDirectory() as temp_dir:
            assembler = _assembler(Path(temp_dir))
            assembler.handle(_meta())

            with self.assertRaisesRegex(ImageStoreError, "invalid size"):
                assembler.handle(_chunk(0, b"\xff\xd8"))

    def test_rejects_final_size_mismatch(self) -> None:
        """Validate final file size before publishing the completed frame."""

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            assembler = _assembler(root)
            assembler.handle(_meta())
            assembler.handle(_chunk(0, JPEG_BYTES[:4]))
            assembler.handle(_chunk(1, JPEG_BYTES[4:]))
            (root / "tmp" / "p4-001" / "cap-abc123.part").write_bytes(JPEG_BYTES + b"x")

            with self.assertRaisesRegex(ImageStoreError, "final image size mismatch"):
                assembler.handle(_done())

    def test_rejects_invalid_jpeg_signature(self) -> None:
        """Reject complete transfers that are not JPEG files."""

        image = b"notjpeg!"
        with tempfile.TemporaryDirectory() as temp_dir:
            assembler = _assembler(Path(temp_dir))
            assembler.handle(_meta(total_size=len(image), chunk_size=4, chunk_count=2))
            assembler.handle(_chunk(0, image[:4]))
            assembler.handle(_chunk(1, image[4:]))

            with self.assertRaisesRegex(ImageStoreError, "not a valid JPEG"):
                assembler.handle(_done(chunk_count=2))

    def test_rejects_unsupported_content_type(self) -> None:
        """Only JPEG transfers are accepted by the first storage interface."""

        with tempfile.TemporaryDirectory() as temp_dir:
            assembler = _assembler(Path(temp_dir))

            with self.assertRaisesRegex(ImageStoreError, "unsupported image content type"):
                assembler.handle(_meta(content_type="image/png"))

    def test_rejects_images_larger_than_configured_limit(self) -> None:
        """Prevent unexpectedly large captures from consuming the SSD."""

        with tempfile.TemporaryDirectory() as temp_dir:
            config = ImageStoreConfig(data_dir=Path(temp_dir), max_image_size_bytes=4)
            assembler = ImageAssembler(config=config, clock=_clock())

            with self.assertRaisesRegex(ImageStoreError, "exceeds max_image_size_bytes"):
                assembler.handle(_meta())

    def test_rejects_unsafe_path_segments(self) -> None:
        """Prevent node and capture ids from escaping the storage root."""

        with tempfile.TemporaryDirectory() as temp_dir:
            assembler = _assembler(Path(temp_dir))

            with self.assertRaisesRegex(ImageStoreError, "node_id is not safe"):
                assembler.handle(_meta(node_id=".."))

    def test_ignores_non_image_messages(self) -> None:
        """Return None for MQTT messages that are unrelated to image storage."""

        with tempfile.TemporaryDirectory() as temp_dir:
            assembler = _assembler(Path(temp_dir))

            result = assembler.handle(NodeHeartbeatMessage(node_id="p4-001", ip="192.168.50.20", uptime_s=10))

            self.assertIsNone(result)

    def test_cleanup_expired_removes_stale_part_files(self) -> None:
        """Delete incomplete sessions after their configured timeout."""

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            assembler = _assembler(root)
            assembler.handle(_meta())

            removed = assembler.cleanup_expired(now=RECEIVED_AT + timedelta(seconds=31))

            self.assertEqual(removed, 1)
            self.assertFalse((root / "tmp" / "p4-001" / "cap-abc123.part").exists())


def _assembler(data_dir: Path) -> ImageAssembler:
    """Create an assembler with deterministic test timestamps."""

    return ImageAssembler(config=ImageStoreConfig(data_dir=data_dir), clock=_clock())


def _clock() -> Callable[[], datetime]:
    """Return a deterministic clock that advances once for completion."""

    values = [RECEIVED_AT, COMPLETED_AT]

    def tick() -> datetime:
        if len(values) == 1:
            return values[0]
        return values.pop(0)

    return tick


def _meta(
    *,
    node_id: str = "p4-001",
    capture_id: str = "cap-abc123",
    content_type: str = "image/jpeg",
    total_size: int = len(JPEG_BYTES),
    chunk_size: int = 4,
    chunk_count: int = 2,
) -> ImageMetaMessage:
    """Build image metadata for tests."""

    return ImageMetaMessage(
        node_id=node_id,
        capture_id=capture_id,
        content_type=content_type,
        total_size=total_size,
        chunk_size=chunk_size,
        chunk_count=chunk_count,
    )


def _chunk(index: int, data: bytes) -> ImageChunkMessage:
    """Build one image chunk for tests."""

    return ImageChunkMessage(node_id="p4-001", capture_id="cap-abc123", index=index, data=data)


def _done(*, chunk_count: int = 2) -> ImageDoneMessage:
    """Build an image transfer completion message for tests."""

    return ImageDoneMessage(node_id="p4-001", capture_id="cap-abc123", chunk_count=chunk_count, ok=True)


if __name__ == "__main__":
    unittest.main()
