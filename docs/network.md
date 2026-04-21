# Network

Vision-Hub uses two local networks on the Raspberry Pi:

| Network | Interface | Purpose |
| --- | --- | --- |
| field Ethernet LAN | `eth0` | ESP32-P4 nodes, PoE switch, MQTT traffic |
| admin Wi-Fi AP | `wlan0` | temporary local access from a laptop or phone |

The two networks are intentionally separate. The admin Wi-Fi is not a bridge to the ESP32 field LAN and does not provide internet routing.

## Field Contract

The ESP32 firmware uses DHCP on Ethernet and derives its MQTT broker URI from the DHCP default gateway:

```text
DHCP default gateway = central unit IP = MQTT broker host
```

## Default Field Contract

| Item | Value |
| --- | --- |
| Raspberry Pi field interface | `eth0` |
| Raspberry Pi field address | `192.168.50.1/24` |
| DHCP gateway advertised to ESP32 | `192.168.50.1` |
| ESP32 DHCP range | `192.168.50.20` to `192.168.50.200` |
| MQTT listener | `0.0.0.0:1883` |
| ESP32 MQTT URI | `mqtt://192.168.50.1:1883` |

The field DHCP service must advertise router option `192.168.50.1`, because the firmware uses that value as the broker host.

## Admin Wi-Fi Contract

| Item | Value |
| --- | --- |
| Raspberry Pi admin interface | `wlan0` |
| Admin Wi-Fi SSID | `VisionHub-Admin` |
| Raspberry Pi admin address | `192.168.60.1/24` |
| Admin DHCP range | `192.168.60.20` to `192.168.60.100` |
| Admin DNS name | `vision-hub.lan` |
| Home Assistant UI | `http://vision-hub.lan:8123` |

The admin DHCP service does not advertise a router option. A connected laptop reaches `192.168.60.1` directly on-link, without making the Raspberry Pi its default internet gateway.

`dnsmasq-admin` also provides local DNS on the admin Wi-Fi only:

```text
vision-hub.lan -> 192.168.60.1
```

The DNS service does not forward internet DNS. It only gives admin clients a stable local name for Raspberry Pi services. Home Assistant still uses its default port, so the URL includes `:8123`.

## Shared Configuration

`deploy/vision-hub-network.env` is the source of truth for local network values:

```env
FIELD_INTERFACE=eth0
FIELD_ADDRESS=192.168.50.1/24
FIELD_GATEWAY=192.168.50.1
FIELD_DHCP_RANGE_START=192.168.50.20
FIELD_DHCP_RANGE_END=192.168.50.200
FIELD_DHCP_NETMASK=255.255.255.0
FIELD_DHCP_LEASE_TIME=24h

ADMIN_INTERFACE=wlan0
ADMIN_ADDRESS=192.168.60.1/24
ADMIN_DHCP_RANGE_START=192.168.60.20
ADMIN_DHCP_RANGE_END=192.168.60.100
ADMIN_DHCP_NETMASK=255.255.255.0
ADMIN_DHCP_LEASE_TIME=12h
ADMIN_WIFI_SSID=VisionHub-Admin
ADMIN_WIFI_PASSWORD=change-this-admin-password
ADMIN_WIFI_BAND=bg
ADMIN_WIFI_CHANNEL=6
ADMIN_DNS_NAME=vision-hub.lan

MQTT_LISTENER_ADDRESS=0.0.0.0
MQTT_PORT=1883
```

Change `ADMIN_WIFI_PASSWORD` before running the Raspberry Pi interface script. The committed value is a placeholder and the script refuses to deploy it.

The Docker renderer reads this file by default and generates:

| Generated config | Consumer |
| --- | --- |
| `deploy/docker/generated/dnsmasq-field/vision-hub.conf` | `dnsmasq-field` container |
| `deploy/docker/generated/dnsmasq-admin/vision-hub.conf` | `dnsmasq-admin` container |
| `deploy/docker/generated/mosquitto/vision-hub.conf` | `mosquitto` container |

## Raspberry Pi Interfaces

The Raspberry Pi interfaces must own their static addresses before Docker starts DHCP:

```bash
sudo deploy/rpi/configure-network-interfaces.sh
```

The script creates or updates two NetworkManager connections:

| Connection | Interface | Type |
| --- | --- | --- |
| `vision-hub-field` | `FIELD_INTERFACE` | Ethernet, manual IPv4 |
| `vision-hub-admin` | `ADMIN_INTERFACE` | Wi-Fi access point, manual IPv4 |

| Setting | Value |
| --- | --- |
| field interface | `FIELD_INTERFACE` |
| field address | `FIELD_ADDRESS` |
| admin interface | `ADMIN_INTERFACE` |
| admin address | `ADMIN_ADDRESS` |
| admin SSID | `ADMIN_WIFI_SSID` |
| IPv4 method | manual |
| default route | disabled with `ipv4.never-default yes` |
| IPv6 | disabled |
| autoconnect | enabled |

The admin Wi-Fi access point is created by NetworkManager. DHCP for clients on that Wi-Fi is still handled by the `dnsmasq-admin` Docker service, not by NetworkManager's `shared` mode.

## Verification

```bash
ip addr show eth0
ip addr show wlan0
nmcli connection show vision-hub-field
nmcli connection show vision-hub-admin
```

The ESP32 should receive a `192.168.50.x` lease and report gateway `192.168.50.1`.

A laptop connected to `VisionHub-Admin` should receive a `192.168.60.x` lease and should be able to reach the Raspberry Pi at `192.168.60.1`.
