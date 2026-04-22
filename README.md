<div align="center">

# Vision-Hub

[![python](https://img.shields.io/badge/-Python_3.13-blue?logo=python&logoColor=white)](https://docs.python.org/3.13/)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)

</div>

🚧 Under construction 🚧

Important: for ESP32 firmware context, see <https://github.com/Sukikui/ESP32-Vision-Node>.

## Documentation

- [Deployment](docs/deployment.md)
- [Network](docs/network.md)
- [Docker](docs/docker.md)
- [Home Assistant](docs/home-assistant.md)
- [Storage](docs/storage.md)
- [Inference](docs/inference.md)

## System Overview

### Tech Stack

| Layer | Choice |
| --- | --- |
| Target OS | Raspberry Pi OS Lite 64-bit |
| Runtime | Python `>=3.13` |
| Package manager | `uv` |
| Container runtime | Docker Engine with Docker Compose plugin |
| Local MQTT broker | Mosquitto |
| Field and admin DHCP | dnsmasq |
| Local operator UI | Home Assistant Container |
| MQTT client | `paho-mqtt` |
| Configuration | YAML |
| Inference runtime | NCNN |
| Person detector | YOLO11n exported to NCNN |

### Runtime Requirements

| Item | Requirement |
| --- | --- |
| Python environment | project `.venv` created with `uv sync` |
| Local interfaces | Raspberry Pi Ethernet and admin Wi-Fi configured from `deploy/rpi/` |
| Docker stack | `compose.yaml` rendered and managed through `deploy/docker/` |
| AI model | YOLO11n exported to NCNN with `tools/export_yolo_ncnn.py` |
| Model files | `model.ncnn.param`, `model.ncnn.bin`, and `metadata.yaml` present in the model directory |
| Capture storage | host directory mounted at `/var/lib/vision-hub` in the container |
| Home Assistant config | host directory mounted at `/config` in the Home Assistant container |
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

### 3. Configure Local Network Interfaces

Raspberry Pi field Ethernet and admin Wi-Fi configuration is stored in [`deploy/vision-hub-network.env`](deploy/vision-hub-network.env).

Before running the script on a real Raspberry Pi, replace the committed admin Wi-Fi placeholder:

```env
ADMIN_WIFI_PASSWORD=change-this-admin-password
```

with a private WPA password of 8 to 63 characters. The script refuses to configure the admin access point while the placeholder is still present.

```bash
sudo deploy/rpi/configure-network-interfaces.sh
```

### 4. Export the Person Detection Model

Run from the repository root:

```bash
uv run --with ultralytics --with pnnx python tools/export_yolo_ncnn.py \
  --model yolo11n.pt \
  --output-dir models/yolo11n-ncnn
```

The export tool uses Ultralytics and PNNX only during provisioning. The Vision-Hub service does not depend on them at runtime.

Ultralytics creates its NCNN export in a temporary generated folder such as `yolo11n_ncnn_model/`. The Vision-Hub tool then installs the runtime `.param`/`.bin` files and the export metadata into the stable directory used by the service:

```text
models/yolo11n-ncnn/
  model.ncnn.param
  model.ncnn.bin
  metadata.yaml
```

Docker mounts this repository directory read-only into the `vision-hub` container at `/opt/vision-hub/models/yolo11n-ncnn`.

Model artifacts are not versioned in Git.

### 5. Install the Docker Stack

Capture data is stored through the host path configured in [`deploy/vision-hub-network.env`](deploy/vision-hub-network.env):

```env
VISION_HUB_HOST_DATA_DIR=/var/lib/vision-hub-data
HOME_ASSISTANT_CONFIG_DIR=/var/lib/vision-hub-homeassistant
HOME_ASSISTANT_TZ=Europe/Paris
ADMIN_DNS_NAME=vision-hub.lan
VISION_HUB_NODE_IDS=p4-001
```

On the Raspberry Pi, this path points to the host directory used for received frames. In a microSD-only deployment, it lives on the microSD card. If external storage is added later, this value can point to that mount instead. Inside the container it is always mounted as `/var/lib/vision-hub`.

`VISION_HUB_NODE_IDS` controls which ESP32 cards are generated in the Home Assistant dashboard. MQTT Discovery still creates entities dynamically; this value only controls the dashboard layout.

```bash
deploy/docker/render-configs.sh
sudo deploy/docker/install-rpi.sh
```

The installed systemd service runs `docker compose up -d` at boot. The stack contains `dnsmasq-field`, `dnsmasq-admin`, `mosquitto`, `vision-hub`, and `homeassistant`, each with `restart: unless-stopped`.

Home Assistant is available from the admin Wi-Fi network at:

```text
http://vision-hub.lan:8123
```

The `:8123` port is required because Home Assistant does not listen on port `80`.

Captured images are browsable in Home Assistant through `Media -> captures`.

## Development

### Local Model Export

```bash
uv run --with ultralytics --with pnnx python tools/export_yolo_ncnn.py \
  --model yolo11n.pt \
  --output-dir models/yolo11n-ncnn
```

### Run Tests

```bash
uv run python -m unittest discover -v -s tests
```
