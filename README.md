# Vision-Hub

Under construction.

Important: for ESP32 firmware context, see <https://github.com/Sukikui/ESP32-Vision-Node>.

## Documentation

- [Inference](docs/inference.md)

## System Overview

### Tech Stack

| Layer | Choice |
| --- | --- |
| Target OS | Raspberry Pi OS Lite 64-bit |
| Runtime | Python `>=3.13` |
| Package manager | `uv` |
| Local MQTT broker | Mosquitto |
| MQTT client | `paho-mqtt` |
| Configuration | YAML |
| Inference runtime | NCNN |
| Person detector | YOLO11n exported to NCNN |

### Runtime Requirements

| Item | Requirement |
| --- | --- |
| Python environment | project `.venv` created with `uv sync` |
| Local MQTT | Mosquitto installed, enabled, and reachable by the ESP32 nodes |
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

### 3. Install the MQTT Broker

```bash
sudo apt update
sudo apt install mosquitto mosquitto-clients
sudo systemctl enable --now mosquitto
```

The ESP32 nodes must be able to reach the Raspberry Pi broker on the field Ethernet network, typically on port `1883`.

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
