"""Retention policy for stored ESP32 JPEG captures."""

from __future__ import annotations

import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from threading import Event
from typing import Protocol


GIGABYTE = 1_000_000_000


class DiskUsageLike(Protocol):
    """Filesystem usage object compatible with `shutil.disk_usage`."""

    total: int
    used: int
    free: int


DiskUsageProvider = Callable[[Path], DiskUsageLike]
Clock = Callable[[], datetime]


@dataclass(frozen=True)
class StorageRetentionConfig:
    """Configuration for capture retention.

    Args:
        captures_dir: Root directory containing stored JPEG captures.
        max_age_days: Maximum capture age before unconditional deletion.
        min_free_bytes: Free-space threshold below which disk-pressure cleanup
            starts.
        target_free_bytes: Free-space target reached by deleting oldest
            captures when disk-pressure cleanup starts.
        age_cleanup_interval_s: Delay between age cleanup scans in loop mode.
        dry_run: When true, compute deletions without modifying the filesystem.
    """

    captures_dir: Path
    max_age_days: int = 31
    min_free_bytes: int = 5 * GIGABYTE
    target_free_bytes: int = 10 * GIGABYTE
    age_cleanup_interval_s: int = 86_400
    dry_run: bool = False

    def __post_init__(self) -> None:
        """Normalize paths and validate retention thresholds.

        Raises:
            ValueError: If a retention threshold is invalid.
        """

        if self.max_age_days <= 0:
            raise ValueError("max_age_days must be greater than zero")
        if self.min_free_bytes < 0:
            raise ValueError("min_free_bytes must be greater than or equal to zero")
        if self.target_free_bytes < self.min_free_bytes:
            raise ValueError("target_free_bytes must be greater than or equal to min_free_bytes")
        if self.age_cleanup_interval_s <= 0:
            raise ValueError("age_cleanup_interval_s must be greater than zero")

        object.__setattr__(self, "captures_dir", Path(self.captures_dir))


@dataclass(frozen=True)
class StorageRetentionResult:
    """Summary returned after one retention pass.

    Args:
        cutoff: Local time before which captures are older than the configured
            age limit.
        deleted_files: Number of JPEG files deleted or selected in dry-run
            mode.
        deleted_bytes: Total JPEG bytes deleted or selected in dry-run mode.
        deleted_dirs: Number of empty directories removed.
        free_bytes_before: Free bytes reported before retention.
        free_bytes_after: Estimated free bytes after retention.
        pressure_cleanup_started: Whether low free space triggered oldest-file
            cleanup.
        dry_run: Whether the pass modified the filesystem.
    """

    cutoff: datetime
    deleted_files: int
    deleted_bytes: int
    deleted_dirs: int
    free_bytes_before: int
    free_bytes_after: int
    pressure_cleanup_started: bool
    dry_run: bool


@dataclass(frozen=True)
class _StoredCapture:
    """Filesystem metadata for one stored JPEG capture."""

    path: Path
    size: int
    mtime: float


