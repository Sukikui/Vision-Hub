# Docker

Docker Compose is the standard Vision-Hub runtime mode on the Raspberry Pi.

The physical Ethernet and Wi-Fi interfaces are configured on the host through NetworkManager. Docker runs the services that use those interfaces.

## Compose Stack

`compose.yaml` is the runtime contract for Docker. It describes which services exist, which images they use, which commands they run, and which files or volumes are mounted.

The stack contains:

```text
dnsmasq-field -> DHCP server for ESP32 nodes on eth0
dnsmasq-admin -> DHCP server for admin Wi-Fi clients on wlan0
mosquitto     -> local MQTT broker
vision-hub    -> Python application container
```

Starting the stack means running:

```bash
docker compose up -d --remove-orphans
```

Stopping the stack means running:

```bash
docker compose down
```

## Services

| Compose service | Image | Image source | Build context | Dockerfile | Network | Restart |
| --- | --- | --- | --- | --- | --- | --- |
| `dnsmasq-field` | `vision-hub-dnsmasq:local` | built locally | repository root `.` | `deploy/docker/dnsmasq.Dockerfile` | host | `unless-stopped` |
| `dnsmasq-admin` | `vision-hub-dnsmasq:local` | built locally | repository root `.` | `deploy/docker/dnsmasq.Dockerfile` | host | `unless-stopped` |
| `mosquitto` | `eclipse-mosquitto:2` | pulled from Docker registry | not built locally | none | host | `unless-stopped` |
| `vision-hub` | `vision-hub:local` | built locally | repository root `.` | `Dockerfile` | host | `unless-stopped` |

All services use `network_mode: host`. This keeps MQTT reachable through the Raspberry Pi field IP and lets dnsmasq handle DHCP broadcast traffic on `eth0` and `wlan0`.

Both dnsmasq containers also use:

```yaml
cap_add:
  - NET_ADMIN
  - NET_RAW
```

## Images

Mosquitto uses the official image:

```yaml
image: eclipse-mosquitto:2
```

Docker pulls it from the public registry on first use, then keeps it in Docker's local image store.

dnsmasq is built locally once and reused by both DHCP services:

```yaml
build:
  context: .
  dockerfile: deploy/docker/dnsmasq.Dockerfile
image: vision-hub-dnsmasq:local
```

Vision-Hub is also built locally:

```yaml
build:
  context: .
  dockerfile: Dockerfile
image: vision-hub:local
```

Local images are not regular files in the repository. Docker stores them in its internal storage, typically under `/var/lib/docker/` on Linux. They should be inspected through Docker commands:

```bash
docker images
```

## Runtime Commands

| Service | Entrypoint or command |
| --- | --- |
| `dnsmasq-field` | `dnsmasq --no-daemon --conf-file=/etc/dnsmasq.d/vision-hub.conf` |
| `dnsmasq-admin` | `dnsmasq --no-daemon --conf-file=/etc/dnsmasq.d/vision-hub.conf` |
| `mosquitto` | `mosquitto -c /mosquitto/config/vision-hub.conf` |
| `vision-hub` | `/opt/vision-hub/.venv/bin/python main.py` |

## Generated Configs

Render Docker-mounted configs:

```bash
deploy/docker/render-configs.sh
```

Template sources:

| Template | Generated file |
| --- | --- |
| `deploy/docker/templates/dnsmasq-field.conf.template` | `deploy/docker/generated/dnsmasq-field/vision-hub.conf` |
| `deploy/docker/templates/dnsmasq-admin.conf.template` | `deploy/docker/generated/dnsmasq-admin/vision-hub.conf` |
| `deploy/docker/templates/mosquitto.conf.template` | `deploy/docker/generated/mosquitto/vision-hub.conf` |
| `deploy/docker/templates/vision-hub-stack.service.template` | `/etc/systemd/system/vision-hub-stack.service` |

Generated repository files and mount targets:

