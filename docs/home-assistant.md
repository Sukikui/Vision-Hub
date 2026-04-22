# Home Assistant

Home Assistant is the local operator UI for Vision-Hub on the Raspberry Pi.

It displays clean Vision-Hub entities, not raw ESP32 firmware topics. ESP32 nodes publish technical MQTT messages to the hub; Vision-Hub turns them into stable Home Assistant devices, entities, images, and commands.

## Runtime Role

```text
ESP32 nodes -> raw MQTT -> Vision-Hub -> clean MQTT Discovery/state -> Home Assistant
Home Assistant buttons/controls -> clean MQTT commands -> Vision-Hub -> ESP32 command topics
```

Home Assistant never consumes:

- binary image chunks;
- raw ESP32 retry details;
- internal transfer sessions;
- firmware command topics;
- per-chunk debug state.

Those details stay inside Vision-Hub.

## MQTT Integration

Home Assistant runs in the Docker Compose stack as `homeassistant` with `network_mode: host`.

The MQTT integration connects to the local Mosquitto broker:

| Field | Value |
| --- | --- |
| Broker | `127.0.0.1` |
| Port | `1883` |
| Discovery prefix | `homeassistant` |

MQTT Discovery creates devices and entities from retained config messages:

```text
homeassistant/<component>/<unique_id>/config
```

Vision-Hub publishes clean retained state under:

```text
vision-hub/status
vision-hub/system/state
vision-hub/nodes/<node_id>/state
vision-hub/nodes/<node_id>/capture
vision-hub/nodes/<node_id>/detection
vision-hub/nodes/<node_id>/latest/image
```

Home Assistant publishes commands under:

```text
vision-hub/commands/<node_id>/<command>
vision-hub/commands/<node_id>/<setting>/set
```

## Device Model

Home Assistant sees two device families:

| Device family | Device identifier | Source |
| --- | --- | --- |
| Vision-Hub / Raspberry Pi | `vision_hub_rpi` | local hub runtime |
| ESP32 vision node | `vision_hub_node_<slug>` | firmware `node_id` |

Each ESP32 device is linked to the hub with:

```text
via_device = vision_hub_rpi
```

Example mapping:

| Firmware `node_id` | Home Assistant slug | Device identifier |
| --- | --- | --- |
| `p4-001` | `p4_001` | `vision_hub_node_p4_001` |

The slug is only used for Home Assistant entity IDs. The original firmware `node_id` remains present in MQTT topics and device names.

## ESP32 Node Entities

Each ESP32 node exposes 27 Home Assistant entities.

### Runtime State

| Entity | Source field | Purpose | Category |
| --- | --- | --- | --- |
| `binary_sensor.<node>_online` | `online` | Node connectivity | primary |
| `sensor.<node>_ip` | `ip` | Current node IP address | diagnostic |
| `sensor.<node>_uptime` | `uptime_s` | Firmware uptime in seconds | diagnostic |
| `sensor.<node>_last_seen` | `last_seen` | Hub-side timestamp of latest node update | diagnostic |
| `binary_sensor.<node>_motion` | `motion_detected` | Latest motion state | primary |
| `sensor.<node>_last_event` | `last_event` | Latest firmware event name | diagnostic |
| `sensor.<node>_last_boot_event` | `last_boot_event` | Last `boot_completed` timestamp | diagnostic |
| `sensor.<node>_last_config_update` | `last_config_update` | Last `config_updated` timestamp | diagnostic |
| `binary_sensor.<node>_capture_error` | `capture_error` | Latest capture failure state | primary warning |

State topic:

```text
vision-hub/nodes/<node_id>/state
```

Payload shape:

```json
{
  "online": true,
  "ip": "192.168.50.20",
  "uptime_s": 1234,
  "last_seen": "2026-04-21T14:38:12.423+02:00",
  "motion_detected": true,
  "last_event": "motion_detected",
  "last_boot_event": "2026-04-21T14:10:02.118+02:00",
  "last_config_update": "2026-04-21T14:12:33.004+02:00",
  "capture_error": false,
  "motion_enabled": true,
  "ir_mode": "capture",
  "heartbeat_interval_s": 30
}
```

