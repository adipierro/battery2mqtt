FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BATTERY2MQTT_RUN_AS_USER=battery2mqtt

RUN groupadd --system --gid 10001 battery2mqtt \
    && useradd --system --uid 10001 --gid battery2mqtt --home-dir /app battery2mqtt

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir --requirement requirements.txt

COPY --chown=battery2mqtt:battery2mqtt config.py script.py ./

CMD ["python", "script.py"]
