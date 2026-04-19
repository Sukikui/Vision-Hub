"""Tests for deployment config rendering scripts."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DNSMASQ_SCRIPT = ROOT / "deploy" / "dnsmasq" / "install-rpi.sh"
MOSQUITTO_SCRIPT = ROOT / "deploy" / "mosquitto" / "install-rpi.sh"
RPI_INTERFACE_SCRIPT = ROOT / "deploy" / "rpi" / "configure-field-interface.sh"


class DeployConfigTest(unittest.TestCase):
    """Unit tests for host deployment scripts and rendered configs."""

    def test_deploy_scripts_are_valid_bash(self) -> None:
        """Validate deployment scripts with bash syntax checking."""

        for script in (DNSMASQ_SCRIPT, MOSQUITTO_SCRIPT, RPI_INTERFACE_SCRIPT):
            with self.subTest(script=script):
                result = subprocess.run(
                    ["bash", "-n", str(script)],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )

                self.assertEqual(result.returncode, 0, result.stderr)

    def test_dnsmasq_render_only_uses_default_field_contract(self) -> None:
        """Render dnsmasq config from the default shared env without installing it."""

        result = _run_script(DNSMASQ_SCRIPT, "--render-only")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("interface=eth0", result.stdout)
        self.assertIn("port=0", result.stdout)
        self.assertIn("dhcp-range=192.168.50.20,192.168.50.200,255.255.255.0,24h", result.stdout)
        self.assertIn("dhcp-option=option:router,192.168.50.1", result.stdout)
        self.assertNotIn("<field_", result.stdout)
        self.assertNotIn("<dhcp_", result.stdout)
        self.assertNotIn("<mqtt_", result.stdout)

    def test_mosquitto_render_only_uses_default_field_contract(self) -> None:
        """Render Mosquitto config from the default shared env without installing it."""

        result = _run_script(MOSQUITTO_SCRIPT, "--render-only")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("listener 1883 0.0.0.0", result.stdout)
        self.assertIn("allow_anonymous true", result.stdout)
        self.assertIn("persistence true", result.stdout)
        self.assertNotIn("<mqtt_", result.stdout)

    def test_custom_env_changes_rendered_configs(self) -> None:
        """Render both service configs from a custom shared env file."""

        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / "custom.env"
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
                        "MQTT_LISTENER_ADDRESS=192.168.60.1",
                        "MQTT_PORT=1884",
                    )
                ),
                encoding="utf-8",
            )

            dnsmasq = _run_script(DNSMASQ_SCRIPT, "--render-only", env_file=env_path)
            mosquitto = _run_script(MOSQUITTO_SCRIPT, "--render-only", env_file=env_path)

        self.assertEqual(dnsmasq.returncode, 0, dnsmasq.stderr)
        self.assertEqual(mosquitto.returncode, 0, mosquitto.stderr)
        self.assertIn("interface=enp1s0", dnsmasq.stdout)
        self.assertIn("dhcp-range=192.168.60.20,192.168.60.99,255.255.255.0,12h", dnsmasq.stdout)
        self.assertIn("dhcp-option=option:router,192.168.60.1", dnsmasq.stdout)
        self.assertIn("mqtt://192.168.60.1:1884", dnsmasq.stdout)
        self.assertIn("listener 1884 192.168.60.1", mosquitto.stdout)

    def test_dnsmasq_rejects_gateway_that_does_not_match_field_address(self) -> None:
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
                        "MQTT_LISTENER_ADDRESS=0.0.0.0",
                        "MQTT_PORT=1883",
                    )
                ),
                encoding="utf-8",
            )

            result = _run_script(DNSMASQ_SCRIPT, "--render-only", env_file=env_path)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("FIELD_GATEWAY must match", result.stderr)


def _run_script(script: Path, *args: str, env_file: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run one deployment script and capture its output.

    Args:
        script: Script path to run through bash.
        *args: Extra command-line arguments passed to the script.
        env_file: Optional field network env file override.

    Returns:
        Completed subprocess result.
    """

    env = os.environ.copy()
    if env_file is not None:
        env["ENV_FILE"] = str(env_file)

    return subprocess.run(
        ["bash", str(script), *args],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


if __name__ == "__main__":
    unittest.main()
