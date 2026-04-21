# Storage

## Overview

Vision-Hub stores complete JPEG frames received from ESP32-P4 nodes over MQTT.

The storage layer does not decode or transform the image. It reconstructs the original JPEG byte stream from binary chunks in RAM, validates it, and writes the final JPEG once to the Raspberry Pi storage path.

No persistent metadata sidecar is created. The useful identity data is carried by the directory layout, the filename, and the in-memory `StoredFrame` returned to the service pipeline.

Raspberry Pi deployments commonly run from microSD only: OS, Docker, code, logs, model files, and captures share the same flash storage. Vision-Hub therefore avoids chunk-by-chunk disk writes. Active transfers are buffered in memory, then only complete and valid JPEG files are persisted.

## Raspberry Pi 5 microSD Constraint

Raspberry Pi 5 exposes a microSD slot with SDR104 support. SDR104 is a UHS-I bus mode capped at `104 MB/s` by the SD standard; actual write speed depends on the card. Raspberry Pi's own SD card documentation lists Raspberry Pi 5 in SDR104 mode with `2,000` random 4 KB write IOPS for its official A2 cards.

For Vision-Hub, the important workload is sequential JPEG writes, not continuous raw video. At `1 frame/s`, the required write bandwidth is:

```text
average_jpeg_size_bytes * node_count / second
```

Even using the current safety limit of `5 MB` per JPEG, one node at `1 frame/s` writes about `5 MB/s`; four nodes at that worst-case limit write about `20 MB/s`. That is below the SDR104 bus ceiling and within the range expected from a good U3/V30/A2 microSD card. The remaining risk is not instantaneous speed but flash wear, so storage is RAM-first, motion/event driven, and must be paired with retention cleanup.