| Repository file | Container path | Mode | Consumer |
| --- | --- | --- | --- |
| `deploy/docker/generated/dnsmasq-field/vision-hub.conf` | `/etc/dnsmasq.d/vision-hub.conf` | read-only | `dnsmasq-field` |
| `deploy/docker/generated/dnsmasq-admin/vision-hub.conf` | `/etc/dnsmasq.d/vision-hub.conf` | read-only | `dnsmasq-admin` |
| `deploy/docker/generated/mosquitto/vision-hub.conf` | `/mosquitto/config/vision-hub.conf` | read-only | `mosquitto` |

The generated directory is ignored by Git because it contains derived local files. It can be deleted and recreated at any time:

```bash
deploy/docker/render-configs.sh
```

Docker Compose needs those generated files to exist before starting `dnsmasq-field`, `dnsmasq-admin`, and `mosquitto`, because they are mounted into the containers.

## Volumes

| Host path or Docker volume | Container path | Mode | Purpose |
| --- | --- | --- | --- |
| `models/yolo11n-ncnn` | `/opt/vision-hub/models/yolo11n-ncnn` | read-only | NCNN model artifacts |
| `mosquitto-data` | `/mosquitto/data` | read-write | Mosquitto persistence |
| `mosquitto-log` | `/mosquitto/log` | read-write | Mosquitto log directory |
| `${VISION_HUB_HOST_DATA_DIR:-/mnt/vision-hub-ssd/data}` | `/var/lib/vision-hub` | read-write | Vision-Hub capture storage |

Vision-Hub capture storage is a bind mount, not a Docker volume. This keeps received images on a predictable host path, so the Raspberry Pi can place them on an SSD mounted at `/mnt/vision-hub-ssd`.

The boot service loads `deploy/vision-hub-network.env` through systemd `EnvironmentFile`, so this variable is visible to `docker compose up` at boot:

```env
VISION_HUB_HOST_DATA_DIR=/mnt/vision-hub-ssd/data
```

`vision-hub` receives these runtime environment variables:

| Variable | Value |
| --- | --- |
| `VISION_HUB_MQTT_HOST` | `127.0.0.1` |
| `VISION_HUB_MQTT_PORT` | `1883` |
| `VISION_HUB_MODEL_PATH` | `/opt/vision-hub/models/yolo11n-ncnn` |
| `VISION_HUB_DATA_DIR` | `/var/lib/vision-hub` |

## Boot Service

Install the stack as a systemd service:

```bash
sudo deploy/docker/install-rpi.sh
```

The script verifies Docker and Docker Compose, renders the configs, installs `/etc/systemd/system/vision-hub-stack.service`, and enables it immediately.

The installed systemd unit is only the boot hook for Docker Compose. It is not a separate dnsmasq or Mosquitto service.

At boot, systemd runs:

```bash
docker compose up -d --remove-orphans
```

On shutdown it runs:

```bash
docker compose down
```

Docker restart policy handles container restarts after reboot or crash:

```yaml
restart: unless-stopped
```

Operationally this means:

| Command | Shows |
| --- | --- |
| `sudo systemctl status vision-hub-stack` | whether systemd started the Compose stack |
| `docker compose ps` | whether `dnsmasq-field`, `dnsmasq-admin`, `mosquitto`, and `vision-hub` containers are running |
| `docker compose logs mosquitto` | Mosquitto logs from inside the container |
| `docker compose logs dnsmasq-field` | field DHCP logs from inside the container |
| `docker compose logs dnsmasq-admin` | admin Wi-Fi DHCP logs from inside the container |

## Verification

```bash
deploy/docker/render-configs.sh
deploy/docker/install-rpi.sh --render-service-only
docker compose config
docker compose ps
docker compose logs dnsmasq-field
docker compose logs dnsmasq-admin
docker compose logs mosquitto
docker compose logs vision-hub
```

Check the systemd unit:

```bash
sudo systemctl status vision-hub-stack
```
