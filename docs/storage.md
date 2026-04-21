# Storage

## Overview

Vision-Hub stores complete JPEG frames received from ESP32-P4 nodes over MQTT.

The storage layer does not decode or transform the image. It reconstructs the original JPEG byte stream from binary chunks, validates it, and writes it to the Raspberry Pi storage path.

No persistent metadata sidecar is created. The useful identity data is carried by the directory layout, the filename, and the in-memory `StoredFrame` returned to the service pipeline.

## Runtime Contract

The storage assembler is independent from the MQTT client. It receives already parsed `ImageMetaMessage`, `ImageChunkMessage`, and `ImageDoneMessage` objects, writes the capture to disk, then returns a `StoredFrame` for the rest of the service pipeline.

| Layer | Contract |
| --- | --- |
| Runtime | Python 3.13+ |
| Input | MQTT image messages parsed by `vision_hub.mqtt` |
| Temporary file | `.part`, written with Python filesystem APIs and `pathlib` |
| Final file | `.jpg`, finalized with atomic `Path.replace()` |
| Timestamp | Raspberry Pi local wall clock, millisecond precision |
| Platform | Linux/Unix-like filesystem with POSIX-style paths and permissions |
| Docker persistence | bind mount from host SSD path to `/var/lib/vision-hub` |

The code is not tied to Raspberry Pi hardware. Raspberry Pi OS Lite is the deployment target because it provides the expected Linux filesystem behavior, Docker bind mounts, local clock, and SSD mount support.

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

Temporary files:

```text
/var/lib/vision-hub/
  tmp/
    p4-001/
      cap-abc123.part
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
| 3 | create `tmp/{node_id}/{capture_id}.part` |
| 4 | receive `ImageChunkMessage` objects |
| 5 | write each chunk at `chunk_index * chunk_size` |
| 6 | receive `ImageDoneMessage` |
| 7 | verify all chunks are present |
| 8 | verify final file size equals `total_size` |
| 9 | verify JPEG start and end markers |
| 10 | atomically rename `.part` to `.jpg` |
| 11 | return a `StoredFrame` object |

Chunk order is not significant. The assembler writes each chunk at its expected file offset.

## Validation Rules

| Validation | Failure |
| --- | --- |
| `node_id` and `capture_id` must be safe path segments | reject transfer |
| `content_type` must be `image/jpeg` | reject transfer |
| `total_size` must be positive and below configured maximum | reject transfer |
| `chunk_count` must match `ceil(total_size / chunk_size)` | reject transfer |
| chunk index must be within range | reject chunk |
| chunk payload size must match expected size | reject chunk |
| all chunks must be present before finalization | keep transfer incomplete and report error |
| final file size must match `total_size` | reject finalization |
| JPEG bytes must start with `FF D8` and end with `FF D9` | reject finalization |

## Python API

Storage config:

```python
from pathlib import Path

from vision_hub.storage import ImageStoreConfig

config = ImageStoreConfig(
    data_dir=Path("/var/lib/vision-hub"),
    max_image_size_bytes=5_000_000,
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
VISION_HUB_HOST_DATA_DIR=/mnt/vision-hub-ssd/data
```

Docker Compose mounts it as:

```text
${VISION_HUB_HOST_DATA_DIR:-/mnt/vision-hub-ssd/data}:/var/lib/vision-hub
```

This is a bind mount, not a Docker volume. The stored frames therefore live on a predictable host path and can be placed directly on an SSD.