class StorageRetentionJob:
    """Delete old captures and protect the capture filesystem from filling up."""

    def __init__(
        self,
        config: StorageRetentionConfig,
        *,
        clock: Clock | None = None,
        disk_usage: DiskUsageProvider | None = None,
    ) -> None:
        """Create a retention job.

        Args:
            config: Retention policy configuration.
            clock: Optional local clock override for deterministic tests.
            disk_usage: Optional disk usage provider for deterministic tests.
        """

        self._config = config
        self._clock = clock or _local_now
        self._disk_usage = disk_usage or shutil.disk_usage

    def run_once(self, now: datetime | None = None) -> StorageRetentionResult:
        """Execute one retention pass.

        The pass first removes captures older than `max_age_days`, then ensures
        the capture filesystem keeps enough free space.

        Args:
            now: Optional reference time. Defaults to the configured clock.

        Returns:
            Retention summary.
        """

        age_result = self.cleanup_by_age(now=now)
        free_space_result = self.ensure_free_space(now=now)

        return StorageRetentionResult(
            cutoff=age_result.cutoff,
            deleted_files=age_result.deleted_files + free_space_result.deleted_files,
            deleted_bytes=age_result.deleted_bytes + free_space_result.deleted_bytes,
            deleted_dirs=age_result.deleted_dirs + free_space_result.deleted_dirs,
            free_bytes_before=age_result.free_bytes_before,
            free_bytes_after=free_space_result.free_bytes_after,
            pressure_cleanup_started=free_space_result.pressure_cleanup_started,
            dry_run=self._config.dry_run,
        )

    def cleanup_by_age(self, now: datetime | None = None) -> StorageRetentionResult:
        """Delete captures older than the configured age limit.

        This operation scans capture files and is intended for periodic
        execution, for example once per day.

        Args:
            now: Optional reference time. Defaults to the configured clock.

        Returns:
            Retention summary for age-based cleanup only.
        """

        reference_time = now or self._clock()
        cutoff = reference_time - timedelta(days=self._config.max_age_days)
        cutoff_ts = cutoff.timestamp()
        captures_dir = self._config.captures_dir
        captures_dir.mkdir(parents=True, exist_ok=True)

        usage_before = self._disk_usage(captures_dir)
        free_bytes_after = usage_before.free
        deleted_files = 0
        deleted_bytes = 0

        for capture in self._iter_captures():
            if capture.mtime < cutoff_ts:
                deleted_files += 1
                deleted_bytes += capture.size
                free_bytes_after += capture.size
                self._unlink(capture.path)

        deleted_dirs = self._cleanup_empty_dirs()

        return StorageRetentionResult(
            cutoff=cutoff,
            deleted_files=deleted_files,
            deleted_bytes=deleted_bytes,
            deleted_dirs=deleted_dirs,
            free_bytes_before=usage_before.free,
            free_bytes_after=free_bytes_after,
            pressure_cleanup_started=False,
            dry_run=self._config.dry_run,
        )

    def ensure_free_space(self, now: datetime | None = None) -> StorageRetentionResult:
        """Delete oldest captures when free space drops below the threshold.

        This method starts with a cheap filesystem usage check. It only scans
        capture files if free space is below `min_free_bytes`, making it suitable
        to call after each stored image.

        Args:
            now: Optional reference time. Defaults to the configured clock.

        Returns:
            Retention summary for disk-pressure cleanup only.
        """

        reference_time = now or self._clock()
        cutoff = reference_time - timedelta(days=self._config.max_age_days)
        captures_dir = self._config.captures_dir
        captures_dir.mkdir(parents=True, exist_ok=True)

        usage_before = self._disk_usage(captures_dir)
        free_bytes_after = usage_before.free
        pressure_cleanup_started = free_bytes_after < self._config.min_free_bytes
        deleted_files = 0
        deleted_bytes = 0

        if not pressure_cleanup_started:
            return StorageRetentionResult(
                cutoff=cutoff,
                deleted_files=0,
                deleted_bytes=0,
                deleted_dirs=0,
                free_bytes_before=usage_before.free,
                free_bytes_after=free_bytes_after,
                pressure_cleanup_started=False,
                dry_run=self._config.dry_run,
            )

        for capture in self._iter_captures_oldest_first():
            if free_bytes_after >= self._config.target_free_bytes:
                break

            deleted_files += 1
            deleted_bytes += capture.size
            free_bytes_after += capture.size
            self._unlink(capture.path)

        deleted_dirs = self._cleanup_empty_dirs()

        return StorageRetentionResult(
            cutoff=cutoff,
            deleted_files=deleted_files,
            deleted_bytes=deleted_bytes,
            deleted_dirs=deleted_dirs,
            free_bytes_before=usage_before.free,
            free_bytes_after=free_bytes_after,
            pressure_cleanup_started=pressure_cleanup_started,
            dry_run=self._config.dry_run,
        )

    def run_forever(self, stop_event: Event | None = None) -> None:
        """Run age cleanup forever with `age_cleanup_interval_s` between passes.

        Args:
            stop_event: Optional event used to stop the loop cleanly.
        """

        while stop_event is None or not stop_event.is_set():
            self.cleanup_by_age()
            if stop_event is None:
                time.sleep(self._config.age_cleanup_interval_s)
            elif stop_event.wait(self._config.age_cleanup_interval_s):
                break

    def _iter_captures_oldest_first(self) -> list[_StoredCapture]:
        """Return stored captures sorted from oldest to newest."""

        return sorted(self._iter_captures(), key=lambda capture: (capture.mtime, str(capture.path)))

    def _iter_captures(self) -> list[_StoredCapture]:
        """Return all stored `.jpg` captures under the configured root."""

        captures: list[_StoredCapture] = []
        for path in self._config.captures_dir.rglob("*.jpg"):
            if not path.is_file():
                continue
            try:
                stat = path.stat()
            except FileNotFoundError:
                continue
            captures.append(_StoredCapture(path=path, size=stat.st_size, mtime=stat.st_mtime))
        return captures

    def _unlink(self, path: Path) -> None:
        """Delete one file unless the job is running in dry-run mode."""

        if self._config.dry_run:
            return
        path.unlink(missing_ok=True)

    def _cleanup_empty_dirs(self) -> int:
        """Remove empty capture directories below the capture root."""

        if self._config.dry_run:
            return 0

        removed = 0
        directories = sorted(
            (path for path in self._config.captures_dir.rglob("*") if path.is_dir()),
            key=lambda path: len(path.parts),
            reverse=True,
        )
        for directory in directories:
            try:
                directory.rmdir()
            except OSError:
                continue
            removed += 1
        return removed


def _local_now() -> datetime:
    """Return the local timezone-aware wall clock time."""

    return datetime.now().astimezone()