### Capture State

| Entity | Source field | Purpose | Category |
| --- | --- | --- | --- |
| `sensor.<node>_last_capture` | `last_capture` | Timestamp of latest stored capture | primary |
| `sensor.<node>_last_capture_id` | `last_capture_id` | Latest firmware capture id | diagnostic |
| `sensor.<node>_last_image_size` | `last_image_size_bytes` | Latest JPEG size in bytes | diagnostic |
| `binary_sensor.<node>_last_capture_ok` | `last_capture_ok` | Last transfer and storage result | primary |
| `sensor.<node>_last_capture_path` | `last_capture_path` | Stored JPEG path on the hub | diagnostic |

Capture topic:

```text
vision-hub/nodes/<node_id>/capture
```

Payload shape:

```json
{
  "last_capture": "2026-04-21T14:38:13.019+02:00",
  "last_capture_id": "cap-abc123",
  "last_image_size_bytes": 184320,
  "last_capture_ok": true,
  "last_capture_path": "/var/lib/vision-hub/captures/p4-001/2026/04/21/2026-04-21_14-38-12.423_cap-abc123.jpg",
  "content_type": "image/jpeg",
  "chunk_count": 91
}
```

`content_type` and `chunk_count` are diagnostic attributes. Home Assistant does not expose MQTT image chunks.

### Inference State

| Entity | Source field | Purpose | Category |
| --- | --- | --- | --- |
| `binary_sensor.<node>_person_detected` | `person_detected` | Whether a person is detected | primary |
| `sensor.<node>_person_count` | `person_count` | Number of detected people | primary |
| `sensor.<node>_person_confidence` | `best_score` | Best person score in percent | primary |
| `sensor.<node>_last_inference` | `last_inference` | Timestamp of latest inference | diagnostic |
| `sensor.<node>_inference_ms` | `inference_ms` | Local inference latency | diagnostic |
| `sensor.<node>_last_inference_image_path` | `last_image_path` | Image used by latest inference | diagnostic |

Detection topic:

```text
vision-hub/nodes/<node_id>/detection
```

Payload shape:

```json
{
  "person_detected": true,
  "person_count": 2,
  "best_score": 0.87,
  "last_inference": "2026-04-21T14:38:13.094+02:00",
  "inference_ms": 72,
  "last_image_path": "/var/lib/vision-hub/captures/p4-001/2026/04/21/2026-04-21_14-38-12.423_cap-abc123.jpg",
  "detections": [
    {
      "class_name": "person",
      "score": 0.87,
      "box": [120.0, 40.0, 210.0, 310.0]
    }
  ]
}
```

Bounding boxes are exposed as attributes only. The dashboard displays the plain latest image unless Vision-Hub explicitly publishes an annotated image later.

### Latest Image

| Entity | Source topic | Purpose |
| --- | --- | --- |
| `image.<node>_latest_capture` | `vision-hub/nodes/<node_id>/latest/image` | Latest validated JPEG |

The image entity uses MQTT Image with:

```text
content_type = image/jpeg
payload = raw JPEG bytes
retain = true
```

Only the latest image is published through MQTT. The full archive is exposed through Home Assistant Media Browser.

### Commands And Runtime Controls

| Entity | MQTT command topic | Firmware command |
| --- | --- | --- |
| `button.<node>_ping` | `vision-hub/commands/<node_id>/ping` | `cmd/ping` |
| `button.<node>_capture` | `vision-hub/commands/<node_id>/capture` | `cmd/capture` |
| `button.<node>_reboot` | `vision-hub/commands/<node_id>/reboot` | `cmd/reboot` |
| `switch.<node>_motion_enabled` | `vision-hub/commands/<node_id>/motion_enabled/set` | `cmd/config` |
| `select.<node>_ir_mode` | `vision-hub/commands/<node_id>/ir_mode/set` | `cmd/config` |
| `number.<node>_heartbeat_interval` | `vision-hub/commands/<node_id>/heartbeat_interval/set` | `cmd/config` |

