#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
DEPLOY_DIR="$(dirname "${SCRIPT_DIR}")"
DEFAULT_ENV_FILE="${DEPLOY_DIR}/vision-hub-field.env"
ENV_FILE="${ENV_FILE:-${DEFAULT_ENV_FILE}}"
CONF_TEMPLATE="${SCRIPT_DIR}/vision-hub.conf.template"
CONF_TARGET="/etc/dnsmasq.d/vision-hub.conf"
RENDER_ONLY=false

usage() {
    echo "usage: $0 [--render-only]" >&2
}

case "${1:-}" in
    "")
        ;;
    "--render-only")
        RENDER_ONLY=true
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

render_config() {
    output_path="$1"

    if [ "${output_path}" = "-" ]; then
        sed \
            -e "s|<field_interface>|${FIELD_INTERFACE}|g" \
            -e "s|<field_address>|${FIELD_ADDRESS}|g" \
            -e "s|<field_gateway>|${FIELD_GATEWAY}|g" \
            -e "s|<dhcp_start>|${FIELD_DHCP_RANGE_START}|g" \
            -e "s|<dhcp_end>|${FIELD_DHCP_RANGE_END}|g" \
            -e "s|<dhcp_netmask>|${FIELD_DHCP_NETMASK}|g" \
            -e "s|<dhcp_lease>|${FIELD_DHCP_LEASE_TIME}|g" \
            -e "s|<mqtt_port>|${MQTT_PORT}|g" \
            "${CONF_TEMPLATE}"
        return
    fi

    sed \
        -e "s|<field_interface>|${FIELD_INTERFACE}|g" \
        -e "s|<field_address>|${FIELD_ADDRESS}|g" \
        -e "s|<field_gateway>|${FIELD_GATEWAY}|g" \
        -e "s|<dhcp_start>|${FIELD_DHCP_RANGE_START}|g" \
        -e "s|<dhcp_end>|${FIELD_DHCP_RANGE_END}|g" \
        -e "s|<dhcp_netmask>|${FIELD_DHCP_NETMASK}|g" \
        -e "s|<dhcp_lease>|${FIELD_DHCP_LEASE_TIME}|g" \
        -e "s|<mqtt_port>|${MQTT_PORT}|g" \
        "${CONF_TEMPLATE}" > "${output_path}"
}

# Step 1: load the shared field-network values.
#
# By default this reads:
#   deploy/vision-hub-field.env
#
# The user normally edits that env file, not this script or the template.
# A caller can pass ENV_FILE=/path/to/custom.env to render the same template for
# another interface or subnet.
if [ -f "${ENV_FILE}" ]; then
    # shellcheck disable=SC1090
    . "${ENV_FILE}"
fi

# Step 2: fail early if required values were not provided by the env file.
FIELD_INTERFACE="${FIELD_INTERFACE:?FIELD_INTERFACE is required}"
FIELD_ADDRESS="${FIELD_ADDRESS:?FIELD_ADDRESS is required}"
FIELD_GATEWAY="${FIELD_GATEWAY:?FIELD_GATEWAY is required}"
FIELD_DHCP_RANGE_START="${FIELD_DHCP_RANGE_START:?FIELD_DHCP_RANGE_START is required}"
FIELD_DHCP_RANGE_END="${FIELD_DHCP_RANGE_END:?FIELD_DHCP_RANGE_END is required}"
FIELD_DHCP_NETMASK="${FIELD_DHCP_NETMASK:?FIELD_DHCP_NETMASK is required}"
FIELD_DHCP_LEASE_TIME="${FIELD_DHCP_LEASE_TIME:?FIELD_DHCP_LEASE_TIME is required}"
MQTT_PORT="${MQTT_PORT:?MQTT_PORT is required}"

# Step 3: enforce the firmware contract.
#
# The ESP32 firmware uses the DHCP router/gateway option as the MQTT broker IP.
# Therefore the gateway advertised by dnsmasq must be the Raspberry Pi address
# configured by deploy/rpi/configure-field-interface.sh.
FIELD_ADDRESS_IP="${FIELD_ADDRESS%%/*}"
if [ "${FIELD_ADDRESS_IP}" != "${FIELD_GATEWAY}" ]; then
    echo "error: FIELD_GATEWAY must match the IP part of FIELD_ADDRESS" >&2
    echo "FIELD_ADDRESS=${FIELD_ADDRESS}" >&2
    echo "FIELD_GATEWAY=${FIELD_GATEWAY}" >&2
    exit 1
fi

# Step 4: optionally render the final config to stdout and stop.
#
# This mode is used by tests and by humans who want to inspect the generated
# config without installing anything into /etc or restarting dnsmasq.
if [ "${RENDER_ONLY}" = true ]; then
    render_config -
    exit 0
fi

# Step 5: this script installs files under /etc and restarts a system service.
if [ "$(id -u)" -ne 0 ]; then
    echo "error: run this script with sudo" >&2
    exit 1
fi

# Step 6: install dnsmasq if the host does not already provide it.
if ! command -v dnsmasq >/dev/null 2>&1; then
    apt-get update
    apt-get install -y dnsmasq
fi

# Step 7: render the template into a temporary concrete config file.
#
# This is the moment where:
#   deploy/dnsmasq/vision-hub.conf.template
#     + deploy/vision-hub-field.env
#     -> a generated dnsmasq config
#
# The generated file is temporary first so we do not write half-rendered config
# into /etc if something fails.
TMP_CONF="$(mktemp)"
trap 'rm -f "${TMP_CONF}"' EXIT

render_config "${TMP_CONF}"

# Step 8: install the rendered config into dnsmasq's system config directory.
install -D -m 0644 "${TMP_CONF}" "${CONF_TARGET}"

# Step 9: validate the installed config before restarting the running service.
dnsmasq --test --conf-file="${CONF_TARGET}"

# Step 10: enable and restart dnsmasq so ESP32 nodes can receive leases.
systemctl enable dnsmasq
systemctl restart dnsmasq

echo "Installed ${CONF_TARGET}"
echo "dnsmasq is configured for the Vision-Hub ESP32 field network."
