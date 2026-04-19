#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
DEPLOY_DIR="$(dirname "${SCRIPT_DIR}")"
DEFAULT_ENV_FILE="${DEPLOY_DIR}/vision-hub-network.env"
ENV_FILE="${ENV_FILE:-${DEFAULT_ENV_FILE}}"
GENERATED_DIR="${GENERATED_DIR:-${SCRIPT_DIR}/generated}"
FIELD_DNSMASQ_TEMPLATE="${SCRIPT_DIR}/templates/dnsmasq-field.conf.template"
ADMIN_DNSMASQ_TEMPLATE="${SCRIPT_DIR}/templates/dnsmasq-admin.conf.template"
MOSQUITTO_TEMPLATE="${SCRIPT_DIR}/templates/mosquitto.conf.template"
FIELD_DNSMASQ_TARGET="${GENERATED_DIR}/dnsmasq-field/vision-hub.conf"
ADMIN_DNSMASQ_TARGET="${GENERATED_DIR}/dnsmasq-admin/vision-hub.conf"
MOSQUITTO_TARGET="${GENERATED_DIR}/mosquitto/vision-hub.conf"

# This script does not install anything. It turns the shared network env file
# into concrete config files mounted by Docker Compose.
#
# Defaults:
#   env source: deploy/vision-hub-network.env
#   output dir: deploy/docker/generated/
#
# Tests and operators can override those paths with:
#   ENV_FILE=/path/to/custom.env
#   GENERATED_DIR=/path/to/output

usage() {
    echo "usage: $0" >&2
}

case "${1:-}" in
    "")
        ;;
    "-h" | "--help")
        usage
        exit 0
        ;;
    *)
        usage
        exit 2
        ;;
esac

render_field_dnsmasq_config() {
    # Render DHCP config for the ESP32 field LAN. dnsmasq advertises
    # FIELD_GATEWAY as the DHCP router option, and the ESP32 firmware uses that
    # gateway IP as the MQTT broker host.
    sed \
        -e "s|<field_interface>|${FIELD_INTERFACE}|g" \
        -e "s|<field_address>|${FIELD_ADDRESS}|g" \
        -e "s|<field_gateway>|${FIELD_GATEWAY}|g" \
        -e "s|<dhcp_start>|${FIELD_DHCP_RANGE_START}|g" \
        -e "s|<dhcp_end>|${FIELD_DHCP_RANGE_END}|g" \
        -e "s|<dhcp_netmask>|${FIELD_DHCP_NETMASK}|g" \
        -e "s|<dhcp_lease>|${FIELD_DHCP_LEASE_TIME}|g" \
        -e "s|<mqtt_port>|${MQTT_PORT}|g" \
        "${FIELD_DNSMASQ_TEMPLATE}"
}

render_admin_dnsmasq_config() {
    # Render DHCP config for the local admin Wi-Fi. This DHCP service does not
    # advertise a router option, so a laptop can use the AP to reach the RPi
    # without making it the default internet gateway.
    sed \
        -e "s|<admin_interface>|${ADMIN_INTERFACE}|g" \
        -e "s|<admin_address>|${ADMIN_ADDRESS}|g" \
        -e "s|<admin_address_ip>|${ADMIN_ADDRESS_IP}|g" \
        -e "s|<admin_start>|${ADMIN_DHCP_RANGE_START}|g" \
        -e "s|<admin_end>|${ADMIN_DHCP_RANGE_END}|g" \
        -e "s|<admin_netmask>|${ADMIN_DHCP_NETMASK}|g" \
        -e "s|<admin_lease>|${ADMIN_DHCP_LEASE_TIME}|g" \
        "${ADMIN_DNSMASQ_TEMPLATE}"
}

render_mosquitto_config() {
    # Render MQTT broker config for the Docker Mosquitto container. The Docker
    # defaults below intentionally use container paths and stdout logging.
    sed \
        -e "s|<mqtt_port>|${MQTT_PORT}|g" \
        -e "s|<mqtt_listener_address>|${MQTT_LISTENER_ADDRESS}|g" \
        -e "s|<mqtt_persistence_location>|${MQTT_PERSISTENCE_LOCATION}|g" \
        -e "s|<mqtt_log_dest>|${MQTT_LOG_DEST}|g" \
        "${MOSQUITTO_TEMPLATE}"
}

validate_no_placeholders() {
    # Failing here catches missing env values or stale template placeholders
    # before Docker starts containers with invalid service configs.
    config_path="$1"

    if grep -q '<[^>]*>' "${config_path}"; then
        echo "error: unresolved placeholder in ${config_path}" >&2
        grep '<[^>]*>' "${config_path}" >&2
        exit 1
    fi
}

# Step 1: load the shared network values used by the Raspberry Pi interface
# script and the Docker-rendered service configs.
if [ -f "${ENV_FILE}" ]; then
    # shellcheck disable=SC1090
    . "${ENV_FILE}"
