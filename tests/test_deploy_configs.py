"""Tests for deployment config rendering scripts."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
RPI_NETWORK_SCRIPT = ROOT / "deploy" / "rpi" / "configure-network-interfaces.sh"
DOCKER_RENDER_SCRIPT = ROOT / "deploy" / "docker" / "render-configs.sh"
DOCKER_INSTALL_SCRIPT = ROOT / "deploy" / "docker" / "install-rpi.sh"
COMPOSE_FILE = ROOT / "compose.yaml"


class DeployConfigTest(unittest.TestCase):
    """Unit tests for Docker deployment scripts and rendered configs."""

    def test_deploy_scripts_are_valid_bash(self) -> None:
        """Validate deployment scripts with bash syntax checking."""

        for script in (
            RPI_NETWORK_SCRIPT,
            DOCKER_RENDER_SCRIPT,
            DOCKER_INSTALL_SCRIPT,
        ):
            with self.subTest(script=script):
                result = subprocess.run(
                    ["bash", "-n", str(script)],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )

                self.assertEqual(result.returncode, 0, result.stderr)

    def test_custom_env_changes_rendered_configs(self) -> None:
        """Render Docker service configs from a custom shared env file."""

        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / "custom.env"
            generated_dir = Path(temp_dir) / "generated"
            env_path.write_text(
                "\n".join(
                    (
                        "FIELD_INTERFACE=enp1s0",
                        "FIELD_ADDRESS=192.168.60.1/24",
                        "FIELD_GATEWAY=192.168.60.1",
                        "FIELD_DHCP_RANGE_START=192.168.60.20",
                        "FIELD_DHCP_RANGE_END=192.168.60.99",
                        "FIELD_DHCP_NETMASK=255.255.255.0",
                        "FIELD_DHCP_LEASE_TIME=12h",
                        "ADMIN_INTERFACE=wlp2s0",
                        "ADMIN_ADDRESS=192.168.70.1/24",
                        "ADMIN_DHCP_RANGE_START=192.168.70.20",
                        "ADMIN_DHCP_RANGE_END=192.168.70.80",
                        "ADMIN_DHCP_NETMASK=255.255.255.0",
                        "ADMIN_DHCP_LEASE_TIME=6h",
                        "ADMIN_DNS_NAME=hub.test",
                        "MQTT_LISTENER_ADDRESS=192.168.60.1",
                        "MQTT_PORT=1884",
                        "VISION_HUB_NODE_IDS=p4-999",
                    )
                ),
                encoding="utf-8",
            )

            result = _run_script(
                DOCKER_RENDER_SCRIPT,
                env_file=env_path,
                env={"GENERATED_DIR": str(generated_dir)},
            )
            field_dnsmasq_config = (generated_dir / "dnsmasq-field" / "vision-hub.conf").read_text(
                encoding="utf-8"
            )
            admin_dnsmasq_config = (generated_dir / "dnsmasq-admin" / "vision-hub.conf").read_text(
                encoding="utf-8"
            )
            mosquitto_config = (generated_dir / "mosquitto" / "vision-hub.conf").read_text(encoding="utf-8")
            homeassistant_dashboard = (generated_dir / "homeassistant" / "dashboards" / "vision-hub.yaml").read_text(
                encoding="utf-8"
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("interface=enp1s0", field_dnsmasq_config)
        self.assertIn("dhcp-range=192.168.60.20,192.168.60.99,255.255.255.0,12h", field_dnsmasq_config)
        self.assertIn("dhcp-option=option:router,192.168.60.1", field_dnsmasq_config)
        self.assertIn("mqtt://192.168.60.1:1884", field_dnsmasq_config)
        self.assertIn("interface=wlp2s0", admin_dnsmasq_config)
        self.assertIn("dhcp-range=192.168.70.20,192.168.70.80,255.255.255.0,6h", admin_dnsmasq_config)
        self.assertIn("listen-address=192.168.70.1", admin_dnsmasq_config)
        self.assertIn("address=/hub.test/192.168.70.1", admin_dnsmasq_config)
        self.assertIn("dhcp-option=option:dns-server,192.168.70.1", admin_dnsmasq_config)
        self.assertNotIn("dhcp-option=option:router", admin_dnsmasq_config)
        self.assertIn("listener 1884 192.168.60.1", mosquitto_config)
        self.assertIn("image.p4_999_latest_capture", homeassistant_dashboard)

    def test_docker_render_rejects_gateway_that_does_not_match_field_address(self) -> None:
        """Reject env files that break the ESP32 gateway-as-broker contract."""

        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / "bad.env"
            env_path.write_text(
                "\n".join(
                    (
                        "FIELD_INTERFACE=eth0",
                        "FIELD_ADDRESS=192.168.50.1/24",
                        "FIELD_GATEWAY=192.168.60.1",
                        "FIELD_DHCP_RANGE_START=192.168.50.20",
                        "FIELD_DHCP_RANGE_END=192.168.50.200",
                        "FIELD_DHCP_NETMASK=255.255.255.0",
                        "FIELD_DHCP_LEASE_TIME=24h",
                        "ADMIN_INTERFACE=wlan0",
                        "ADMIN_ADDRESS=192.168.60.1/24",
                        "ADMIN_DHCP_RANGE_START=192.168.60.20",
                        "ADMIN_DHCP_RANGE_END=192.168.60.100",
                        "ADMIN_DHCP_NETMASK=255.255.255.0",
                        "ADMIN_DHCP_LEASE_TIME=12h",
                        "ADMIN_DNS_NAME=vision-hub.lan",
                        "MQTT_LISTENER_ADDRESS=0.0.0.0",
                        "MQTT_PORT=1883",
                    )
                ),
                encoding="utf-8",
            )

            result = _run_script(DOCKER_RENDER_SCRIPT, env_file=env_path)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("FIELD_GATEWAY must match", result.stderr)

    def test_docker_render_rejects_reused_network_interface(self) -> None:
        """Reject configs that bind field and admin DHCP to the same interface."""

        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / "bad.env"
            env_path.write_text(
                "\n".join(
                    (
                        "FIELD_INTERFACE=eth0",
                        "FIELD_ADDRESS=192.168.50.1/24",
                        "FIELD_GATEWAY=192.168.50.1",
                        "FIELD_DHCP_RANGE_START=192.168.50.20",
                        "FIELD_DHCP_RANGE_END=192.168.50.200",
                        "FIELD_DHCP_NETMASK=255.255.255.0",
                        "FIELD_DHCP_LEASE_TIME=24h",
                        "ADMIN_INTERFACE=eth0",
                        "ADMIN_ADDRESS=192.168.60.1/24",
                        "ADMIN_DHCP_RANGE_START=192.168.60.20",
                        "ADMIN_DHCP_RANGE_END=192.168.60.100",
                        "ADMIN_DHCP_NETMASK=255.255.255.0",
                        "ADMIN_DHCP_LEASE_TIME=12h",
                        "ADMIN_DNS_NAME=vision-hub.lan",
                        "MQTT_LISTENER_ADDRESS=0.0.0.0",
                        "MQTT_PORT=1883",
                    )
                ),
                encoding="utf-8",
            )

            result = _run_script(DOCKER_RENDER_SCRIPT, env_file=env_path)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("FIELD_INTERFACE and ADMIN_INTERFACE must be different", result.stderr)

    def test_network_script_rejects_default_admin_wifi_password(self) -> None:
        """Refuse the committed admin Wi-Fi placeholder before touching the host."""

        result = _run_script(RPI_NETWORK_SCRIPT)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("change ADMIN_WIFI_PASSWORD", result.stderr)

    def test_docker_render_configs_uses_default_field_contract(self) -> None:
        """Render Docker-mounted service configs from the default env file."""

        with tempfile.TemporaryDirectory() as temp_dir:
            generated_dir = Path(temp_dir) / "generated"
            result = _run_script(DOCKER_RENDER_SCRIPT, env={"GENERATED_DIR": str(generated_dir)})

            field_dnsmasq_config = (generated_dir / "dnsmasq-field" / "vision-hub.conf").read_text(
                encoding="utf-8"
            )
            admin_dnsmasq_config = (generated_dir / "dnsmasq-admin" / "vision-hub.conf").read_text(
                encoding="utf-8"
            )
            mosquitto_config = (generated_dir / "mosquitto" / "vision-hub.conf").read_text(encoding="utf-8")
            homeassistant_dashboard = (generated_dir / "homeassistant" / "dashboards" / "vision-hub.yaml").read_text(
                encoding="utf-8"
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("interface=eth0", field_dnsmasq_config)
        self.assertIn("dhcp-option=option:router,192.168.50.1", field_dnsmasq_config)
        self.assertIn("mqtt://192.168.50.1:1883", field_dnsmasq_config)
        self.assertIn("interface=wlan0", admin_dnsmasq_config)
        self.assertIn("dhcp-range=192.168.60.20,192.168.60.100,255.255.255.0,12h", admin_dnsmasq_config)
        self.assertIn("port=53", admin_dnsmasq_config)
        self.assertIn("listen-address=192.168.60.1", admin_dnsmasq_config)
        self.assertIn("address=/vision-hub.lan/192.168.60.1", admin_dnsmasq_config)
        self.assertIn("dhcp-option=option:dns-server,192.168.60.1", admin_dnsmasq_config)
        self.assertNotIn("dhcp-option=option:router", admin_dnsmasq_config)
        self.assertIn("listener 1883 0.0.0.0", mosquitto_config)
        self.assertIn("persistence_location /mosquitto/data/", mosquitto_config)
        self.assertIn("log_dest stdout", mosquitto_config)
        self.assertIn("image.p4_001_latest_capture", homeassistant_dashboard)
        self.assertIn("Media > captures", homeassistant_dashboard)
        self.assertNotIn("<", field_dnsmasq_config)
        self.assertNotIn("<", admin_dnsmasq_config)
        self.assertNotIn("<", mosquitto_config)
        self.assertNotIn("<", homeassistant_dashboard)

    def test_docker_systemd_render_points_to_compose_stack(self) -> None:
        """Render the systemd unit for the repository Docker Compose stack."""

        result = _run_script(DOCKER_INSTALL_SCRIPT, "--render-service-only", env={"DOCKER_BIN": "/usr/bin/docker"})

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"WorkingDirectory={ROOT}", result.stdout)
        self.assertIn(f"EnvironmentFile={ROOT}/deploy/vision-hub-network.env", result.stdout)
        self.assertIn(f"ExecStartPre={ROOT}/deploy/docker/render-configs.sh", result.stdout)
        self.assertIn("ExecStart=/usr/bin/docker compose up -d --remove-orphans", result.stdout)
        self.assertIn("ExecStop=/usr/bin/docker compose down", result.stdout)
        self.assertNotIn("<project_dir>", result.stdout)
        self.assertNotIn("<docker_bin>", result.stdout)

    def test_compose_file_defines_restartable_host_network_services(self) -> None:
        """Validate the Docker Compose service contract without starting it."""

        compose = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
        services = compose["services"]

        for service_name in ("dnsmasq-field", "dnsmasq-admin", "mosquitto", "vision-hub", "homeassistant"):
            with self.subTest(service=service_name):
                service = services[service_name]
                self.assertEqual(service["network_mode"], "host")
                self.assertEqual(service["restart"], "unless-stopped")

        self.assertEqual(services["dnsmasq-field"]["cap_add"], ["NET_ADMIN", "NET_RAW"])
        self.assertEqual(services["dnsmasq-admin"]["cap_add"], ["NET_ADMIN", "NET_RAW"])
        self.assertIn("dnsmasq-field/vision-hub.conf", services["dnsmasq-field"]["volumes"][0])
        self.assertIn("dnsmasq-admin/vision-hub.conf", services["dnsmasq-admin"]["volumes"][0])
        self.assertIn("eclipse-mosquitto:2", services["mosquitto"]["image"])
        self.assertEqual(services["vision-hub"]["environment"]["VISION_HUB_MQTT_HOST"], "127.0.0.1")
        self.assertEqual(
            services["vision-hub"]["volumes"][1],
            {
                "type": "bind",
                "source": "${VISION_HUB_HOST_DATA_DIR:-/var/lib/vision-hub-data}",
                "target": "/var/lib/vision-hub",
            },
        )
        self.assertEqual(services["homeassistant"]["image"], "ghcr.io/home-assistant/home-assistant:stable")
        self.assertTrue(services["homeassistant"]["privileged"])
        self.assertEqual(services["homeassistant"]["environment"]["TZ"], "${HOME_ASSISTANT_TZ:-Europe/Paris}")
        self.assertEqual(
            services["homeassistant"]["volumes"][0],
            {
                "type": "bind",
                "source": "${HOME_ASSISTANT_CONFIG_DIR:-/var/lib/vision-hub-homeassistant}",
                "target": "/config",
            },
        )
        self.assertIn("./deploy/homeassistant/configuration.yaml:/config/configuration.yaml:ro", services["homeassistant"]["volumes"])
        self.assertIn(
            "./deploy/docker/generated/homeassistant/dashboards/vision-hub.yaml:/config/dashboards/vision-hub.yaml:ro",
            services["homeassistant"]["volumes"],
        )
        self.assertEqual(
            services["homeassistant"]["volumes"][3],
            {
                "type": "bind",
                "source": "${VISION_HUB_HOST_DATA_DIR:-/var/lib/vision-hub-data}/captures",
                "target": "/media/vision-hub-captures",
                "read_only": True,
            },
        )
        self.assertIn("/etc/localtime:/etc/localtime:ro", services["homeassistant"]["volumes"])
        self.assertIn("/run/dbus:/run/dbus:ro", services["homeassistant"]["volumes"])
        self.assertIn("mosquitto-data", compose["volumes"])
        self.assertNotIn("vision-hub-data", compose["volumes"])
        self.assertNotIn("homeassistant-data", compose["volumes"])

    def test_docker_compose_config_when_available(self) -> None:
        """Validate the Compose file with Docker Compose when available."""

        docker = shutil.which("docker")
        if docker is None:
            self.skipTest("docker is not installed")

        version = subprocess.run(
            [docker, "compose", "version"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        if version.returncode != 0:
            self.skipTest("docker compose plugin is not available")

        render = _run_script(DOCKER_RENDER_SCRIPT)
        self.assertEqual(render.returncode, 0, render.stderr)

        result = subprocess.run(
            [docker, "compose", "-f", str(COMPOSE_FILE), "config"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)


def _run_script(
    script: Path,
    *args: str,
    env_file: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run one deployment script and capture its output.

    Args:
        script: Script path to run through bash.
        *args: Extra command-line arguments passed to the script.
        env_file: Optional field network env file override.
        env: Optional environment overrides.

    Returns:
        Completed subprocess result.
    """

    command_env = os.environ.copy()
    if env_file is not None:
        command_env["ENV_FILE"] = str(env_file)
    if env is not None:
        command_env.update(env)

    return subprocess.run(
        ["bash", str(script), *args],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        env=command_env,
    )


if __name__ == "__main__":
    unittest.main()
