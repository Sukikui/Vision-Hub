#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
DEPLOY_DIR="$(dirname "${SCRIPT_DIR}")"
DEFAULT_ENV_FILE="${DEPLOY_DIR}/vision-hub-network.env"
ENV_FILE="${ENV_FILE:-${DEFAULT_ENV_FILE}}"
DEFAULT_ADMIN_WIFI_PASSWORD="change-this-admin-password"

# Step 1: load the shared network values.
#
# By default this reads:
#   deploy/vision-hub-network.env
#
# That file is the single source of truth for:
#   - the ESP32 field Ethernet network on FIELD_INTERFACE
#   - the local admin Wi-Fi network on ADMIN_INTERFACE
#
# A caller can pass ENV_FILE=/path/to/custom.env to reuse this script with
# another interface or subnet without editing the script.
if [ -f "${ENV_FILE}" ]; then
    # shellcheck disable=SC1090
    . "${ENV_FILE}"
fi

# Step 2: fail early if required values were not provided by the env file.
FIELD_INTERFACE="${FIELD_INTERFACE:?FIELD_INTERFACE is required}"
FIELD_ADDRESS="${FIELD_ADDRESS:?FIELD_ADDRESS is required}"
FIELD_CONNECTION_NAME="${FIELD_CONNECTION_NAME:-vision-hub-field}"

ADMIN_INTERFACE="${ADMIN_INTERFACE:?ADMIN_INTERFACE is required}"
ADMIN_ADDRESS="${ADMIN_ADDRESS:?ADMIN_ADDRESS is required}"
ADMIN_WIFI_SSID="${ADMIN_WIFI_SSID:?ADMIN_WIFI_SSID is required}"
ADMIN_WIFI_PASSWORD="${ADMIN_WIFI_PASSWORD:?ADMIN_WIFI_PASSWORD is required}"
ADMIN_WIFI_BAND="${ADMIN_WIFI_BAND:-bg}"
ADMIN_WIFI_CHANNEL="${ADMIN_WIFI_CHANNEL:-6}"
ADMIN_CONNECTION_NAME="${ADMIN_CONNECTION_NAME:-vision-hub-admin}"

# Step 3: prevent accidentally deploying the committed placeholder Wi-Fi key.
#
# NetworkManager requires an 8-63 character WPA-PSK. The placeholder satisfies
# the length rule for documentation and tests, but it must not be used in the
# field.
if [ "${ADMIN_WIFI_PASSWORD}" = "${DEFAULT_ADMIN_WIFI_PASSWORD}" ]; then
    echo "error: change ADMIN_WIFI_PASSWORD in ${ENV_FILE} before configuring the admin Wi-Fi" >&2
    exit 1
fi

if [ "${#ADMIN_WIFI_PASSWORD}" -lt 8 ] || [ "${#ADMIN_WIFI_PASSWORD}" -gt 63 ]; then
    echo "error: ADMIN_WIFI_PASSWORD must be 8 to 63 characters" >&2
    exit 1
fi

if [ "${FIELD_INTERFACE}" = "${ADMIN_INTERFACE}" ]; then
    echo "error: FIELD_INTERFACE and ADMIN_INTERFACE must be different" >&2
    exit 1
fi

# Step 4: this script changes host network settings, so it must run as root.
if [ "$(id -u)" -ne 0 ]; then
    echo "error: run this script with sudo" >&2
    exit 1
fi

# Step 5: Raspberry Pi OS Lite is expected to use NetworkManager here.
# `nmcli` creates persistent Ethernet and Wi-Fi access point profiles.
if ! command -v nmcli >/dev/null 2>&1; then
    echo "error: nmcli is required; install or enable NetworkManager first" >&2
    exit 1
fi

# Step 6: refuse to create profiles for interfaces that do not exist.
if ! ip link show "${FIELD_INTERFACE}" >/dev/null 2>&1; then
    echo "error: network interface ${FIELD_INTERFACE} does not exist" >&2
    exit 1
fi

if ! ip link show "${ADMIN_INTERFACE}" >/dev/null 2>&1; then
    echo "error: network interface ${ADMIN_INTERFACE} does not exist" >&2
    exit 1
fi

connection_exists() {
    nmcli --terse --fields NAME connection show | grep -Fxq "$1"
}

configure_field_interface() {
    # Create or update the Ethernet profile used by ESP32-P4 nodes.
    #
    # The address comes from FIELD_ADDRESS. `ipv4.never-default yes` prevents
    # the isolated field LAN from becoming the Raspberry Pi internet route.
    if connection_exists "${FIELD_CONNECTION_NAME}"; then
        nmcli connection modify "${FIELD_CONNECTION_NAME}" \
            connection.interface-name "${FIELD_INTERFACE}" \
            ipv4.method manual \
            ipv4.addresses "${FIELD_ADDRESS}" \
            ipv4.never-default yes \
            ipv6.method disabled \
            connection.autoconnect yes
    else
        nmcli connection add type ethernet \
            ifname "${FIELD_INTERFACE}" \
            con-name "${FIELD_CONNECTION_NAME}" \
            ipv4.method manual \
            ipv4.addresses "${FIELD_ADDRESS}" \
            ipv4.never-default yes \
            ipv6.method disabled \
            connection.autoconnect yes
    fi
}

configure_admin_access_point() {
    # Create or update the Wi-Fi access point used for local administration.
    #
    # NetworkManager creates the AP radio mode. The profile intentionally uses
    # `ipv4.method manual`, not `shared`, because DHCP is handled by the
    # dnsmasq-admin Docker service.
    if connection_exists "${ADMIN_CONNECTION_NAME}"; then
        nmcli connection modify "${ADMIN_CONNECTION_NAME}" \
            connection.interface-name "${ADMIN_INTERFACE}"
    else
        nmcli connection add type wifi \
            ifname "${ADMIN_INTERFACE}" \
            con-name "${ADMIN_CONNECTION_NAME}" \
            ssid "${ADMIN_WIFI_SSID}"
    fi

    nmcli connection modify "${ADMIN_CONNECTION_NAME}" \
        connection.autoconnect yes \
        802-11-wireless.mode ap \
        802-11-wireless.ssid "${ADMIN_WIFI_SSID}" \
        802-11-wireless.band "${ADMIN_WIFI_BAND}" \
        802-11-wireless.channel "${ADMIN_WIFI_CHANNEL}" \
        wifi-sec.key-mgmt wpa-psk \
        wifi-sec.psk "${ADMIN_WIFI_PASSWORD}" \
        ipv4.method manual \
        ipv4.addresses "${ADMIN_ADDRESS}" \
        ipv4.never-default yes \
        ipv6.method disabled
}

# Step 7: create or update both persistent NetworkManager profiles.
configure_field_interface
configure_admin_access_point

# Step 8: bring the profiles up now.
#
# Docker dnsmasq services must start after this because they bind to
# FIELD_INTERFACE and ADMIN_INTERFACE.
nmcli connection up "${FIELD_CONNECTION_NAME}"
nmcli connection up "${ADMIN_CONNECTION_NAME}"

echo "Configured field interface ${FIELD_INTERFACE} as ${FIELD_ADDRESS}."
echo "Configured admin Wi-Fi ${ADMIN_WIFI_SSID} on ${ADMIN_INTERFACE} as ${ADMIN_ADDRESS}."
