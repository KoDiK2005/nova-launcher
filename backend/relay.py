"""
relay.py — обнаружение друзей через публичный MQTT брокер.
Используем broker.emqx.io (бесплатно, без регистрации).
Данные сессии публикуются с retain=True → друг видит статус сразу при подключении.

Топик: nova-mc/host/<username_lower>
Payload: JSON {ip, port, ver, ts} или "" (офлайн)
"""

import json
import time
import threading
import hashlib

try:
    import paho.mqtt.client as mqtt
    _MQTT_OK = True
except ImportError:
    _MQTT_OK = False

BROKER   = "broker.emqx.io"
PORT     = 1883
PREFIX   = "nova-mc-launcher/v1/host/"
# Добавляем к никнейму хэш чтобы меньше шансов столкнуться с другими
APP_SALT = "nova2026"

_client    = None
_connected = False
_lock      = threading.Lock()
_subs: dict[str, callable] = {}   # topic → callback


# ─── Утилиты ──────────────────────────────────────────────────────────────────

def _topic(username: str) -> str:
    slug = username.strip().lower()
    # небольшой "namespace" — первые 6 символов хэша ника+соли
    ns = hashlib.md5((slug + APP_SALT).encode()).hexdigest()[:6]
    return f"{PREFIX}{ns}_{slug}"


def _on_connect(client, userdata, flags, rc):
    global _connected
    _connected = (rc == 0)
    if _connected:
        # переподписываемся на все топики (на случай реконнекта)
        for topic in _subs:
            client.subscribe(topic)


def _on_disconnect(client, userdata, rc):
    global _connected
    _connected = False


def _on_message(client, userdata, msg):
    topic   = msg.topic
    payload = msg.payload.decode(errors="ignore").strip()
    cb = _subs.get(topic)
    if cb:
        try:
            data = json.loads(payload) if payload else None
            cb(data)
        except Exception:
            cb(None)


def _ensure_connected():
    global _client, _connected
    if not _MQTT_OK:
        return False
    with _lock:
        if _client is None:
            _client = mqtt.Client(client_id=f"nova_{int(time.time())}", clean_session=True)
            _client.on_connect    = _on_connect
            _client.on_disconnect = _on_disconnect
            _client.on_message    = _on_message
            try:
                _client.connect_async(BROKER, PORT, keepalive=60)
                _client.loop_start()
                # ждём подключения максимум 3 секунды
                for _ in range(30):
                    if _connected:
                        break
                    time.sleep(0.1)
            except Exception:
                _client = None
                return False
    return _connected


# ─── Публичный API ────────────────────────────────────────────────────────────

def publish_session(username: str, ip: str, port: int, version: str) -> bool:
    """Объявляем что мы хостим. Друзья увидят статус автоматически."""
    if not _ensure_connected():
        return False
    payload = json.dumps({"ip": ip, "port": port, "ver": version, "ts": int(time.time())})
    res = _client.publish(_topic(username), payload, qos=1, retain=True)
    return res.rc == 0


def clear_session(username: str) -> None:
    """Очищаем сессию — говорим всем что мы оффлайн."""
    if not _MQTT_OK or _client is None:
        return
    try:
        _client.publish(_topic(username), "", qos=1, retain=True)
    except Exception:
        pass


def watch_friend(friend_username: str, callback: callable) -> None:
    """Подписываемся на статус друга. callback(data | None) при каждом изменении."""
    if not _ensure_connected():
        return
    topic = _topic(friend_username)
    _subs[topic] = callback
    _client.subscribe(topic, qos=1)


def unwatch_friend(friend_username: str) -> None:
    topic = _topic(friend_username)
    _subs.pop(topic, None)
    if _client:
        try:
            _client.unsubscribe(topic)
        except Exception:
            pass


def disconnect() -> None:
    global _client, _connected
    if _client:
        try:
            _client.loop_stop()
            _client.disconnect()
        except Exception:
            pass
        _client     = None
        _connected  = False
