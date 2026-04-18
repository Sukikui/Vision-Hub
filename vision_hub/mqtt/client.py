from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from paho.mqtt import client as mqtt

from vision_hub.mqtt.messages import (
    IncomingMqttMessage,
    NodeRuntimeConfigPatch,
    OutgoingCommand,
    PayloadError,
    build_capture_command,
    build_config_command,
    build_ping_command,
    build_reboot_command,
    build_broadcast_capture_command,
    build_broadcast_config_command,
    build_broadcast_ping_command,
    build_broadcast_reboot_command,
    parse_incoming_message,
)
from vision_hub.mqtt.topics import DEFAULT_SUBSCRIPTIONS


LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class MqttConnectionConfig:
    host: str = "127.0.0.1"
    port: int = 1883
    client_id: str = "vision-hub"
    keepalive_s: int = 60
    username: str | None = None
    password: str | None = None
    reconnect_min_delay_s: int = 1
    reconnect_max_delay_s: int = 30


class MqttClient:
    def __init__(
        self,
        config: MqttConnectionConfig,
        on_message: Callable[[IncomingMqttMessage], None],
        *,
        on_rejected_message: Callable[[str, bytes, Exception], None] | None = None,
    ) -> None:
        self._config = config
        self._on_message = on_message
        self._on_rejected_message = on_rejected_message
        self._client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=config.client_id,
        )
        if config.username is not None:
            self._client.username_pw_set(config.username, config.password)

        self._client.reconnect_delay_set(
            min_delay=config.reconnect_min_delay_s,
            max_delay=config.reconnect_max_delay_s,
        )
        self._client.on_connect = self._handle_connect
        self._client.on_disconnect = self._handle_disconnect
        self._client.on_message = self._handle_message

    def connect(self) -> None:
        self._client.connect_async(self._config.host, self._config.port, keepalive=self._config.keepalive_s)

    def loop_forever(self) -> None:
        self.connect()
        self._client.loop_forever(retry_first_connection=True)

    def start_background(self) -> None:
        self.connect()
        self._client.loop_start()

    def stop_background(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()

    def publish_command(self, command: OutgoingCommand) -> mqtt.MQTTMessageInfo:
        return self._client.publish(command.topic, command.payload, qos=command.qos, retain=command.retain)

    def send_ping(self, node_id: str, request_id: str) -> mqtt.MQTTMessageInfo:
        return self.publish_command(build_ping_command(node_id, request_id))

    def send_capture(self, node_id: str, request_id: str) -> mqtt.MQTTMessageInfo:
        return self.publish_command(build_capture_command(node_id, request_id))

    def send_reboot(self, node_id: str, request_id: str) -> mqtt.MQTTMessageInfo:
        return self.publish_command(build_reboot_command(node_id, request_id))

    def send_config(self, node_id: str, request_id: str, patch: NodeRuntimeConfigPatch) -> mqtt.MQTTMessageInfo:
        return self.publish_command(build_config_command(node_id, request_id, patch))

    def broadcast_ping(self, request_id: str) -> mqtt.MQTTMessageInfo:
        return self.publish_command(build_broadcast_ping_command(request_id))

    def broadcast_capture(self, request_id: str) -> mqtt.MQTTMessageInfo:
        return self.publish_command(build_broadcast_capture_command(request_id))

    def broadcast_reboot(self, request_id: str) -> mqtt.MQTTMessageInfo:
        return self.publish_command(build_broadcast_reboot_command(request_id))

    def broadcast_config(self, request_id: str, patch: NodeRuntimeConfigPatch) -> mqtt.MQTTMessageInfo:
        return self.publish_command(build_broadcast_config_command(request_id, patch))

    def _handle_connect(self, client: mqtt.Client, userdata: Any, flags: Any, reason_code: Any, properties: Any) -> None:
        if getattr(reason_code, "is_failure", False):
            LOG.error("MQTT connection failed: %s", reason_code)
            return

        LOG.info("MQTT connected to %s:%s", self._config.host, self._config.port)
        result, message_id = client.subscribe(list(DEFAULT_SUBSCRIPTIONS))
        if result != mqtt.MQTT_ERR_SUCCESS:
            LOG.error("failed to subscribe to ESP32 topics: result=%s mid=%s", result, message_id)

    def _handle_disconnect(self, client: mqtt.Client, userdata: Any, disconnect_flags: Any, reason_code: Any, properties: Any) -> None:
        LOG.warning("MQTT disconnected: %s", reason_code)

    def _handle_message(self, client: mqtt.Client, userdata: Any, message: mqtt.MQTTMessage) -> None:
        try:
            parsed = parse_incoming_message(message.topic, message.payload)
        except PayloadError as exc:
            LOG.warning("rejected MQTT message on %s: %s", message.topic, exc)
            if self._on_rejected_message is not None:
                self._on_rejected_message(message.topic, message.payload, exc)
            return

        self._on_message(parsed)
