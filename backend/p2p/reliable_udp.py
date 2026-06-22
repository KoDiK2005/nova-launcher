"""
reliable_udp.py — лёгкий надёжный канал поверх "сырого" UDP-сокета.

Зачем: после успешного hole punching у нас есть голый UDP-путь между двумя
NAT. Minecraft работает по TCP, поэтому нужно поверх UDP дать гарантии
доставки и порядка, но без оверхеда полного TCP/IP-стека (минимизация
накладных расходов ради низкого пинга — см. п.3 задачи).

Протокол кадра (после расшифровки SessionCrypto):
  [1 байт flags][4 байта seq][payload]
  flags: 0x01 DATA, 0x02 ACK, 0x04 PING, 0x08 PONG

Гарантии:
  - порядок и доставка DATA через скользящее окно + retransmit по таймауту
  - keep-alive PING/PONG каждые KEEPALIVE_SEC, чтобы NAT-маппинг на роутере
    и провайдере не "протухал" (большинство NAT убирают UDP-сессию через
    20-60 сек простоя)
  - простая детекция обрыва: если PONG не пришёл N keep-alive подряд —
    канал считается мёртвым, вызывающий код инициирует reconnect/re-punch
"""

import socket
import struct
import threading
import time
from collections import OrderedDict

FLAG_DATA = 0x01
FLAG_ACK = 0x02
FLAG_PING = 0x04
FLAG_PONG = 0x08

KEEPALIVE_SEC = 5.0
DEAD_AFTER_MISSED = 4          # 4 * 5с = 20с без ответа -> канал считается оборванным
RETRANSMIT_SEC = 0.25
MAX_RETRANSMIT = 12


class ReliableUdpChannel:
    """Один логический канал данных с конкретным peer-адресом поверх UDP-сокета.
    sock должен быть уже создан/bind-нут вызывающим кодом (тот же сокет, что
    использовался для STUN и punching)."""

    def __init__(self, sock: socket.socket, peer_addr: tuple, crypto, on_data, on_dead):
        self.sock = sock
        self.peer_addr = peer_addr
        self.crypto = crypto          # SessionCrypto: encrypt/decrypt
        self.on_data = on_data        # callback(bytes) при получении DATA по порядку
        self.on_dead = on_dead        # callback() при обрыве соединения

        self._send_seq = 0
        self._recv_expect = 0
        self._reorder_buf: dict[int, bytes] = OrderedDict()
        self._unacked: dict[int, tuple] = {}   # seq -> (frame_bytes, last_sent_ts, retries)

        self._missed_pongs = 0
        self._last_pong = time.monotonic()

        self._lock = threading.Lock()
        self._alive = True
        threading.Thread(target=self._keepalive_loop, daemon=True).start()
        threading.Thread(target=self._retransmit_loop, daemon=True).start()

    # --- отправка -----------------------------------------------------------

    def send(self, payload: bytes):
        with self._lock:
            seq = self._send_seq
            self._send_seq += 1
            frame = struct.pack("!BI", FLAG_DATA, seq) + payload
            self._unacked[seq] = (frame, time.monotonic(), 0)
        self._raw_send(frame)

    def _raw_send(self, frame: bytes):
        try:
            ct = self.crypto.encrypt(frame)
            self.sock.sendto(ct, self.peer_addr)
        except OSError:
            pass

    # --- приём (вызывается из общего диспетчера сокета, см. tunnel.py) -----

    def on_packet(self, ciphertext: bytes):
        plain = self.crypto.decrypt(ciphertext)
        if plain is None or len(plain) < 5:
            return  # не наше / поддельное / битое — тихо отбрасываем
        flags, seq = struct.unpack_from("!BI", plain, 0)
        body = plain[5:]

        if flags & FLAG_PING:
            self._raw_send(struct.pack("!BI", FLAG_PONG, seq))
            return
        if flags & FLAG_PONG:
            with self._lock:
                self._missed_pongs = 0
                self._last_pong = time.monotonic()
            return
        if flags & FLAG_ACK:
            with self._lock:
                self._unacked.pop(seq, None)
            return
        if flags & FLAG_DATA:
            self._raw_send(struct.pack("!BI", FLAG_ACK, seq))
            with self._lock:
                if seq < self._recv_expect:
                    return  # дубликат
                self._reorder_buf[seq] = body
                while self._recv_expect in self._reorder_buf:
                    chunk = self._reorder_buf.pop(self._recv_expect)
                    self._recv_expect += 1
                    self.on_data(chunk)

    # --- фоновые задачи -------------------------------------------------------

    def _keepalive_loop(self):
        seq = 0
        while self._alive:
            time.sleep(KEEPALIVE_SEC)
            if not self._alive:
                return
            self._raw_send(struct.pack("!BI", FLAG_PING, seq))
            seq += 1
            with self._lock:
                self._missed_pongs += 1
                dead = self._missed_pongs > DEAD_AFTER_MISSED
            if dead:
                self.close()
                self.on_dead()
                return

    def _retransmit_loop(self):
        while self._alive:
            time.sleep(RETRANSMIT_SEC)
            now = time.monotonic()
            with self._lock:
                items = list(self._unacked.items())
            for seq, (frame, last_ts, retries) in items:
                if now - last_ts < RETRANSMIT_SEC:
                    continue
                if retries >= MAX_RETRANSMIT:
                    # слишком много потерь подряд — путь, видимо, умер
                    self.close()
                    self.on_dead()
                    return
                self._raw_send(frame)
                with self._lock:
                    if seq in self._unacked:
                        self._unacked[seq] = (frame, now, retries + 1)

    def close(self):
        self._alive = False
