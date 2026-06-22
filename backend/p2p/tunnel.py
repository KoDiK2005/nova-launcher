"""
tunnel.py — склеивает всё вместе: STUN -> signaling -> hole punching (или TURN
fallback) -> зашифрованный канал -> локальный TCP-сокет, который видит
Minecraft.

Интеграция с клиентом Minecraft (п.4 задачи): мы НЕ подменяем сетевой стек
системы и не создаём настоящий виртуальный адаптер (это требовало бы driver
уровня TAP/WinTun и админских прав — overkill для P2P между друзьями).
Вместо этого:
  - Гость подключается к серверу через 127.0.0.1:<local_port> — лаунчер сам
    прописывает эту запись в servers.dat (см. minecraft.sync_servers_dat),
    то есть для игрока это просто обычный сервер в списке.
  - Локальный TCP-листенер на 127.0.0.1 принимает соединение клиента
    Minecraft и прозрачно прокидывает байты в P2P-туннель до хоста.
  - На стороне хоста принятые из туннеля байты прокидываются в настоящий
    Minecraft-сервер на 127.0.0.1:25565.
Так Minecraft вообще не знает, что соединение P2P — для него это localhost.
"""

import os
import socket
import threading
import time

from . import stun
from . import punch
from . import signaling
from .crypto_box import Identity, SessionCrypto
from .reliable_udp import ReliableUdpChannel

PUNCH_PORT_RANGE = (28000, 28100)
RECONNECT_BACKOFF = (1, 2, 4, 8, 8)   # секунды между попытками реконнекта


class _LinkBase:
    """Общий интерфейс канала: send(bytes), get_recv_queue() — оба конкретных
    транспорта (UDP-reliable / TCP-relay) реализуют этот контракт, поэтому
    остальной код tunnel.py не знает, какой путь в итоге использовался."""

    def send(self, data: bytes):
        raise NotImplementedError

    def close(self):
        raise NotImplementedError


class UdpLink(_LinkBase):
    def __init__(self, sock, peer_addr, crypto, on_data, on_dead):
        self.chan = ReliableUdpChannel(sock, peer_addr, crypto, on_data, on_dead)
        self.sock = sock

    def send(self, data: bytes):
        # дробим на куски, чтобы не превышать безопасный MTU и не получить
        # фрагментацию IP (минимизация оверхеда/потерь, см. п.3 задачи)
        CHUNK = 1200
        for i in range(0, len(data), CHUNK):
            self.chan.send(data[i:i + CHUNK])

    def close(self):
        self.chan.close()


class TcpRelayLink(_LinkBase):
    def __init__(self, sock, crypto, on_data, on_dead):
        self.sock = sock
        self.crypto = crypto
        self._alive = True
        threading.Thread(target=self._recv_loop, args=(on_data, on_dead), daemon=True).start()

    def send(self, data: bytes):
        ct = self.crypto.encrypt(data)
        frame = len(ct).to_bytes(4, "big") + ct
        try:
            self.sock.sendall(frame)
        except OSError:
            pass

    def _recv_loop(self, on_data, on_dead):
        buf = b""
        try:
            while self._alive:
                chunk = self.sock.recv(65536)
                if not chunk:
                    break
                buf += chunk
                while len(buf) >= 4:
                    n = int.from_bytes(buf[:4], "big")
                    if len(buf) < 4 + n:
                        break
                    ct, buf = buf[4:4 + n], buf[4 + n:]
                    plain = self.crypto.decrypt(ct)
                    if plain is not None:
                        on_data(plain)
        except OSError:
            pass
        finally:
            if self._alive:
                on_dead()

    def close(self):
        self._alive = False
        try:
            self.sock.close()
        except OSError:
            pass


def establish_link(identity: Identity, peer_pub_b64: str, my_token: bytes,
                    remote_candidates: list, relay_server: tuple | None,
                    on_data, on_dead) -> _LinkBase | None:
    """Пытается установить прямой UDP-путь через hole punching; при неудаче —
    падает на TCP TURN-relay, если он сконфигурирован. Возвращает готовый Link
    или None, если оба способа не сработали."""
    crypto = SessionCrypto.derive(identity, peer_pub_b64)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    bound = False
    for p in range(*PUNCH_PORT_RANGE):
        try:
            sock.bind(("0.0.0.0", p))
            bound = True
            break
        except OSError:
            continue
    if not bound:
        sock.bind(("0.0.0.0", 0))

    peer_addr = punch.punch(sock, my_token, remote_candidates)
    if peer_addr:
        return UdpLink(sock, peer_addr, crypto, on_data, on_dead)

    sock.close()
    if relay_server:
        from . import turn_relay
        token16 = my_token[:16].ljust(16, b"\0")
        tcp_sock = turn_relay.connect_via_relay(relay_server[0], relay_server[1], token16)
        if tcp_sock:
            return TcpRelayLink(tcp_sock, crypto, on_data, on_dead)
    return None


class LocalBridge:
    """Хостовая/клиентская сторона локального TCP-моста к/от Minecraft.
    is_host=True  -> принимает данные из туннеля и шлёт их в реальный MC-сервер
                      на 127.0.0.1:mc_port, и обратно.
    is_host=False -> слушает 127.0.0.1:listen_port, ждёт подключения клиента
                      Minecraft и зеркалит его трафик в туннель."""

    def __init__(self, is_host: bool, mc_port: int = 25565, listen_port: int = 25566):
        self.is_host = is_host
        self.mc_port = mc_port
        self.listen_port = listen_port
        self._link: _LinkBase | None = None
        self._mc_sock: socket.socket | None = None
        self._listen_sock: socket.socket | None = None
        self._stopped = False

    def attach_link(self, link: _LinkBase):
        self._link = link
        if self.is_host:
            self._connect_to_local_server()
        else:
            threading.Thread(target=self._accept_client, daemon=True).start()

    # --- хост: туннель -> реальный MC-сервер ---------------------------------

    def _connect_to_local_server(self):
        try:
            self._mc_sock = socket.create_connection(("127.0.0.1", self.mc_port), timeout=5)
        except OSError:
            return
        threading.Thread(target=self._pump_mc_to_link, daemon=True).start()

    def _pump_mc_to_link(self):
        try:
            while not self._stopped:
                data = self._mc_sock.recv(65536)
                if not data:
                    break
                if self._link:
                    self._link.send(data)
        except OSError:
            pass

    def on_tunnel_data(self, data: bytes):
        """Вызывается Link-ом при получении данных из туннеля."""
        if self.is_host:
            if self._mc_sock:
                try:
                    self._mc_sock.sendall(data)
                except OSError:
                    pass
        else:
            if self._client_sock:
                try:
                    self._client_sock.sendall(data)
                except OSError:
                    pass

    # --- гость: локальный TCP listener -> туннель ----------------------------

    _client_sock = None

    def _accept_client(self):
        self._listen_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listen_sock.bind(("127.0.0.1", self.listen_port))
        self._listen_sock.listen(1)
        try:
            conn, _ = self._listen_sock.accept()
        except OSError:
            return
        self._client_sock = conn
        try:
            while not self._stopped:
                data = conn.recv(65536)
                if not data:
                    break
                if self._link:
                    self._link.send(data)
        except OSError:
            pass

    def stop(self):
        self._stopped = True
        for s in (self._mc_sock, self._listen_sock, self._client_sock):
            try:
                if s:
                    s.close()
            except OSError:
                pass
        if self._link:
            self._link.close()
