#!/usr/bin/env python3
"""Publish a Linux system's battery state to MQTT."""

from __future__ import annotations

import json
import logging
import signal
import sys
import threading
import time
from typing import Any, Optional

import paho.mqtt.client as mqtt
import psutil

from config import Config, ConfigError


LOGGER = logging.getLogger("battery2mqtt")


class BatteryPublisher:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.connected = threading.Event()
        self.stopping = threading.Event()
        self._generation = 0
        self._generation_lock = threading.Lock()
        self._published_generation = -1
        self._discovery_generation = -1
        self._last_percentage: Optional[int] = None
        self._last_plugged: Optional[bool] = None
        self._next_refresh = 0.0
        self._last_battery_warning = 0.0

        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=config.client_id,
            protocol=mqtt.MQTTv5,
        )
        self.client.on_connect = self._on_connect
        self.client.on_connect_fail = self._on_connect_fail
        self.client.on_disconnect = self._on_disconnect
        self.client.enable_logger(LOGGER)
        self.client.reconnect_delay_set(
            min_delay=config.reconnect_min_delay,
            max_delay=config.reconnect_max_delay,
        )
        self.client.max_queued_messages_set(20)

        if config.username is not None:
            self.client.username_pw_set(config.username, config.password)
        if config.tls:
            self.client.tls_set(
                ca_certs=config.tls_ca_cert,
                certfile=config.tls_cert,
                keyfile=config.tls_key,
            )
            if config.tls_insecure:
                LOGGER.warning("TLS hostname verification is disabled")
                self.client.tls_insecure_set(True)

        self.client.will_set(
            config.availability_topic,
            payload="offline",
            qos=1,
            retain=True,
        )

    def _on_connect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: mqtt.ConnectFlags,
        reason_code: mqtt.ReasonCode,
        properties: Optional[mqtt.Properties],
    ) -> None:
        if reason_code.is_failure:
            self.connected.clear()
            LOGGER.error("MQTT connection rejected: %s", reason_code)
            return

        with self._generation_lock:
            self._generation += 1
        self.connected.set()
        LOGGER.info("Connected to MQTT broker %s:%d", self.config.host, self.config.port)

        info = client.publish(
            self.config.availability_topic,
            payload="online",
            qos=1,
            retain=True,
        )
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            LOGGER.warning("Could not queue online availability: %s", mqtt.error_string(info.rc))

    def _on_connect_fail(self, client: mqtt.Client, userdata: Any) -> None:
        self.connected.clear()
        LOGGER.warning(
            "MQTT connection attempt failed; the next retry uses exponential backoff"
        )

    def _on_disconnect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: mqtt.DisconnectFlags,
        reason_code: mqtt.ReasonCode,
        properties: Optional[mqtt.Properties],
    ) -> None:
        self.connected.clear()
        if self.stopping.is_set():
            LOGGER.info("Disconnected from MQTT broker")
        else:
            LOGGER.warning(
                "MQTT connection lost (%s); automatic reconnection is active", reason_code
            )

    def _generation_number(self) -> int:
        with self._generation_lock:
            return self._generation

    def _publish_confirmed(self, topic: str, payload: object) -> bool:
        if not self.connected.is_set():
            return False

        info = self.client.publish(topic, payload=payload, qos=1, retain=True)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            LOGGER.warning(
                "Publish to %s was not queued: %s", topic, mqtt.error_string(info.rc)
            )
            if info.rc == mqtt.MQTT_ERR_NO_CONN:
                self.connected.clear()
            return False

        try:
            info.wait_for_publish(timeout=self.config.publish_timeout)
        except (RuntimeError, ValueError) as exc:
            LOGGER.warning("Publish to %s failed: %s", topic, exc)
            return False

        if not info.is_published():
            LOGGER.warning(
                "Timed out after %.1fs waiting for broker acknowledgement of %s",
                self.config.publish_timeout,
                topic,
            )
            return False
        return True

    def _read_battery(self) -> Optional[Any]:
        try:
            return psutil.sensors_battery()
        except Exception:
            LOGGER.exception("Unable to read battery status")
            return None

    def _publish_home_assistant_discovery(self, generation: int) -> bool:
        if not self.config.home_assistant_discovery:
            return True
        if generation == self._discovery_generation:
            return True

        device = {
            "identifiers": [self.config.discovery_id],
            "name": self.config.device_name,
            "manufacturer": "battery2mqtt",
            "model": "Linux system battery",
        }
        origin = {"name": "battery2mqtt"}
        common = {
            "availability_topic": self.config.availability_topic,
            "payload_available": "online",
            "payload_not_available": "offline",
            "device": device,
            "origin": origin,
            "qos": 1,
        }
        battery_config = {
            **common,
            "name": "Battery",
            "unique_id": f"{self.config.discovery_id}_battery",
            "state_topic": self.config.battery_topic,
            "device_class": "battery",
            "state_class": "measurement",
            "unit_of_measurement": "%",
        }
        plugged_config = {
            **common,
            "name": "Power",
            "unique_id": f"{self.config.discovery_id}_plugged",
            "state_topic": self.config.plugged_topic,
            "device_class": "plug",
            "payload_on": "True",
            "payload_off": "False",
        }

        messages = (
            (self.config.battery_discovery_topic, battery_config),
            (self.config.plugged_discovery_topic, plugged_config),
        )
        all_published = True
        for topic, payload in messages:
            serialized = json.dumps(payload, separators=(",", ":"), sort_keys=True)
            if not self._publish_confirmed(topic, serialized):
                all_published = False

        if (
            all_published
            and generation == self._generation_number()
            and self.connected.is_set()
        ):
            self._discovery_generation = generation
            LOGGER.info("Published Home Assistant discovery configuration")
            return True
        return False

    def _warn_no_battery(self) -> None:
        now = time.monotonic()
        if now - self._last_battery_warning >= 60:
            LOGGER.warning("No battery was reported by the operating system")
            self._last_battery_warning = now

    def _sample_and_publish(self) -> None:
        battery = self._read_battery()
        if battery is None:
            self._warn_no_battery()
            return

        percentage = round(battery.percent)
        plugged = bool(battery.power_plugged)
        generation = self._generation_number()
        now = time.monotonic()
        self._publish_home_assistant_discovery(generation)
        full_snapshot = (
            generation != self._published_generation or now >= self._next_refresh
        )

        percentage_ok = True
        plugged_ok = True
        if full_snapshot or percentage != self._last_percentage:
            percentage_ok = self._publish_confirmed(self.config.battery_topic, percentage)
            if percentage_ok:
                self._last_percentage = percentage
                LOGGER.info("Published battery charge: %d%%", percentage)

        if full_snapshot or plugged != self._last_plugged:
            # Preserve the previous script's True/False payload convention.
            plugged_ok = self._publish_confirmed(self.config.plugged_topic, str(plugged))
            if plugged_ok:
                self._last_plugged = plugged
                LOGGER.info("Published power status: %s", plugged)

        if full_snapshot and percentage_ok and plugged_ok:
            # Do not mark a snapshot complete if another connection was made
            # while these two messages were being acknowledged.
            if generation == self._generation_number() and self.connected.is_set():
                self._published_generation = generation
                self._next_refresh = now + self.config.refresh_interval

    def run(self) -> None:
        LOGGER.info(
            "Starting for device %s on topic prefix %s",
            self.config.device_name,
            self.config.topic_prefix,
        )
        self.client.connect_async(
            self.config.host,
            port=self.config.port,
            keepalive=self.config.keepalive,
        )
        self.client.loop_start()

        try:
            while not self.stopping.is_set():
                if not self.connected.wait(timeout=1.0):
                    continue
                self._sample_and_publish()
                self.stopping.wait(self.config.poll_interval)
        finally:
            self.stop()

    def request_stop(self, signum: int, frame: Any) -> None:
        LOGGER.info("Received signal %d; shutting down", signum)
        self.stopping.set()

    def stop(self) -> None:
        already_stopping = self.stopping.is_set()
        self.stopping.set()
        if self.connected.is_set():
            self._publish_confirmed(self.config.availability_topic, "offline")
        self.client.disconnect()
        self.client.loop_stop()
        if not already_stopping:
            LOGGER.info("Stopped")


def main() -> int:
    try:
        config = Config.from_env()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr, flush=True)
        return 2

    logging.basicConfig(
        level=getattr(logging, config.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    publisher = BatteryPublisher(config)
    signal.signal(signal.SIGTERM, publisher.request_stop)
    signal.signal(signal.SIGINT, publisher.request_stop)
    publisher.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
