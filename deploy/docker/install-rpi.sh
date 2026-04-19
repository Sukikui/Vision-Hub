#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
DEPLOY_DIR="$(dirname "${SCRIPT_DIR}")"
PROJECT_DIR="$(dirname "${DEPLOY_DIR}")"
SERVICE_TEMPLATE="${SCRIPT_DIR}/templates/vision-hub-stack.service.template"
SERVICE_TARGET="/etc/systemd/system/vision-hub-stack.service"
RENDER_SERVICE_ONLY=false

# This script installs the Docker Compose stack as a boot-time systemd service.
# It assumes Docker Engine and the Docker Compose plugin are already installed
# on the Raspberry Pi.

usage() {
    echo "usage: $0 [--render-service-only]" >&2
}

case "${1:-}" in
    "")
        ;;
    "--render-service-only")
        RENDER_SERVICE_ONLY=true
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

resolve_docker_bin() {
    # Allow tests or unusual installations to force a Docker binary path.
    # Otherwise prefer the binary visible on PATH and fall back to the standard
    # Raspberry Pi/Linux location used inside the generated systemd unit.
    if [ -n "${DOCKER_BIN:-}" ]; then
        echo "${DOCKER_BIN}"
        return
    fi

    if command -v docker >/dev/null 2>&1; then
        command -v docker
        return
    fi

    echo "/usr/bin/docker"
}

render_service() {
    # Convert the systemd template into a concrete unit for this checkout.
    # PROJECT_DIR is embedded so systemd can run Compose from the repository
    # root even when started at boot without an interactive shell.
    docker_bin="$1"

    sed \
        -e "s|<project_dir>|${PROJECT_DIR}|g" \
        -e "s|<docker_bin>|${docker_bin}|g" \
        "${SERVICE_TEMPLATE}"
}

DOCKER_BIN_RESOLVED="$(resolve_docker_bin)"

# Step 1: optionally render the systemd unit to stdout and exit.
#
# This mode is used by tests and by operators who want to inspect the generated
# unit without writing to /etc/systemd/system.
if [ "${RENDER_SERVICE_ONLY}" = true ]; then
    render_service "${DOCKER_BIN_RESOLVED}"
    exit 0
fi

# Step 2: require root for the real install path.
#
# The installer writes to /etc/systemd/system and controls a system service.
if [ "$(id -u)" -ne 0 ]; then
    echo "error: run this script with sudo" >&2
    exit 1
fi

# Step 3: make sure the script is being run from a complete Vision-Hub checkout.
#
# The generated systemd unit uses this repository root as its Compose working
# directory, so compose.yaml must exist before installation.
if [ ! -f "${PROJECT_DIR}/compose.yaml" ]; then
    echo "error: missing ${PROJECT_DIR}/compose.yaml" >&2
    exit 1
fi

# Step 4: verify Docker runtime prerequisites.
#
# Docker Engine and the Compose plugin are explicit prerequisites. This script
# checks for them but does not install them.
if ! command -v docker >/dev/null 2>&1; then
    echo "error: docker is required; install Docker Engine before running this script" >&2
    exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
    echo "error: docker compose plugin is required" >&2
    exit 1
fi

# Step 5: render Docker-mounted service configs before enabling the stack.
#
# The systemd unit also renders them on each start, so changes in
# deploy/vision-hub-field.env are picked up after reboot or service restart.
"${SCRIPT_DIR}/render-configs.sh"

# Step 6: render the systemd unit to a temporary file, then install it atomically
# with the expected root-owned permissions.
TMP_SERVICE="$(mktemp)"
trap 'rm -f "${TMP_SERVICE}"' EXIT

render_service "${DOCKER_BIN_RESOLVED}" > "${TMP_SERVICE}"
install -D -m 0644 "${TMP_SERVICE}" "${SERVICE_TARGET}"

# Step 7: reload systemd and enable the stack immediately and at boot.
systemctl daemon-reload
systemctl enable --now vision-hub-stack.service

echo "Installed ${SERVICE_TARGET}"
echo "Vision-Hub Docker stack is enabled at boot."
