"""
manager.py — публичное API P2P-подсистемы. Всё, что нужно backend/api.py:

    mgr = P2PManager(config_dict, save_config_fn)
    mgr.ensure_identity()                       # один раз создаёт ключи игрока
    mgr.host_ready(mc_port=25565)                 # начинаем принимать гостей
    mgr.connect_to_friend("Username", local_port=25566)  # подключаемся к другу

Авторизация: connect_to_friend разрешён только если имя есть в cfg["friends"].
Offer от незнакомого имени просто игнорируется (см. _on_signal).
Identity друга закрепляется по TOFU при первом успешном обмене (как
known_hosts в SSH) и сохраняется в cfg["friends"][i]["pub"].
"""

import os
import threading
import time

from . import signaling
from . import stun
from .crypto_box import Identity, generate_identity
from .tunnel import establish_link, LocalBridge

RECONNECT_DELAYS = [1, 2, 4, 8, 8, 8]


class P2PManager:
    def __init__(self, load_config, save_config, relay_server: tuple | None = None):
        self._load_config = load_config
        self._save_config = save_config
        self._relay_server = relay_server   # ("turn.example.com", 27460) или None
        self._username = None
        self._identity: Identity | None = None
        self._bridges: dict[str, LocalBridge] = {}
        self._on_status = lambda friend, status, info: None

    def set_status_callback(self, cb):
        """cb(friend_name, status, info) — для обновления UI: 'connecting',
        'punching', 'relay', 'connected', 'failed', 'disconnected'."""
        self._on_status = cb

    # --- identity -------------------------------------------------------------

    def ensure_identity(self) -> str:
        cfg = self._load_config()
        self._username = cfg["username"]
        if not cfg.get("p2p_priv"):
            priv, pub = generate_identity()
            cfg["p2p_priv"] = priv
            cfg["p2p_pub"] = pub
            self._save_config({"p2p_priv": priv, "p2p_pub": pub})
        self._identity = Identity(cfg["p2p_priv"])
        signaling.listen(self._username, self._on_signal)
        return cfg["p2p_pub"]

    def _friend_record(self, name: str) -> dict | None:
        cfg = self._load_config()
        for f in cfg["friends"]:
            if f["name"].lower() == name.lower():
                return f
        return None

    def _pin_friend_pubkey(self, name: str, pub_b64: str):
        cfg = self._load_config()
        changed = False
        for f in cfg["friends"]:
            if f["name"].lower() == name.lower():
                if not f.get("pub"):
                    f["pub"] = pub_b64
                    changed = True
                # если ключ уже закреплён и не совпадает — потенциальный спуфинг,
                # игнорируем нового претендента (TOFU, как known_hosts mismatch)
        if changed:
            self._save_config({"friends": cfg["friends"]})

    # --- хостинг: ждём входящих offer от друзей --------------------------------

    def _on_signal(self, data):
        if not data or data.get("type") != "offer":
            return
        friend_name = data.get("from", "")
        record = self._friend_record(friend_name)
        if record is None:
            return  # не в списке друзей — игнорируем (авторизация по списку друзей)
        pinned = record.get("pub")
        if pinned and pinned != data.get("pub"):
            return  # ключ не совпадает с закреплённым -> отклоняем (anti-spoof)
        self._pin_friend_pubkey(friend_name, data.get("pub", ""))

        threading.Thread(
            target=self._accept_as_host,
            args=(friend_name, data),
            daemon=True,
        ).start()

    def _accept_as_host(self, friend_name: str, offer: dict, mc_port: int = 25565):
        self._on_status(friend_name, "connecting", {})
        sock_candidates = self._gather_and_announce_answer(friend_name, offer)
        bridge = LocalBridge(is_host=True, mc_port=mc_port)
        self._wire_link(friend_name, bridge, offer["nonce"], offer["cand"], offer["pub"])

    def _gather_and_announce_answer(self, friend_name: str, offer: dict):
        import socket as _socket
        tmp = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
        tmp.bind(("0.0.0.0", 0))
        from . import punch as _punch
        cands = _punch.gather_candidates(tmp)
        tmp.close()
        cfg = self._load_config()
        signaling.send_answer(friend_name, self._username, cfg["p2p_pub"], cands, offer["nonce"])
        return cands

    # --- клиент: инициируем подключение к другу --------------------------------

    def connect_to_friend(self, friend_name: str, local_port: int = 25566) -> bool:
        record = self._friend_record(friend_name)
        if record is None:
            return False  # не друг -> не авторизован

        self._on_status(friend_name, "connecting", {})
        import socket as _socket
        tmp = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
        tmp.bind(("0.0.0.0", 0))
        from . import punch as _punch
        my_cands = _punch.gather_candidates(tmp)
        tmp.close()

        nonce = os.urandom(8).hex()
        cfg = self._load_config()
        signaling.send_offer(friend_name, self._username, cfg["p2p_pub"], my_cands, nonce)

        answer = self._wait_for_answer(friend_name, nonce, timeout=10.0)
        if answer is None:
            self._on_status(friend_name, "failed", {"reason": "friend offline or unreachable"})
            return False

        pinned = record.get("pub")
        if pinned and pinned != answer["pub"]:
            self._on_status(friend_name, "failed", {"reason": "pubkey mismatch"})
            return False
        self._pin_friend_pubkey(friend_name, answer["pub"])

        bridge = LocalBridge(is_host=False, listen_port=local_port)
        threading.Thread(
            target=self._wire_link,
            args=(friend_name, bridge, nonce, answer["cand"], answer["pub"]),
            daemon=True,
        ).start()
        return True

    def _wait_for_answer(self, friend_name, nonce, timeout):
        result = {"data": None}
        done = threading.Event()

        def cb(data):
            if data and data.get("type") == "answer" and data.get("nonce") == nonce \
                    and data.get("from", "").lower() == friend_name.lower():
                result["data"] = data
                done.set()

        signaling.listen(self._username, cb)
        done.wait(timeout)
        signaling.listen(self._username, self._on_signal)  # возвращаем основной обработчик
        return result["data"]

    # --- общая часть: punching + reliable-канал + автопереподключение ----------

    def _wire_link(self, friend_name, bridge: LocalBridge, nonce, candidates, peer_pub):
        token = nonce.encode()
        attempt = 0
        while True:
            link = establish_link(
                self._identity, peer_pub, token, candidates, self._relay_server,
                on_data=bridge.on_tunnel_data,
                on_dead=lambda: self._on_link_dead(friend_name, bridge, nonce, candidates, peer_pub),
            )
            if link:
                bridge.attach_link(link)
                self._bridges[friend_name] = bridge
                via = "relay" if link.__class__.__name__ == "TcpRelayLink" else "p2p"
                self._on_status(friend_name, "connected", {"via": via})
                return
            if attempt >= len(RECONNECT_DELAYS):
                self._on_status(friend_name, "failed", {"reason": "no path (symmetric NAT, no relay)"})
                return
            time.sleep(RECONNECT_DELAYS[attempt])
            attempt += 1
            self._on_status(friend_name, "reconnecting", {"attempt": attempt})

    def _on_link_dead(self, friend_name, bridge, nonce, candidates, peer_pub):
        self._on_status(friend_name, "disconnected", {})
        # короткая потеря пакетов не должна выкидывать игрока — пробуем
        # восстановить путь автоматически (п.3: reconnect без вылета из игры)
        self._wire_link(friend_name, bridge, nonce, candidates, peer_pub)

    def disconnect(self, friend_name: str):
        bridge = self._bridges.pop(friend_name, None)
        if bridge:
            bridge.stop()

    def shutdown(self):
        for name in list(self._bridges):
            self.disconnect(name)
        if self._username:
            signaling.stop_listening(self._username)
