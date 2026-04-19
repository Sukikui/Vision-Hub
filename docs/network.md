# Network

Vision-Hub uses an isolated Ethernet field network between the Raspberry Pi and ESP32-P4 nodes.

The ESP32 firmware uses DHCP and derives its MQTT broker URI from the DHCP default gateway:

```text
DHCP default gateway = central unit IP = MQTT broker host
```

## Default Contract

| Item | Value |
| --- | --- |
| Raspberry Pi field interface | `eth0` |
| Raspberry Pi field address | `192.168.50.1/24` |
| DHCP gateway advertised to ESP32 | `192.168.50.1` |
| ESP32 DHCP range | `192.168.50.20` to `192.168.50.200` |
| MQTT listener | `0.0.0.0:1883` |
| ESP32 MQTT URI | `mqtt://192.168.50.1:1883` |

## Shared Configuration

`deploy/vision-hub-field.env` is the source of truth for the field network:

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

The Docker renderer reads this file by default.

## Raspberry Pi Interface

The field Ethernet interface must own the gateway address before DHCP starts:

```bash
sudo deploy/rpi/configure-field-interface.sh
```

The script creates or updates the NetworkManager connection `vision-hub-field`.

| Setting | Value |
| --- | --- |
| interface | `FIELD_INTERFACE` |
| IPv4 method | manual |
| IPv4 address | `FIELD_ADDRESS` |
| default route | disabled with `ipv4.never-default yes` |
| IPv6 | disabled |
| autoconnect | enabled |

The field interface does not become the Raspberry Pi internet route.

## Verification

```bash
ip addr show eth0
nmcli connection show vision-hub-field
```

The ESP32 should receive a `192.168.50.x` lease and report gateway `192.168.50.1`.
