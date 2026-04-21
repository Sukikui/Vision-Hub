"""Tests for capture retention policies."""

from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from vision_hub.storage import StorageRetentionConfig, StorageRetentionJob
from vision_hub.storage.retention import GIGABYTE


NOW = datetime(2026, 4, 21, 14, 38, 12, tzinfo=timezone.utc)


@dataclass(frozen=True)
class _DiskUsage:
    """Test disk usage snapshot."""

    total: int
    used: int
    free: int


class StorageRetentionJobTest(unittest.TestCase):
    """Unit tests for capture cleanup by age and disk pressure."""

    def test_default_thresholds_match_micro_sd_policy(self) -> None:
        """Use 31 days, 5 GB minimum free, and 10 GB target free by default."""

        config = StorageRetentionConfig(captures_dir=Path("/tmp/captures"))

        self.assertEqual(config.max_age_days, 31)
        self.assertEqual(config.min_free_bytes, 5 * GIGABYTE)
        self.assertEqual(config.target_free_bytes, 10 * GIGABYTE)
        self.assertEqual(config.age_cleanup_interval_s, 86_400)

    def test_deletes_captures_older_than_max_age(self) -> None:
        """Remove JPEG captures older than the configured retention window."""

        with tempfile.TemporaryDirectory() as temp_dir:
            captures_dir = Path(temp_dir) / "captures"
            old_capture = _write_capture(captures_dir, "p4-001", "old.jpg", NOW - timedelta(days=32), size=4)
            recent_capture = _write_capture(
                captures_dir,
                "p4-001",
                "recent.jpg",
                NOW - timedelta(days=30),
                size=4,
            )

            job = _job(captures_dir, min_free_bytes=0, target_free_bytes=0)
            result = job.cleanup_by_age(now=NOW)

            self.assertEqual(result.deleted_files, 1)
            self.assertEqual(result.deleted_bytes, 4)
            self.assertGreater(result.deleted_dirs, 0)
            self.assertFalse(old_capture.exists())
            self.assertFalse(old_capture.parent.exists())
            self.assertTrue(recent_capture.exists())

    def test_deletes_oldest_captures_when_free_space_is_low(self) -> None:
        """Delete oldest remaining JPEGs until the target free space is reached."""

        with tempfile.TemporaryDirectory() as temp_dir:
            captures_dir = Path(temp_dir) / "captures"
            oldest = _write_capture(captures_dir, "p4-001", "oldest.jpg", NOW - timedelta(days=3), size=4)
            middle = _write_capture(captures_dir, "p4-001", "middle.jpg", NOW - timedelta(days=2), size=3)
            newest = _write_capture(captures_dir, "p4-001", "newest.jpg", NOW - timedelta(days=1), size=9)

            job = _job(captures_dir, min_free_bytes=5, target_free_bytes=10, free_bytes=4)
            result = job.ensure_free_space(now=NOW)

            self.assertTrue(result.pressure_cleanup_started)
            self.assertEqual(result.deleted_files, 2)
            self.assertEqual(result.deleted_bytes, 7)
            self.assertEqual(result.free_bytes_before, 4)
            self.assertEqual(result.free_bytes_after, 11)
            self.assertFalse(oldest.exists())
            self.assertFalse(middle.exists())
            self.assertTrue(newest.exists())

    def test_does_not_run_pressure_cleanup_above_min_free_space(self) -> None:
        """Keep recent captures when free space is already above the action threshold."""

        with tempfile.TemporaryDirectory() as temp_dir:
            captures_dir = Path(temp_dir) / "captures"
            capture = _write_capture(captures_dir, "p4-001", "recent.jpg", NOW - timedelta(days=1), size=4)

            job = _job(captures_dir, min_free_bytes=5, target_free_bytes=10, free_bytes=5)
            result = job.ensure_free_space(now=NOW)

            self.assertFalse(result.pressure_cleanup_started)
            self.assertEqual(result.deleted_files, 0)
            self.assertTrue(capture.exists())

    def test_dry_run_counts_deletions_without_removing_files(self) -> None:
        """Support retention previews without mutating the filesystem."""

        with tempfile.TemporaryDirectory() as temp_dir:
            captures_dir = Path(temp_dir) / "captures"
            old_capture = _write_capture(captures_dir, "p4-001", "old.jpg", NOW - timedelta(days=32), size=4)

            job = _job(captures_dir, min_free_bytes=0, target_free_bytes=0, dry_run=True)
            result = job.cleanup_by_age(now=NOW)

            self.assertTrue(result.dry_run)
            self.assertEqual(result.deleted_files, 1)
            self.assertEqual(result.deleted_bytes, 4)
            self.assertTrue(old_capture.exists())

    def test_ignores_non_jpeg_files(self) -> None:
        """Only `.jpg` capture files are managed by the retention job."""

        with tempfile.TemporaryDirectory() as temp_dir:
            captures_dir = Path(temp_dir) / "captures"
            text_file = _write_file(captures_dir / "p4-001" / "2026" / "03" / "20" / "note.txt", b"keep")
            _set_mtime(text_file, NOW - timedelta(days=90))

            job = _job(captures_dir, min_free_bytes=0, target_free_bytes=0)
            result = job.cleanup_by_age(now=NOW)

            self.assertEqual(result.deleted_files, 0)
            self.assertTrue(text_file.exists())

    def test_rejects_target_free_below_minimum_free(self) -> None:
        """Reject a disk-pressure policy that cannot reach its target."""

        with self.assertRaisesRegex(ValueError, "target_free_bytes"):
            StorageRetentionConfig(captures_dir=Path("/tmp/captures"), min_free_bytes=10, target_free_bytes=5)


def _job(
    captures_dir: Path,
    *,
    min_free_bytes: int,
    target_free_bytes: int,
    free_bytes: int = 100,
    dry_run: bool = False,
) -> StorageRetentionJob:
    """Create a retention job with deterministic disk usage."""

    config = StorageRetentionConfig(
        captures_dir=captures_dir,
        max_age_days=31,
        min_free_bytes=min_free_bytes,
        target_free_bytes=target_free_bytes,
        dry_run=dry_run,
    )
    return StorageRetentionJob(
        config,
        clock=lambda: NOW,
        disk_usage=lambda _path: _DiskUsage(total=1_000, used=1_000 - free_bytes, free=free_bytes),
    )


def _write_capture(captures_dir: Path, node_id: str, filename: str, mtime: datetime, *, size: int) -> Path:
    """Create one test capture with a deterministic modification time."""

    path = captures_dir / node_id / f"{mtime.year:04d}" / f"{mtime.month:02d}" / f"{mtime.day:02d}" / filename
    _write_file(path, b"x" * size)
    _set_mtime(path, mtime)
    return path


def _write_file(path: Path, data: bytes) -> Path:
    """Write a test file and create parent directories."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _set_mtime(path: Path, value: datetime) -> None:
    """Set a file modification time from a timezone-aware datetime."""

    timestamp = value.timestamp()
    os.utime(path, (timestamp, timestamp))


if __name__ == "__main__":
    unittest.main()
