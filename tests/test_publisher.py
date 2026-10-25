import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import paho.mqtt.client as mqtt

from config import Config
from script import BatteryPublisher


class PublishInfo:
    def __init__(self, published=True, rc=mqtt.MQTT_ERR_SUCCESS):
        self.rc = rc
        self._published = published

    def wait_for_publish(self, timeout=None):
        return None

    def is_published(self):
        return self._published


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.config = Config.from_env(
            {
                "MQTT_BROKER_HOST": "broker.local",
                "MQTT_DEVICE_NAME": "workstation",
                "HOME_ASSISTANT_DISCOVERY_ENABLED": "false",
            }
        )
        self.publisher = BatteryPublisher(self.config)
        self.publisher.connected.set()
        self.publisher._generation = 1
        self.publisher._read_battery = Mock(
            return_value=SimpleNamespace(percent=72.6, power_plugged=True)
        )

    def test_initial_sample_publishes_full_confirmed_snapshot(self):
        self.publisher.client.publish = Mock(return_value=PublishInfo())

        self.publisher._sample_and_publish()

        calls = self.publisher.client.publish.call_args_list
        self.assertEqual([call.args[0] for call in calls], [
            self.config.battery_topic,
            self.config.plugged_topic,
        ])
        self.assertEqual(calls[0].kwargs["payload"], 73)
        self.assertEqual(calls[1].kwargs["payload"], "True")
        self.assertEqual(self.publisher._published_generation, 1)

    def test_home_assistant_discovery_is_retained_and_grouped(self):
        config = Config.from_env(
            {
                "MQTT_BROKER_HOST": "broker.local",
                "MQTT_DEVICE_NAME": "workstation",
            }
        )
        publisher = BatteryPublisher(config)
        publisher.connected.set()
        publisher._generation = 1
        publisher._read_battery = Mock(
            return_value=SimpleNamespace(percent=72.6, power_plugged=True)
        )
        publisher.client.publish = Mock(return_value=PublishInfo())

        publisher._sample_and_publish()

        calls = publisher.client.publish.call_args_list
        self.assertEqual(
            [call.args[0] for call in calls],
            [
                config.battery_discovery_topic,
                config.plugged_discovery_topic,
                config.battery_topic,
                config.plugged_topic,
            ],
        )
        battery_discovery = json.loads(calls[0].kwargs["payload"])
        plugged_discovery = json.loads(calls[1].kwargs["payload"])
        self.assertEqual(battery_discovery["state_topic"], "data/workstation/state")
        self.assertEqual(battery_discovery["device_class"], "battery")
        self.assertEqual(battery_discovery["unit_of_measurement"], "%")
        self.assertEqual(plugged_discovery["state_topic"], "data/workstation/status")
        self.assertEqual(plugged_discovery["device_class"], "plug")
        self.assertEqual(
            battery_discovery["device"]["identifiers"],
            plugged_discovery["device"]["identifiers"],
        )
        self.assertTrue(all(call.kwargs["retain"] for call in calls))
        self.assertEqual(publisher._discovery_generation, 1)

        publisher.client.publish.reset_mock()
        publisher._sample_and_publish()
        publisher.client.publish.assert_not_called()

        publisher._generation = 2
        publisher._sample_and_publish()
        reconnect_topics = [
            call.args[0] for call in publisher.client.publish.call_args_list
        ]
        self.assertEqual(
            reconnect_topics,
            [
                config.battery_discovery_topic,
                config.plugged_discovery_topic,
                config.battery_topic,
                config.plugged_topic,
            ],
        )

    def test_reconnect_forces_snapshot_even_when_state_is_unchanged(self):
        self.publisher.client.publish = Mock(return_value=PublishInfo())
        self.publisher._sample_and_publish()
        self.publisher.client.publish.reset_mock()
        self.publisher._generation = 2

        self.publisher._sample_and_publish()

        self.assertEqual(self.publisher.client.publish.call_count, 2)
        self.assertEqual(self.publisher._published_generation, 2)

    def test_unacknowledged_state_is_not_recorded_as_sent(self):
        self.publisher.client.publish = Mock(return_value=PublishInfo(published=False))

        with self.assertLogs("battery2mqtt", level="WARNING") as logs:
            self.publisher._sample_and_publish()

        self.assertIsNone(self.publisher._last_percentage)
        self.assertIsNone(self.publisher._last_plugged)
        self.assertEqual(self.publisher._published_generation, -1)
        self.assertEqual(len(logs.output), 2)


if __name__ == "__main__":
    unittest.main()
