"""Configuration loading for battery2mqtt."""

from dataclasses import dataclass
import os
from pathlib import Path
import re
import socket
from typing import Mapping, Optional


class ConfigError(ValueError):
    """Raised when the application configuration is invalid."""


def _file_contents(setting_name: str, file_path: str) -> str:
    if not file_path.strip():
        raise ConfigError(f"{setting_name} must not be empty")
    try:
        # Docker secrets and /etc/hostname normally have one trailing newline.
        # Preserve all other characters because they may be part of a password.
        return Path(file_path).read_text(encoding="utf-8").rstrip("\r\n")
    except (OSError, UnicodeError) as exc:
        raise ConfigError(f"Cannot read {setting_name} path {file_path!r}: {exc}") from exc


def _value(
    env: Mapping[str, str],
    name: str,
    *,
    default: Optional[str] = None,
    required: bool = False,
    allow_file: bool = False,
) -> Optional[str]:
    direct = env.get(name)
    file_name = f"{name}_FILE"
    file_path = env.get(file_name) if allow_file else None

    if direct is not None and file_path is not None:
        raise ConfigError(f"Set only one of {name} or {file_name}")

    if file_path is not None:
        value = _file_contents(file_name, file_path)
    elif direct is not None:
        value = direct
    else:
        value = default

    if required and (value is None or not value.strip()):
        suffix = f" or {file_name}" if allow_file else ""
        raise ConfigError(f"{name}{suffix} is required")
    return value


def _integer(env: Mapping[str, str], name: str, default: int, minimum: int = 1) -> int:
    raw = _value(env, name)
    if raw is None or not raw.strip():
        raw = str(default)
    try:
        value = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name} must be an integer") from exc
    if value < minimum:
        raise ConfigError(f"{name} must be at least {minimum}")
    return value


def _float(env: Mapping[str, str], name: str, default: float, minimum: float) -> float:
    raw = _value(env, name)
    if raw is None or not raw.strip():
        raw = str(default)
    try:
        value = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name} must be a number") from exc
    if value < minimum:
        raise ConfigError(f"{name} must be at least {minimum}")
    return value


def _boolean(env: Mapping[str, str], name: str, default: bool = False) -> bool:
    raw = _value(env, name)
    if raw is None or not raw.strip():
        raw = "true" if default else "false"
    normalized = raw.strip().lower()  # type: ignore[union-attr]
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name} must be true or false")


def _topic_part(value: Optional[str], name: str) -> str:
    normalized = (value or "").strip().strip("/")
    if not normalized:
        raise ConfigError(f"{name} must not be empty")
    if "+" in normalized or "#" in normalized:
        raise ConfigError(f"{name} must not contain MQTT wildcards")
    return normalized


