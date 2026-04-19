# Deployment

Vision-Hub runs on a Raspberry Pi that acts as the central unit for an isolated ESP32 Ethernet field network.

The standard deployment mode is Docker Compose. The Raspberry Pi still owns the physical Ethernet configuration; Docker runs the services.

## Runtime Model

Vision-Hub uses one host-level systemd service to start the whole Docker stack:

```text
Raspberry Pi boot
  -> systemd
    -> vision-hub-stack.service
      -> docker compose up -d --remove-orphans
        -> dnsmasq container
        -> mosquitto container
        -> vision-hub container
```

There is no separate `dnsmasq.service` or `mosquitto.service` in the Docker deployment. Those processes run inside containers managed by Docker Compose.

## Runtime Responsibilities

| Responsibility | Implementation |
| --- | --- |
| stable field address | NetworkManager profile on the field Ethernet interface |
| DHCP for ESP32 nodes | dnsmasq container |
| local MQTT broker | Mosquitto container |
| hub application | Vision-Hub Python container |

## Deployment Files

| Path | Role |
| --- | --- |
| `deploy/vision-hub-field.env` | source of truth for field network values |
| `deploy/rpi/configure-field-interface.sh` | creates or updates the Raspberry Pi Ethernet profile |
| `deploy/docker/render-configs.sh` | renders Docker-mounted dnsmasq and Mosquitto configs |
| `deploy/docker/install-rpi.sh` | installs and enables the `vision-hub-stack.service` systemd unit |
| `compose.yaml` | defines the `dnsmasq`, `mosquitto`, and `vision-hub` containers |

The old non-Docker split is intentionally gone. dnsmasq and Mosquitto are not installed as independent host services; Compose starts them from `compose.yaml`.

## Installation Order

Configure the Raspberry Pi field interface once:

```bash
sudo deploy/rpi/configure-field-interface.sh
```

Export the NCNN model before starting the service:

```bash
uv run --with ultralytics --with pnnx python tools/export_yolo_ncnn.py \
  --model yolo11n.pt \
  --output-dir models/yolo11n-ncnn
```

Render Docker configs and install the boot service:

```bash
deploy/docker/render-configs.sh
sudo deploy/docker/install-rpi.sh
```

`render-configs.sh` can be run manually, but `install-rpi.sh` also runs it before installing the service. The installed systemd unit renders configs again at each start, then starts the Docker Compose stack.

## Docs

| Document | Content |
| --- | --- |
| [Network](network.md) | field LAN contract, DHCP gateway, Raspberry Pi interface |
| [Docker](docker.md) | Compose stack, containers, volumes, systemd boot service |
| [Inference](inference.md) | NCNN YOLO model, export, and runtime loading |

## Quick Verification

```bash
ip addr show eth0
sudo systemctl status vision-hub-stack
docker compose ps
docker compose logs dnsmasq
docker compose logs mosquitto
docker compose logs vision-hub
```

Expected ESP32 logs:

```text
ETHIP:192.168.50.xx
ETHGW:192.168.50.1
using DHCP gateway as MQTT broker: mqtt://192.168.50.1:1883
```