Command replies update node diagnostics:

| Field | Purpose |
| --- | --- |
| `last_command_ok` | Last command status |
| `last_command_error` | Last command error message |
| `last_ping_ms` | Ping round-trip time measured by Vision-Hub |

Vision-Hub exposes node-specific controls only. Firmware broadcast commands are intentionally not exposed in Home Assistant.

## Hub Entities

The Raspberry Pi / Vision-Hub device exposes 19 entities.

| Entity | Source field | Purpose | Category |
| --- | --- | --- | --- |
| `binary_sensor.vision_hub_online` | `vision-hub/status` | Hub availability | primary |
| `binary_sensor.vision_hub_mqtt_connected` | `mqtt_connected` | Vision-Hub MQTT client state | primary |
| `binary_sensor.mosquitto_available` | `mosquitto_available` | Local broker health | primary |
| `binary_sensor.vision_hub_inference_ready` | `inference_ready` | NCNN model readiness | primary |
| `binary_sensor.vision_hub_storage_pressure` | `storage_pressure` | Free-space warning | primary warning |
| `sensor.vision_hub_storage_free` | `storage_free_bytes` | Free storage in bytes | primary |
| `sensor.vision_hub_storage_used_percent` | `storage_used_percent` | Storage use percent | primary |
| `sensor.vision_hub_capture_count` | `capture_count` | Total stored captures | diagnostic |
| `sensor.vision_hub_last_capture` | `last_capture` | Last capture timestamp across all nodes | primary |
| `sensor.vision_hub_last_age_cleanup` | `last_age_cleanup` | Last age-based cleanup timestamp | diagnostic |
| `sensor.vision_hub_retention_deleted_files` | `retention_deleted_files` | Files removed by latest retention run | diagnostic |
| `sensor.vision_hub_retention_deleted_bytes` | `retention_deleted_bytes` | Bytes removed by latest retention run | diagnostic |
| `sensor.vision_hub_uptime` | `vision_hub_uptime_s` | Vision-Hub process uptime | diagnostic |
| `sensor.vision_hub_version` | `vision_hub_version` | Application version | diagnostic |
| `sensor.vision_hub_cpu_temperature` | `rpi_cpu_temperature_c` | Raspberry Pi CPU temperature | primary |
| `sensor.vision_hub_memory_used` | `rpi_memory_used_percent` | RAM usage percent | primary |
| `sensor.vision_hub_load` | `rpi_load_1m` | 1-minute CPU load | diagnostic |
| `sensor.vision_hub_admin_ip` | `rpi_admin_ip` | Admin Wi-Fi IP | diagnostic |
| `sensor.vision_hub_field_ip` | `rpi_field_ip` | ESP32 field LAN IP | diagnostic |

Hub state topic:

```text
vision-hub/system/state
```

Payload shape:

```json
{
  "mqtt_connected": true,
  "mosquitto_available": true,
  "inference_ready": true,
  "storage_pressure": false,
  "storage_free_bytes": 24800000000,
  "storage_used_percent": 42.1,
  "capture_count": 18421,
  "last_capture": "2026-04-21T14:38:13.019+02:00",
  "last_age_cleanup": "2026-04-21T03:00:00.000+02:00",
  "retention_deleted_files": 120,
  "retention_deleted_bytes": 340000000,
  "vision_hub_uptime_s": 188400,
  "vision_hub_version": "0.1.0",
  "rpi_cpu_temperature_c": 51.3,
  "rpi_memory_used_percent": 41.0,
  "rpi_load_1m": 0.72,
  "rpi_admin_ip": "192.168.60.1",
  "rpi_field_ip": "192.168.50.1"
}
```

## Dashboard

