"""
signaling.py — обмен ICE-кандидатами/публичными ключами между друзьями через
тот же публичный MQTT-брокер, что уже используется в relay.py для presence.

MQTT тут выступает только сигнальным каналом (как Signal-сервер в WebRTC) —
сам игровой трафик через него никогда не идёт. Топик именной (хэш от ника),
а полезная нагрузка — это offer/answer с публичным ключом и списком
кандидатов (host + STUN-reflexive). Поскольку брокер публичный и сообщения
никем не шифруются на транспорте, авторизация обеспечивается на уровне
приложения: переподключиться может кто угодно, но без приватного ключа
adresата он не получит доступ к туннелю (см. crypto_box.py).
"""

import json
import time

from .. import relay

SIG_PREFIX = "nova-mc-launcher/v1/p2p-sig/"


def _topic(username: str) -> str:
    # Переиспользуем ту же схему имени топика, что и relay._topic, но в своём namespace
    import hashlib
    slug = username.strip().lower()
    ns = hashlib.md5((slug + relay.APP_SALT).encode()).hexdigest()[:6]
    return f"{SIG_PREFIX}{ns}_{slug}"


def send_offer(to_username: str, from_username: str, pubkey_b64: str,
               candidates: list, nonce: str) -> bool:
    """Кладём offer в топик адресата (retained=False — это разовое сообщение,
    не статус). candidates — список (ip, port, kind) где kind: 'host'|'srflx'."""
    if not relay._ensure_connected():
        return False
    payload = json.dumps({
        "type": "offer",
        "from": from_username,
        "pub": pubkey_b64,
        "cand": candidates,
        "nonce": nonce,
        "ts": int(time.time()),
    })
    res = relay._client.publish(_topic(to_username), payload, qos=1, retain=False)
    return res.rc == 0


def send_answer(to_username: str, from_username: str, pubkey_b64: str,
                candidates: list, nonce: str) -> bool:
    if not relay._ensure_connected():
        return False
    payload = json.dumps({
        "type": "answer",
        "from": from_username,
        "pub": pubkey_b64,
        "cand": candidates,
        "nonce": nonce,
        "ts": int(time.time()),
    })
    res = relay._client.publish(_topic(to_username), payload, qos=1, retain=False)
    return res.rc == 0


def listen(username: str, callback) -> None:
    """Подписка на свой собственный топик — сюда будут приходить offer/answer
    от друзей, инициирующих P2P-сессию."""
    if not relay._ensure_connected():
        return
    topic = _topic(username)
    relay._subs[topic] = callback
    relay._client.subscribe(topic, qos=1)


def stop_listening(username: str) -> None:
    topic = _topic(username)
    relay._subs.pop(topic, None)
    if relay._client:
        try:
            relay._client.unsubscribe(topic)
        except Exception:
            pass
