# battery2mqtt

`battery2mqtt` publishes a Linux computer's battery charge and external-power
status to MQTT. It is designed for long-running installations: MQTT connections
are retried with exponential backoff, every publish is acknowledged with QoS 1,
and a full retained state snapshot is sent after reconnecting.

Home Assistant MQTT discovery is enabled by default, but the state topics are
generic and can be consumed by any MQTT client.

## MQTT topics

The default base topic is `data/<hostname>`. For a host named `workstation`, the
service publishes:

| Topic | Retained payload |
| --- | --- |
| `data/workstation/state` | Rounded battery percentage, such as `83` |
| `data/workstation/status` | `True` when plugged in, otherwise `False` |
| `data/workstation/lw` | `online` or `offline` availability |

The topic prefix and hostname are configurable. `MQTT_DEVICE_NAME` defaults to
the operating system hostname. The Compose file reads the Linux host's
`/etc/hostname` through a read-only mount so the identity remains stable when the
container is recreated. An explicit `MQTT_DEVICE_NAME` takes precedence.

## Quick start with Docker Compose

Copy the example containing non-secret settings:

```sh
cp .env.example .env
```

Set `MQTT_BROKER_HOST` in `.env`. You may also set `MQTT_DEVICE_NAME` explicitly
if the detected hostname is not the name you want in MQTT and Home Assistant.

Create untracked credential files:

```sh
mkdir -p secrets
printf '%s' 'mqtt-user' > secrets/mqtt_username
printf '%s' 'mqtt-password' > secrets/mqtt_password
chmod 600 secrets/mqtt_username secrets/mqtt_password
```

Build and start the service:

```sh
docker compose up --build -d
docker compose logs -f battery2mqtt
```

The Compose file mounts `/sys/class/power_supply` and `/etc/hostname` read-only
so the container can read the host battery and stable hostname. At startup, the
container reads the explicitly granted file-backed secrets and immediately drops
permanently to the unprivileged `battery2mqtt` user before configuring MQTT or
opening a network connection. It does not need privileged mode or access to the
Docker socket. The host secret files can remain mode `0600`.

For a broker without authentication, remove the two credential `_FILE`
environment entries, the service-level `secrets` list, and the top-level
`secrets` section from `compose.yaml`.

## Run without Docker

Python 3.9 or newer is required.

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
MQTT_BROKER_HOST=mqtt.example.internal python script.py
```

For credentials, prefer files readable only by the service account:

```sh
export MQTT_BROKER_USERNAME_FILE=/run/secrets/mqtt_username
export MQTT_BROKER_PASSWORD_FILE=/run/secrets/mqtt_password
```

## Home Assistant discovery

Two retained discovery configurations are published on every MQTT connection:

- A battery percentage sensor with the `battery` device class and `%` unit.
- A plugged-in binary sensor with the `plug` device class.

Both entities are grouped under one Home Assistant device, use stable unique IDs
derived from `MQTT_DEVICE_NAME`, and share `data/<hostname>/lw` for availability.
The default discovery prefix is `homeassistant`.

Set `HOME_ASSISTANT_DISCOVERY_ENABLED=false` if the broker is not used by Home
Assistant. If discovery messages from an earlier run are already retained, clear
the corresponding `homeassistant/sensor/battery2mqtt_.../config` and
`homeassistant/binary_sensor/battery2mqtt_.../config` topics with empty retained
payloads, or delete the discovered device from Home Assistant.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `MQTT_BROKER_HOST` | required | Broker hostname or IP address |
| `MQTT_BROKER_PORT` | `1883`, or `8883` with TLS | Broker TCP port |
| `MQTT_BROKER_USERNAME` | unset | Username; prefer the `_FILE` form |
| `MQTT_BROKER_USERNAME_FILE` | unset | File containing the username |
| `MQTT_BROKER_PASSWORD` | unset | Password; prefer the `_FILE` form |
| `MQTT_BROKER_PASSWORD_FILE` | unset | File containing the password |
| `MQTT_DEVICE_NAME` | system hostname | Device name and topic segment |
| `MQTT_DEVICE_NAME_FILE` | unset | Fallback file containing the device name |
| `MQTT_TOPIC_PREFIX` | `data` | MQTT topic prefix |
| `MQTT_CLIENT_ID` | `battery2mqtt-<device>` | Stable MQTT client ID |
| `MQTT_KEEPALIVE` | `30` | MQTT keepalive in seconds |
| `MQTT_RECONNECT_MIN_DELAY` | `1` | Initial reconnect delay in seconds |
| `MQTT_RECONNECT_MAX_DELAY` | `120` | Maximum reconnect delay in seconds |
| `MQTT_PUBLISH_TIMEOUT` | `10` | QoS 1 acknowledgement timeout |
| `BATTERY_POLL_INTERVAL` | `5` | Battery polling interval in seconds |
| `BATTERY_REFRESH_INTERVAL` | `300` | Full state republish interval in seconds |
| `HOME_ASSISTANT_DISCOVERY_ENABLED` | `true` | Publish Home Assistant discovery |
| `HOME_ASSISTANT_DISCOVERY_PREFIX` | `homeassistant` | Discovery topic prefix |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL` |

Direct credential environment variables are supported for simple deployments,
but mounted files are preferable because container environment variables are
visible in container configuration. Setting both a value and its `_FILE`
counterpart is rejected.

### TLS

Set `MQTT_BROKER_TLS=true`; the port defaults to `8883` unless explicitly set.
The system trust store is used by default. Configure a private CA or mutual TLS
with `MQTT_TLS_CA_CERT`, `MQTT_TLS_CERT`, and `MQTT_TLS_KEY`. The client
certificate and key must be supplied together.

`MQTT_TLS_INSECURE=true` disables hostname verification and should only be used
temporarily while diagnosing certificate problems.

## Upgrading from the old topic layout

Earlier versions used topics such as `data/algae_battery/state` and
`data/algae_battery_status/state`. MQTT brokers keep retained messages until they
are explicitly removed. After deploying this version, clear any old retained
topics that are no longer needed by publishing an empty retained payload to each
old topic.

## Development

Run the test suite with:

```sh
python -m unittest discover -s tests -v
```
