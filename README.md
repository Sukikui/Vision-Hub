# Vision-Hub

Under construction.

Important: for ESP32 firmware context, see <https://github.com/Sukikui/ESP32-Vision-Node>.

## Documentation

- [Deployment](docs/deployment.md)
- [Inference](docs/inference.md)

## System Overview

### Tech Stack

| Layer | Choice |
| --- | --- |
| Target OS | Raspberry Pi OS Lite 64-bit |
| Runtime | Python `>=3.13` |
| Package manager | `uv` |
| Local MQTT broker | Mosquitto |
| Field DHCP server | dnsmasq |
| MQTT client | `paho-mqtt` |
| Configuration | YAML |
| Inference runtime | NCNN |
| Person detector | YOLO11n exported to NCNN |

### Runtime Requirements

| Item | Requirement |
| --- | --- |
| Python environment | project `.venv` created with `uv sync` |
| Field interface | Raspberry Pi Ethernet configured from `deploy/rpi/` |
| Field DHCP | dnsmasq configured from `deploy/dnsmasq/` |
| Local MQTT | Mosquitto configured from `deploy/mosquitto/` |
| AI model | YOLO11n exported to NCNN with `tools/export_yolo_ncnn.py` |
| Model files | `model.ncnn.param` and `model.ncnn.bin` present in the model directory |
| Field network | ESP32 nodes can reach the Raspberry Pi MQTT broker, typically on port `1883` |

## Getting Started

### 1. Install uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Restart the shell if `uv` is not immediately available on `PATH`.

Install Python `3.13` through uv if it is not already available:

```bash
uv python install 3.13
```

### 2. Create the Virtual Environment

```bash
uv sync
```

`uv sync` creates the project `.venv` if it does not already exist, then installs dependencies from `pyproject.toml` and `uv.lock`.

### 3. Configure Field Network Services

Raspberry Pi field interface configuration is stored in [`deploy/rpi/`](deploy/rpi/).
DHCP configuration for the ESP32 field network is stored in [`deploy/dnsmasq/`](deploy/dnsmasq/).
Mosquitto configuration for the local MQTT broker is stored in [`deploy/mosquitto/`](deploy/mosquitto/).

### 4. Export the Person Detection Model

```bash
uv run --with ultralytics python tools/export_yolo_ncnn.py \
  --model yolo11n.pt \
  --output-dir /opt/vision-hub/models/person-detector/yolo11n-ncnn
```

The export tool uses Ultralytics only during provisioning. The Vision-Hub service does not depend on Ultralytics at runtime.

Ultralytics creates its NCNN export in a generated folder such as `yolo11n_ncnn_model/`. The Vision-Hub tool then installs the resulting `.param` and `.bin` files into the stable directory used by the runtime:

```text
/opt/vision-hub/models/person-detector/yolo11n-ncnn/
  model.ncnn.param
  model.ncnn.bin
```

Model artifacts are not versioned in Git.

### 5. Run the Service

```bash
uv run python main.py
```

## Development

### Local Model Export

```bash
uv run --with ultralytics python tools/export_yolo_ncnn.py \
  --model yolo11n.pt \
  --output-dir models/person-detector/yolo11n-ncnn
```

### Run Tests

```bash
uv run python -m unittest discover -v -s tests
```