Vision-Hub installs a dedicated Home Assistant dashboard named `Vision-Hub`.

Dashboard source:

```text
/config/dashboards/vision-hub.yaml
```

The dashboard is generated from `VISION_HUB_NODE_IDS` in `deploy/vision-hub-network.env`. Entity discovery remains dynamic at runtime, but cards are generated from known node IDs so the layout is stable and readable.

### Overview View

Purpose: high-level field status.

Cards:

| Card | Content |
| --- | --- |
| Hub status glance | Hub online, MQTT, Mosquitto, inference, storage pressure |
| Storage gauges | Free space, used percent, capture count |
| System gauges | CPU temperature, memory used, load |
| Latest capture summary | Last global capture, last cleanup, retention result |

### Cameras View

Purpose: one operational card per ESP32.

Each ESP32 card contains:

```text
<node name>
[latest image]

Online | Motion | Person detected
Person count | Confidence | Last capture

IP | Uptime | Last seen | Last event
Capture OK | Image size | Capture error

[Ping] [Capture] [Reboot]
Motion enabled | IR mode | Heartbeat interval
```

The latest image comes from:

```text
image.<node>_latest_capture
```

The operational status comes from MQTT sensors and binary sensors, not from parsing images in Home Assistant.

### Node Diagnostics View

Purpose: detailed per-node troubleshooting.

Per node:

| Field | Display |
| --- | --- |
| Last boot event | timestamp |
| Last config update | timestamp |
| Last capture ID | text |
| Last capture path | text |
| Last inference | timestamp |
| Inference latency | milliseconds |
| Last inference image path | text |
| Capture error | warning |

### System View

Purpose: Raspberry Pi and service health.

Cards:

| Card | Content |
| --- | --- |
| Vision-Hub process | online, version, uptime |
| MQTT | Vision-Hub MQTT connection, Mosquitto availability |
| Inference | NCNN readiness |
| Storage | free, used percent, pressure, retention counters |
| Raspberry Pi | CPU temperature, RAM, load, admin IP, field IP |

### Captures View

Purpose: direct access to stored JPEG archives.

The capture directory is mounted read-only into Home Assistant:

```text
/media/vision-hub-captures
```

On the Raspberry Pi this directory is backed by the host path configured through `VISION_HUB_HOST_DATA_DIR`. With the default microSD-only deployment, the archive physically lives on the Raspberry Pi microSD card; Home Assistant only receives a read-only view of the same files.

The operator browses archived images through:

```text
Media -> captures -> <node_id> -> YYYY -> MM -> DD
```

This uses Home Assistant Media Browser instead of a custom gallery card. The filesystem layout remains the source of truth:

```text
captures/
  p4-001/
    2026/
      04/
        21/
          2026-04-21_14-38-12.423_cap-abc123.jpg
```

## Entity Counts

| Scope | Count |
| --- | ---: |
| Hub / Raspberry Pi | 19 |
| Each ESP32 node | 27 |

Total:

```text
19 + 27 * number_of_nodes
```

Examples:

| Nodes | Entities |
| ---: | ---: |
| 1 | 46 |
| 2 | 73 |
| 4 | 127 |
| 8 | 235 |

## References

- Home Assistant MQTT integration: <https://www.home-assistant.io/integrations/mqtt/>
- Home Assistant MQTT Image: <https://www.home-assistant.io/integrations/image.mqtt/>
- Home Assistant MQTT Button: <https://www.home-assistant.io/integrations/button.mqtt/>
- Home Assistant dashboards: <https://www.home-assistant.io/dashboards/dashboards/>
- Home Assistant Picture Glance card: <https://www.home-assistant.io/dashboards/picture-glance/>
- Home Assistant Picture Entity card: <https://www.home-assistant.io/dashboards/picture-entity/>
- Home Assistant Tile card: <https://www.home-assistant.io/dashboards/tile/>
- Home Assistant Media Source: <https://www.home-assistant.io/integrations/media_source/>
