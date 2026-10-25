import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from config import Config, ConfigError


class ConfigTests(unittest.TestCase):
    def test_defaults_use_hostname_and_universal_topics(self):
        with patch("config.socket.gethostname", return_value="workstation"):
            config = Config.from_env({"MQTT_BROKER_HOST": "broker.local"})

        self.assertEqual(config.port, 1883)
        self.assertEqual(config.device_name, "workstation")
        self.assertEqual(config.availability_topic, "data/workstation/lw")
        self.assertEqual(config.battery_topic, "data/workstation/state")
        self.assertEqual(config.plugged_topic, "data/workstation/status")
        self.assertTrue(config.home_assistant_discovery)

    def test_device_name_file_is_fallback_for_containers(self):
        with tempfile.TemporaryDirectory() as directory:
            hostname = Path(directory, "hostname")
            hostname.write_text("linux-laptop\n", encoding="utf-8")
            config = Config.from_env(
                {
                    "MQTT_BROKER_HOST": "broker.local",
                    "MQTT_DEVICE_NAME": "",
                    "MQTT_DEVICE_NAME_FILE": str(hostname),
                }
            )

        self.assertEqual(config.device_name, "linux-laptop")
        self.assertEqual(config.base_topic, "data/linux-laptop")

    def test_device_name_override_and_discovery_topics(self):
        config = Config.from_env(
            {
                "MQTT_BROKER_HOST": "broker.local",
                "MQTT_DEVICE_NAME": "Lab.Laptop",
                "HOME_ASSISTANT_DISCOVERY_PREFIX": "ha",
            }
        )

        self.assertEqual(config.base_topic, "data/Lab.Laptop")
        self.assertEqual(config.discovery_id, "battery2mqtt_Lab_Laptop")
        self.assertEqual(
            config.battery_discovery_topic,
            "ha/sensor/battery2mqtt_Lab_Laptop_battery/config",
        )
        self.assertEqual(
            config.plugged_discovery_topic,
            "ha/binary_sensor/battery2mqtt_Lab_Laptop_plugged/config",
        )

    def test_rejects_device_name_with_topic_separator(self):
        with self.assertRaisesRegex(ConfigError, "single MQTT topic segment"):
            Config.from_env(
                {
                    "MQTT_BROKER_HOST": "broker.local",
                    "MQTT_DEVICE_NAME": "building/laptop",
                }
            )

    def test_tls_uses_secure_default_port(self):
        config = Config.from_env(
            {
                "MQTT_BROKER_HOST": "broker.local",
                "MQTT_BROKER_TLS": "true",
                "MQTT_BROKER_PORT": "",
            }
        )

        self.assertEqual(config.port, 8883)
        self.assertTrue(config.tls)

    def test_reads_credentials_from_files_and_removes_only_newline(self):
        with tempfile.TemporaryDirectory() as directory:
            username = Path(directory, "username")
            password = Path(directory, "password")
            username.write_text("mqtt-user\n", encoding="utf-8")
            password.write_text(" secret value \n", encoding="utf-8")

            config = Config.from_env(
                {
                    "MQTT_BROKER_HOST": "broker.local",
                    "MQTT_BROKER_USERNAME_FILE": str(username),
                    "MQTT_BROKER_PASSWORD_FILE": str(password),
                }
            )

        self.assertEqual(config.username, "mqtt-user")
        self.assertEqual(config.password, " secret value ")

    def test_rejects_ambiguous_secret_source(self):
        with self.assertRaisesRegex(ConfigError, "Set only one"):
            Config.from_env(
                {
                    "MQTT_BROKER_HOST": "broker.local",
                    "MQTT_BROKER_USERNAME": "direct",
                    "MQTT_BROKER_USERNAME_FILE": "/run/secrets/username",
                }
            )

    def test_rejects_invalid_reconnect_range(self):
        with self.assertRaisesRegex(ConfigError, "MAX_DELAY"):
            Config.from_env(
                {
                    "MQTT_BROKER_HOST": "broker.local",
                    "MQTT_RECONNECT_MIN_DELAY": "30",
                    "MQTT_RECONNECT_MAX_DELAY": "10",
                }
            )

    def test_rejects_password_without_username(self):
        with self.assertRaisesRegex(ConfigError, "USERNAME"):
            Config.from_env(
                {
                    "MQTT_BROKER_HOST": "broker.local",
                    "MQTT_BROKER_PASSWORD": "secret",
                }
            )


if __name__ == "__main__":
    unittest.main()