References: [Raspberry Pi 5 announcement](https://www.raspberrypi.com/news/introducing-raspberry-pi-5/), [SD Association bus speeds](https://www.sdcard.org/developers/sd-standard-overview/bus-speed-default-speed-high-speed-uhs-sd-express/), [Raspberry Pi SD card documentation](https://www.raspberrypi.com/documentation/accessories/sd-cards.html).

## Runtime Contract

The storage assembler is independent from the MQTT client. It receives already parsed `ImageMetaMessage`, `ImageChunkMessage`, and `ImageDoneMessage` objects, writes the capture to disk, then returns a `StoredFrame` for the rest of the service pipeline.

| Layer | Contract |
| --- | --- |
| Runtime | Python 3.13+ |
| Input | MQTT image messages parsed by `vision_hub.mqtt` |
| Transfer buffer | RAM `bytearray`, one active buffer per capture |
| Final file | `.jpg`, written after all chunks are validated |
| Timestamp | Raspberry Pi local wall clock, millisecond precision |
| Platform | Linux/Unix-like filesystem with POSIX-style paths and permissions |
| Docker persistence | bind mount from host storage path to `/var/lib/vision-hub` |

The code is not tied to Raspberry Pi hardware. Raspberry Pi OS Lite is the deployment target because it provides the expected Linux filesystem behavior, Docker bind mounts, local clock, and microSD or external storage support.

## MQTT Image Contract

One capture is transferred as three message categories:

| Topic suffix | Payload | Meaning |
| --- | --- | --- |
| `image/{capture_id}/meta` | JSON | transfer metadata |
| `image/{capture_id}/chunk/{index}` | binary | raw JPEG bytes for one chunk |
| `image/{capture_id}/done` | JSON | transfer completion marker |

The metadata message contains:

| Field | Meaning |
| --- | --- |
| `capture_id` | capture identifier |
| `content_type` | expected MIME type, currently `image/jpeg` |
| `total_size` | full image size in bytes |
| `chunk_size` | nominal chunk size |
| `chunk_count` | number of expected chunks |

Chunks are not JSON and are not base64 encoded. They are raw binary slices of the final JPEG file.

## Memory Model

Each active capture reserves one RAM buffer sized exactly from `total_size`.

Default limits:

| Setting | Default | Meaning |
| --- | ---: | --- |
| `max_image_size_bytes` | `5_000_000` | maximum size for one received image |
| `max_buffered_bytes` | `64_000_000` | maximum RAM reserved by all active captures |
| `session_timeout_s` | `30` | maximum age of an incomplete transfer |

If a node starts a transfer that would exceed these limits, the transfer is rejected before allocating more memory.

## Retention Policy

Capture retention is handled by `StorageRetentionJob`. The job is independent from MQTT reception, but the service calls different methods at different moments.

| Method | When | Work done |
| --- | --- | --- |
| `ensure_free_space()` | after each stored `StoredFrame` | cheap free-space check; scans and deletes only if free space is too low |
| `cleanup_by_age()` | periodic background task, normally once per day | scan captures and delete files older than the age cutoff |
| `run_once()` | manual maintenance pass | age cleanup, then free-space cleanup |

Retention uses these defaults:

| Rule | Default | Effect |
| --- | ---: | --- |
| maximum age | `31 days` | delete `.jpg` captures older than the cutoff |
| minimum free space | `5 GB` | start disk-pressure cleanup |
| target free space | `10 GB` | delete oldest remaining captures until this free-space target is reached |

The free-space check is safe to run after each image because it only calls the filesystem usage API when space is healthy. It scans capture files only when free space is below `5 GB`. Age cleanup is not tied to writes because deleting files older than 31 days is not time-critical and requires a file scan.

Only `.jpg` capture files are managed. Empty date and node directories are removed after file deletion.

## Storage Layout

Container path:

```text
/var/lib/vision-hub/
```

Final captures:

```text
/var/lib/vision-hub/
  captures/
    p4-001/
      2026/
        04/
          21/
            2026-04-21_14-38-12.423_cap-abc123.jpg
```

The first capture directory level is always the ESP32 `node_id`. This keeps captures from different nodes physically separated on disk.

## Filename Contract

Final JPEG files use this format:

```text
{YYYY-MM-DD}_{HH-MM-SS.mmm}_{capture_id}.jpg
```

Example:

```text
2026-04-21_14-38-12.423_cap-abc123.jpg
```

The timestamp is generated from the Raspberry Pi local time when the `meta` message is received. It is intentionally human-readable and filename-safe.

The date is also repeated in the directory path to keep large capture sets navigable without a database.

## Reconstruction Flow

| Step | Operation |
| --- | --- |
| 1 | receive `ImageMetaMessage` |
| 2 | validate node id, capture id, content type, total size, chunk size, and chunk count |
| 3 | allocate one RAM buffer with `total_size` bytes |
| 4 | receive `ImageChunkMessage` objects |
| 5 | write each chunk into RAM at `chunk_index * chunk_size` |
| 6 | receive `ImageDoneMessage` |
| 7 | verify all chunks are present |
| 8 | verify buffer size equals `total_size` |
| 9 | verify JPEG start and end markers |
| 10 | write the final `.jpg` file |
| 11 | return a `StoredFrame` object |

Chunk order is not significant. The assembler writes each chunk into its expected RAM offset.

## Validation Rules

| Validation | Failure |
| --- | --- |
| `node_id` and `capture_id` must be safe path segments | reject transfer |
| `content_type` must be `image/jpeg` | reject transfer |
| `total_size` must be positive and below configured maximum | reject transfer |
| total active buffers must remain below configured maximum | reject transfer |
| `chunk_count` must match `ceil(total_size / chunk_size)` | reject transfer |
| chunk index must be within range | reject chunk |
| chunk payload size must match expected size | reject chunk |
| all chunks must be present before finalization | keep transfer incomplete and report error |
| final buffer size must match `total_size` | reject finalization |
| JPEG bytes must start with `FF D8` and end with `FF D9` | reject finalization |

## Python API

Storage config:

```python
from pathlib import Path

from vision_hub.storage import ImageStoreConfig

config = ImageStoreConfig(
    data_dir=Path("/var/lib/vision-hub"),
    max_image_size_bytes=5_000_000,
    max_buffered_bytes=64_000_000,
    session_timeout_s=30,
    allowed_content_types={"image/jpeg"},
)
```

Assembler:

```python
from vision_hub.storage import ImageAssembler

assembler = ImageAssembler(config)

stored_frame = assembler.handle(message)
if stored_frame is not None:
    print(stored_frame.image_path)
```

Returned frame:

| Field | Meaning |
| --- | --- |
| `node_id` | ESP32 node identifier |
| `capture_id` | capture identifier |
| `image_path` | final JPEG path |
| `received_at` | Raspberry Pi local time when metadata arrived |
| `completed_at` | Raspberry Pi local time when finalization succeeded |
| `total_size` | final JPEG size in bytes |

Retention config:

```python
from pathlib import Path

from vision_hub.storage import StorageRetentionConfig, StorageRetentionJob

retention = StorageRetentionJob(
    StorageRetentionConfig(
        captures_dir=Path("/var/lib/vision-hub/captures"),
        max_age_days=31,
        min_free_bytes=5_000_000_000,
        target_free_bytes=10_000_000_000,
        age_cleanup_interval_s=86_400,
    )
)

# After each StoredFrame:
retention.ensure_free_space()

# Periodic background cleanup:
retention.cleanup_by_age()
```

## Docker Storage

Inside the `vision-hub` container, the storage root is stable:

```text
/var/lib/vision-hub
```

On the Raspberry Pi host, the backing path is configured in:

```text
deploy/vision-hub-network.env
```

Default:

```env
VISION_HUB_HOST_DATA_DIR=/var/lib/vision-hub-data
```

Docker Compose mounts it as:

```text
${VISION_HUB_HOST_DATA_DIR:-/var/lib/vision-hub-data}:/var/lib/vision-hub
```

This is a bind mount, not a Docker volume. The stored frames therefore live on a predictable host path. In the default microSD-only deployment, that path is on the microSD card. If external storage is added later, only `VISION_HUB_HOST_DATA_DIR` needs to change.
