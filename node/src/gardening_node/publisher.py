"""MQTT publisher for AWS IoT Core"""
import logging
import threading

import paho.mqtt.client as mqtt

logger = logging.getLogger(__name__)

PORT = 8883  # MQTT over TLS with X.509 client certificates
QOS = 1  # contract: every message is QoS 1
KEEPALIVE_S = 30
# Readings kept in memory while offline: one week of hourly readings
MAX_QUEUED = 7 * 24
# How long start() waits for the first connection (see start())
CONNECT_WAIT_S = 15


class Publisher:
    def __init__(self, cfg):
        for path in (cfg.cert, cfg.key, cfg.root_ca):
            if not path.is_file():
                raise FileNotFoundError(f"missing {path}; provision the node first")
        self.cfg = cfg
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=cfg.thing_name)
        client.tls_set(ca_certs=str(cfg.root_ca), certfile=str(cfg.cert), keyfile=str(cfg.key))
        client.reconnect_delay_set(min_delay=1, max_delay=120)
        client.max_queued_messages_set(MAX_QUEUED)
        client.on_connect = self._on_connect
        client.on_connect_fail = self._on_connect_fail
        client.on_disconnect = self._on_disconnect
        self.client = client
        self._connected = threading.Event()

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            logger.error("connection refused by %s: %s", self.cfg.endpoint, reason_code)
        else:
            logger.info("connected to %s", self.cfg.endpoint)
            self._connected.set()

    def _on_connect_fail(self, client, userdata):
        logger.warning(
            "cannot reach %s:%d; retrying (network down or wrong endpoint?)",
            self.cfg.endpoint,
            PORT,
        )

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        self._connected.clear()
        # AWS IoT Core closes the connection without an error code when the
        # certificate or the IoT policy is wrong: this is where that shows up.
        if reason_code != 0:
            logger.warning(
                "disconnected (%s); reconnecting. If it repeats, check the certificate and the IoT policy",
                reason_code,
            )

    def start(self, wait_s=CONNECT_WAIT_S):
        """Connect in the background and wait up to `wait_s` for the first connection.

        Why wait: connect_async() returns at once and the TLS handshake + CONNACK
        take a moment (about a second, more on a Pi or with a slow network). The
        service takes its first reading right after start(), so without waiting
        that reading was always published before the connection was up: paho
        queued it and delivered it a second later, but every start logged a
        misleading "not connected" warning.

        Not waiting forever: if the network is down at boot, start() returns after
        `wait_s`, paho keeps retrying, and readings are queued until it connects
        (then the warning is real). Returns True if connected.
        Solution provided by Claude
        """
        self.client.connect_async(self.cfg.endpoint, PORT, keepalive=KEEPALIVE_S)
        self.client.loop_start()
        if self._connected.wait(wait_s):
            return True
        logger.warning("not connected after %s s; readings will be queued until it connects", wait_s)
        return False

    def send(self, payload, timeout=15):
        """Publish an encoded reading. Returns True if the broker acknowledged it (PUBACK)."""
        info = self.client.publish(self.cfg.topic, payload, qos=QOS)
        if info.rc == mqtt.MQTT_ERR_NO_CONN:
            logger.warning("not connected: reading queued, it will be sent on reconnect")
            return False
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            logger.error("reading dropped: %s", mqtt.error_string(info.rc))
            return False
        info.wait_for_publish(timeout)
        if not info.is_published():
            logger.warning("no PUBACK within %s s: paho keeps retrying", timeout)
            return False
        return True

    def stop(self):
        self.client.disconnect()
        self.client.loop_stop()
