# Deployment

## Overview

Vision-Hub runs on a Raspberry Pi that acts as the central unit for an isolated ESP32 Ethernet field network.

The Raspberry Pi provides three local responsibilities:

| Responsibility | Implementation |
| --- | --- |
| stable field address | NetworkManager profile on the field Ethernet interface |
| DHCP for ESP32 nodes | dnsmasq |
| local MQTT broker | Mosquitto |

The Python service does not implement DHCP or broker behavior. It connects to Mosquitto as an MQTT client.

## Network Contract

The ESP32 firmware uses DHCP and derives the MQTT broker URI from the DHCP default gateway.

Firmware-side behavior:

```text
DHCP default gateway = central unit IP = MQTT broker host
```

Default Vision-Hub field network:

| Item | Value |
| --- | --- |
| Raspberry Pi field interface | `eth0` |
| Raspberry Pi field address | `192.168.50.1/24` |
| DHCP gateway advertised to ESP32 | `192.168.50.1` |
| ESP32 DHCP range | `192.168.50.20` to `192.168.50.200` |
| MQTT listener | `0.0.0.0:1883` |
| ESP32 MQTT URI | `mqtt://192.168.50.1:1883` |

## Deployment Files

| Path | Purpose |
| --- | --- |
| `deploy/vision-hub-field.env` | shared field network values |
| `deploy/rpi/configure-field-interface.sh` | configures the Raspberry Pi Ethernet address |
| `deploy/dnsmasq/vision-hub.conf.template` | dnsmasq DHCP config template |
| `deploy/dnsmasq/install-rpi.sh` | renders, installs, validates, and restarts dnsmasq |
| `deploy/mosquitto/vision-hub.conf.template` | Mosquitto config template |
| `deploy/mosquitto/install-rpi.sh` | renders, installs, validates, and restarts Mosquitto |

## Shared Configuration

`deploy/vision-hub-field.env` is the single source of truth for field network values:

```env
FIELD_INTERFACE=eth0
FIELD_ADDRESS=192.168.50.1/24
FIELD_GATEWAY=192.168.50.1
FIELD_DHCP_RANGE_START=192.168.50.20
FIELD_DHCP_RANGE_END=192.168.50.200
FIELD_DHCP_NETMASK=255.255.255.0
FIELD_DHCP_LEASE_TIME=24h
MQTT_LISTENER_ADDRESS=0.0.0.0
MQTT_PORT=1883
```

The install scripts read this file by default.

To use another network contract, provide another env file:

```bash
sudo ENV_FILE=/path/to/custom.env deploy/rpi/configure-field-interface.sh
sudo ENV_FILE=/path/to/custom.env deploy/dnsmasq/install-rpi.sh
sudo ENV_FILE=/path/to/custom.env deploy/mosquitto/install-rpi.sh
```

## Raspberry Pi Field Interface

The field Ethernet interface must own the gateway address before DHCP starts.

Default command:

```bash
sudo deploy/rpi/configure-field-interface.sh
```

The script creates or updates a NetworkManager connection named `vision-hub-field`.

Effective NetworkManager settings:

| Setting | Value |
| --- | --- |
| interface | `FIELD_INTERFACE` |
| IPv4 method | manual |
| IPv4 address | `FIELD_ADDRESS` |
| default route | disabled with `ipv4.never-default yes` |
| IPv6 | disabled |
| autoconnect | enabled |

The field interface does not become the Raspberry Pi internet route.

## DHCP

dnsmasq gives ESP32 nodes their field IP addresses and advertises the Raspberry Pi as the default gateway.

Install and activate:

```bash
sudo deploy/dnsmasq/install-rpi.sh
```

Generation flow:

```text
deploy/dnsmasq/vision-hub.conf.template
  + deploy/vision-hub-field.env
  -> /etc/dnsmasq.d/vision-hub.conf
```

Rendered dnsmasq behavior:

| Setting | Value source |
| --- | --- |
| interface | `FIELD_INTERFACE` |
| DHCP range | `FIELD_DHCP_RANGE_START`, `FIELD_DHCP_RANGE_END`, `FIELD_DHCP_NETMASK`, `FIELD_DHCP_LEASE_TIME` |
| router option | `FIELD_GATEWAY` |
| DNS service | disabled with `port=0` |

The script validates that `FIELD_GATEWAY` matches the IP part of `FIELD_ADDRESS`. This keeps the ESP32 broker discovery contract coherent.

## MQTT Broker

Mosquitto receives MQTT messages from ESP32 nodes and from the Vision-Hub Python service.

Install and activate:

```bash
sudo deploy/mosquitto/install-rpi.sh
```

Generation flow:

```text
deploy/mosquitto/vision-hub.conf.template
  + deploy/vision-hub-field.env
  -> /etc/mosquitto/conf.d/vision-hub.conf
```

Rendered Mosquitto behavior:

| Setting | Value |
| --- | --- |
| listener | `MQTT_LISTENER_ADDRESS:MQTT_PORT` |
| anonymous clients | allowed |
| persistence | enabled |
| persistence path | `/var/lib/mosquitto/` |
| logs | syslog |

With the default config, ESP32 nodes connect to:

```text
mqtt://192.168.50.1:1883
```

Vision-Hub runs on the same Raspberry Pi and can connect to:

```text
127.0.0.1:1883
```

## Installation Order

Run the host-level network pieces in this order:

```bash
sudo deploy/rpi/configure-field-interface.sh
sudo deploy/dnsmasq/install-rpi.sh
sudo deploy/mosquitto/install-rpi.sh
```

Then start the Python service:

```bash
uv run python main.py
```

## Verification

Check the field interface:

```bash
ip addr show eth0
nmcli connection show vision-hub-field
```

Check DHCP:

```bash
sudo systemctl status dnsmasq
journalctl -u dnsmasq -n 100 --no-pager
```

Check MQTT:

```bash
sudo systemctl status mosquitto
mosquitto_sub -h 127.0.0.1 -t 'vision/#' -v
```

Expected ESP32 logs:

```text
ETHIP:192.168.50.xx
ETHGW:192.168.50.1
using DHCP gateway as MQTT broker: mqtt://192.168.50.1:1883
```

## Container Compatibility

The deployment layout separates values, templates, and host installers:

```text
deploy/vision-hub-field.env
deploy/dnsmasq/vision-hub.conf.template
deploy/mosquitto/vision-hub.conf.template
```

This keeps the network contract independent from the runtime mode.

For host deployment, the scripts render templates into `/etc/...`.

For container deployment, the same templates can be rendered and mounted into service containers. DHCP requires broadcast traffic and low UDP ports, so dnsmasq containerization requires host networking or an equivalent explicit network setup. Mosquitto can run on the host or in a container as long as `MQTT_PORT` remains reachable from the ESP32 field network.