@dataclass(frozen=True)
class Config:
    host: str
    port: int
    username: Optional[str]
    password: Optional[str]
    topic_prefix: str
    device_name: str
    client_id: str
    keepalive: int
    poll_interval: float
    refresh_interval: float
    publish_timeout: float
    reconnect_min_delay: int
    reconnect_max_delay: int
    tls: bool
    tls_ca_cert: Optional[str]
    tls_cert: Optional[str]
    tls_key: Optional[str]
    tls_insecure: bool
    home_assistant_discovery: bool
    home_assistant_discovery_prefix: str
    log_level: str

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "Config":
        values = os.environ if env is None else env

        host = _value(values, "MQTT_BROKER_HOST", required=True)
        tls = _boolean(values, "MQTT_BROKER_TLS", False)
        port = _integer(values, "MQTT_BROKER_PORT", 8883 if tls else 1883)
        username = _value(values, "MQTT_BROKER_USERNAME", allow_file=True)
        password = _value(values, "MQTT_BROKER_PASSWORD", allow_file=True)
        if password is not None and username is None:
            raise ConfigError("MQTT_BROKER_USERNAME is required when a password is configured")

        topic_prefix = _topic_part(
            _value(values, "MQTT_TOPIC_PREFIX", default="data"), "MQTT_TOPIC_PREFIX"
        )
        configured_device_name = _value(values, "MQTT_DEVICE_NAME")
        if not configured_device_name or not configured_device_name.strip():
            device_name_file = values.get("MQTT_DEVICE_NAME_FILE")
            if device_name_file:
                configured_device_name = _file_contents(
                    "MQTT_DEVICE_NAME_FILE", device_name_file
                )
        device_name = _topic_part(
            configured_device_name or socket.gethostname(), "MQTT_DEVICE_NAME"
        )
        if "/" in device_name:
            raise ConfigError("MQTT_DEVICE_NAME must be a single MQTT topic segment")
        client_id = (_value(values, "MQTT_CLIENT_ID") or f"battery2mqtt-{device_name}").strip()
        if not client_id:
            raise ConfigError("MQTT_CLIENT_ID must not be empty")

        reconnect_min_delay = _integer(values, "MQTT_RECONNECT_MIN_DELAY", 1)
        reconnect_max_delay = _integer(values, "MQTT_RECONNECT_MAX_DELAY", 120)
        if reconnect_max_delay < reconnect_min_delay:
            raise ConfigError(
                "MQTT_RECONNECT_MAX_DELAY must be greater than or equal to "
                "MQTT_RECONNECT_MIN_DELAY"
            )

        tls_ca_cert = _value(values, "MQTT_TLS_CA_CERT")
        tls_cert = _value(values, "MQTT_TLS_CERT")
        tls_key = _value(values, "MQTT_TLS_KEY")
        tls_insecure = _boolean(values, "MQTT_TLS_INSECURE", False)
        if bool(tls_cert) != bool(tls_key):
            raise ConfigError("MQTT_TLS_CERT and MQTT_TLS_KEY must be configured together")
        if not tls and any((tls_ca_cert, tls_cert, tls_key, tls_insecure)):
            raise ConfigError("Set MQTT_BROKER_TLS=true before configuring TLS options")

        home_assistant_discovery = _boolean(
            values, "HOME_ASSISTANT_DISCOVERY_ENABLED", True
        )
        home_assistant_discovery_prefix = _topic_part(
            _value(values, "HOME_ASSISTANT_DISCOVERY_PREFIX", default="homeassistant"),
            "HOME_ASSISTANT_DISCOVERY_PREFIX",
        )

        log_level = (_value(values, "LOG_LEVEL", default="INFO") or "INFO").upper()
        if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ConfigError("LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR, or CRITICAL")

        return cls(
            host=host.strip(),  # type: ignore[union-attr]
            port=port,
            username=username,
            password=password,
            topic_prefix=topic_prefix,
            device_name=device_name,
            client_id=client_id,
            keepalive=_integer(values, "MQTT_KEEPALIVE", 30),
            poll_interval=_float(values, "BATTERY_POLL_INTERVAL", 5.0, 0.1),
            refresh_interval=_float(values, "BATTERY_REFRESH_INTERVAL", 300.0, 1.0),
            publish_timeout=_float(values, "MQTT_PUBLISH_TIMEOUT", 10.0, 0.1),
            reconnect_min_delay=reconnect_min_delay,
            reconnect_max_delay=reconnect_max_delay,
            tls=tls,
            tls_ca_cert=tls_ca_cert,
            tls_cert=tls_cert,
            tls_key=tls_key,
            tls_insecure=tls_insecure,
            home_assistant_discovery=home_assistant_discovery,
            home_assistant_discovery_prefix=home_assistant_discovery_prefix,
            log_level=log_level,
        )

    @property
    def base_topic(self) -> str:
        return f"{self.topic_prefix}/{self.device_name}"

    @property
    def availability_topic(self) -> str:
        return f"{self.base_topic}/lw"

    @property
    def battery_topic(self) -> str:
        return f"{self.base_topic}/state"

    @property
    def plugged_topic(self) -> str:
        return f"{self.base_topic}/status"

    @property
    def discovery_id(self) -> str:
        normalized = re.sub(r"[^a-zA-Z0-9_-]+", "_", self.device_name).strip("_")
        return f"battery2mqtt_{normalized or 'device'}"

    @property
    def battery_discovery_topic(self) -> str:
        return (
            f"{self.home_assistant_discovery_prefix}/sensor/"
            f"{self.discovery_id}_battery/config"
        )

    @property
    def plugged_discovery_topic(self) -> str:
        return (
            f"{self.home_assistant_discovery_prefix}/binary_sensor/"
            f"{self.discovery_id}_plugged/config"
        )