fi

# Step 2: validate the values needed by the generated service configs.
#
# Bash parameter expansion with `:?` makes the script fail immediately with a
# readable error if a required key is missing from ENV_FILE.
FIELD_INTERFACE="${FIELD_INTERFACE:?FIELD_INTERFACE is required}"
FIELD_ADDRESS="${FIELD_ADDRESS:?FIELD_ADDRESS is required}"
FIELD_GATEWAY="${FIELD_GATEWAY:?FIELD_GATEWAY is required}"
FIELD_DHCP_RANGE_START="${FIELD_DHCP_RANGE_START:?FIELD_DHCP_RANGE_START is required}"
FIELD_DHCP_RANGE_END="${FIELD_DHCP_RANGE_END:?FIELD_DHCP_RANGE_END is required}"
FIELD_DHCP_NETMASK="${FIELD_DHCP_NETMASK:?FIELD_DHCP_NETMASK is required}"
FIELD_DHCP_LEASE_TIME="${FIELD_DHCP_LEASE_TIME:?FIELD_DHCP_LEASE_TIME is required}"
ADMIN_INTERFACE="${ADMIN_INTERFACE:?ADMIN_INTERFACE is required}"
ADMIN_ADDRESS="${ADMIN_ADDRESS:?ADMIN_ADDRESS is required}"
ADMIN_DHCP_RANGE_START="${ADMIN_DHCP_RANGE_START:?ADMIN_DHCP_RANGE_START is required}"
ADMIN_DHCP_RANGE_END="${ADMIN_DHCP_RANGE_END:?ADMIN_DHCP_RANGE_END is required}"
ADMIN_DHCP_NETMASK="${ADMIN_DHCP_NETMASK:?ADMIN_DHCP_NETMASK is required}"
ADMIN_DHCP_LEASE_TIME="${ADMIN_DHCP_LEASE_TIME:?ADMIN_DHCP_LEASE_TIME is required}"
MQTT_LISTENER_ADDRESS="${MQTT_LISTENER_ADDRESS:?MQTT_LISTENER_ADDRESS is required}"
MQTT_PORT="${MQTT_PORT:?MQTT_PORT is required}"

# Docker Mosquitto stores persistence in its container data directory and logs
# to stdout so Docker can collect service logs.
MQTT_PERSISTENCE_LOCATION="${MQTT_PERSISTENCE_LOCATION:-/mosquitto/data/}"
MQTT_LOG_DEST="${MQTT_LOG_DEST:-stdout}"

# Step 3: enforce the ESP32 firmware contract. The DHCP router option is also
# the MQTT broker host used by the firmware.
FIELD_ADDRESS_IP="${FIELD_ADDRESS%%/*}"
if [ "${FIELD_ADDRESS_IP}" != "${FIELD_GATEWAY}" ]; then
    echo "error: FIELD_GATEWAY must match the IP part of FIELD_ADDRESS" >&2
    echo "FIELD_ADDRESS=${FIELD_ADDRESS}" >&2
    echo "FIELD_GATEWAY=${FIELD_GATEWAY}" >&2
    exit 1
fi

if [ "${FIELD_INTERFACE}" = "${ADMIN_INTERFACE}" ]; then
    echo "error: FIELD_INTERFACE and ADMIN_INTERFACE must be different" >&2
    exit 1
fi

ADMIN_ADDRESS_IP="${ADMIN_ADDRESS%%/*}"
if [ "${FIELD_ADDRESS_IP}" = "${ADMIN_ADDRESS_IP}" ]; then
    echo "error: FIELD_ADDRESS and ADMIN_ADDRESS must use different interface IPs" >&2
    exit 1
fi

# Step 4: render concrete Docker-mounted config files.
#
# The generated directory is ignored by Git because these files are derived
# artifacts. Compose mounts them read-only into the service containers.
mkdir -p \
    "$(dirname "${FIELD_DNSMASQ_TARGET}")" \
    "$(dirname "${ADMIN_DNSMASQ_TARGET}")" \
    "$(dirname "${MOSQUITTO_TARGET}")"

render_field_dnsmasq_config > "${FIELD_DNSMASQ_TARGET}"
render_admin_dnsmasq_config > "${ADMIN_DNSMASQ_TARGET}"
render_mosquitto_config > "${MOSQUITTO_TARGET}"

# Step 5: validate the rendered files before reporting success.
validate_no_placeholders "${FIELD_DNSMASQ_TARGET}"
validate_no_placeholders "${ADMIN_DNSMASQ_TARGET}"
validate_no_placeholders "${MOSQUITTO_TARGET}"

echo "Rendered Docker configs:"
echo "- ${FIELD_DNSMASQ_TARGET}"
echo "- ${ADMIN_DNSMASQ_TARGET}"
echo "- ${MOSQUITTO_TARGET}"
