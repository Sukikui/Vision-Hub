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
        -> dnsmasq-field container
        -> dnsmasq-admin container
        -> mosquitto container
        -> vision-hub container
```

There is no separate `dnsmasq.service` or `mosquitto.service` in the Docker deployment. Those processes run inside containers managed by Docker Compose.

## Runtime Responsibilities

| Responsibility | Implementation |
| --- | --- |
| stable field address | NetworkManager profile on the field Ethernet interface |
| local admin Wi-Fi access point | NetworkManager Wi-Fi AP profile |
| DHCP for ESP32 nodes | `dnsmasq-field` container |
| DHCP for admin Wi-Fi clients | `dnsmasq-admin` container |
| local MQTT broker | Mosquitto container |
| hub application | Vision-Hub Python container |

## Deployment Files

| Path | Role |
| --- | --- |
| `deploy/vision-hub-network.env` | source of truth for field and admin network values |
| `deploy/rpi/configure-network-interfaces.sh` | creates or updates the Raspberry Pi Ethernet and Wi-Fi AP profiles |
| `deploy/docker/render-configs.sh` | renders Docker-mounted dnsmasq and Mosquitto configs |
| `deploy/docker/install-rpi.sh` | installs and enables the `vision-hub-stack.service` systemd unit |
| `compose.yaml` | defines the `dnsmasq-field`, `dnsmasq-admin`, `mosquitto`, and `vision-hub` containers |

The old non-Docker split is intentionally gone. dnsmasq and Mosquitto are not installed as independent host services; Compose starts them from `compose.yaml`.

The admin Wi-Fi radio mode is the only part that stays host-side: NetworkManager creates the access point on `wlan0`. DHCP for clients connected to that access point is still served by the `dnsmasq-admin` container.

## Installation Order

Set a real admin Wi-Fi password in `deploy/vision-hub-network.env`, then configure the Raspberry Pi network interfaces once:

```bash
sudo deploy/rpi/configure-network-interfaces.sh
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
docker compose logs dnsmasq-field
docker compose logs dnsmasq-admin
docker compose logs mosquitto
docker compose logs vision-hub
```

Expected ESP32 logs:

```text
ETHIP:192.168.50.xx
ETHGW:192.168.50.1
using DHCP gateway as MQTT broker: mqtt://192.168.50.1:1883
```

Expected admin client state:

```text
SSID: VisionHub-Admin
client IP: 192.168.60.x
RPi admin IP: 192.168.60.1
```
