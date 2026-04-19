#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
DEPLOY_DIR="$(dirname "${SCRIPT_DIR}")"
DEFAULT_ENV_FILE="${DEPLOY_DIR}/vision-hub-field.env"
ENV_FILE="${ENV_FILE:-${DEFAULT_ENV_FILE}}"

# Step 1: load the shared field-network values.
#
# By default this reads:
#   deploy/vision-hub-field.env
#
# That file is the single source of truth for the field interface and subnet.
# A caller can pass ENV_FILE=/path/to/custom.env to reuse this script with
# another interface or subnet without editing the script.
if [ -f "${ENV_FILE}" ]; then
    # shellcheck disable=SC1090
    . "${ENV_FILE}"
fi

# Step 2: fail early if required values were not provided by the env file.
FIELD_INTERFACE="${FIELD_INTERFACE:?FIELD_INTERFACE is required}"
FIELD_ADDRESS="${FIELD_ADDRESS:?FIELD_ADDRESS is required}"
CONNECTION_NAME="${CONNECTION_NAME:-vision-hub-field}"

# Step 3: this script changes host network settings, so it must run as root.
if [ "$(id -u)" -ne 0 ]; then
    echo "error: run this script with sudo" >&2
    exit 1
fi

# Step 4: Raspberry Pi OS Lite is expected to use NetworkManager here.
# `nmcli` is the tool that creates/updates the persistent Ethernet profile.
if ! command -v nmcli >/dev/null 2>&1; then
    echo "error: nmcli is required; install or enable NetworkManager first" >&2
    exit 1
fi

# Step 5: refuse to create a profile for an interface that does not exist.
if ! ip link show "${FIELD_INTERFACE}" >/dev/null 2>&1; then
    echo "error: network interface ${FIELD_INTERFACE} does not exist" >&2
    exit 1
fi

# Step 6: create or update one stable NetworkManager profile for the field LAN.
#
# Re-running this script updates the existing profile instead of creating
# duplicates. The address comes from FIELD_ADDRESS in the shared env file.
if nmcli --terse --fields NAME connection show | grep -Fxq "${CONNECTION_NAME}"; then
    nmcli connection modify "${CONNECTION_NAME}" \
        connection.interface-name "${FIELD_INTERFACE}" \
        ipv4.method manual \
        ipv4.addresses "${FIELD_ADDRESS}" \
        ipv4.never-default yes \
        ipv6.method disabled \
        connection.autoconnect yes
else
    nmcli connection add type ethernet \
        ifname "${FIELD_INTERFACE}" \
        con-name "${CONNECTION_NAME}" \
        ipv4.method manual \
        ipv4.addresses "${FIELD_ADDRESS}" \
        ipv4.never-default yes \
        ipv6.method disabled \
        connection.autoconnect yes
fi

# Step 7: bring the profile up now.
#
# dnsmasq must start after this, because it binds to FIELD_INTERFACE and
# advertises FIELD_GATEWAY, which should be the IP part of FIELD_ADDRESS.
nmcli connection up "${CONNECTION_NAME}"

echo "Configured ${FIELD_INTERFACE} as ${FIELD_ADDRESS} using NetworkManager connection ${CONNECTION_NAME}."
