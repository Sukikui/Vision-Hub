#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
DEPLOY_DIR="$(dirname "${SCRIPT_DIR}")"
DEFAULT_ENV_FILE="${DEPLOY_DIR}/vision-hub-field.env"
ENV_FILE="${ENV_FILE:-${DEFAULT_ENV_FILE}}"
CONF_TEMPLATE="${SCRIPT_DIR}/vision-hub.conf.template"
CONF_TARGET="/etc/mosquitto/conf.d/vision-hub.conf"

# Step 1: load the shared field-network values.
#
# By default this reads:
#   deploy/vision-hub-field.env
#
# The user normally edits that env file, not this script or the template.
# A caller can pass ENV_FILE=/path/to/custom.env to render the same template
# with another MQTT listener or port.
if [ -f "${ENV_FILE}" ]; then
    # shellcheck disable=SC1090
    . "${ENV_FILE}"
fi

# Step 2: fail early if required values were not provided by the env file.
MQTT_LISTENER_ADDRESS="${MQTT_LISTENER_ADDRESS:?MQTT_LISTENER_ADDRESS is required}"
MQTT_PORT="${MQTT_PORT:?MQTT_PORT is required}"

# Step 3: this script installs files under /etc and restarts a system service.
if [ "$(id -u)" -ne 0 ]; then
    echo "error: run this script with sudo" >&2
    exit 1
fi

# Step 4: install Mosquitto if the host does not already provide it.
if ! command -v mosquitto >/dev/null 2>&1; then
    apt-get update
    apt-get install -y mosquitto mosquitto-clients
fi

# Step 5: render the template into a temporary concrete config file.
#
# This is the moment where:
#   deploy/mosquitto/vision-hub.conf.template
#     + deploy/vision-hub-field.env
#     -> a generated Mosquitto config
#
# The generated file is temporary first so we do not write half-rendered config
# into /etc if something fails.
TMP_CONF="$(mktemp)"
trap 'rm -f "${TMP_CONF}"' EXIT

sed \
    -e "s|<mqtt_port>|${MQTT_PORT}|g" \
    -e "s|<mqtt_listener_address>|${MQTT_LISTENER_ADDRESS}|g" \
    "${CONF_TEMPLATE}" > "${TMP_CONF}"

# Step 6: install the rendered config into Mosquitto's system config directory.
install -D -m 0644 "${TMP_CONF}" "${CONF_TARGET}"

# Step 7: validate the installed config before restarting the running service.
mosquitto -c "${CONF_TARGET}" -t

# Step 8: enable and restart Mosquitto so ESP32 nodes can connect to MQTT.
systemctl enable mosquitto
systemctl restart mosquitto

echo "Installed ${CONF_TARGET}"
echo "Mosquitto is configured for the Vision-Hub ESP32 field network."
