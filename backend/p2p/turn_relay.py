"""
turn_relay.py — relay-фолбэк ("TURN-lite") на случай, когда прямой UDP
hole punching не сработал (оба пира за Symmetric NAT, либо UDP режется
файрволом/провайдером — встречается у мобильных операторов).

В отличие от настоящего TURN (RFC 5766) это не универсальный relay, а
предельно простой парный TCP-пересыльщик: сервер ждёт двух клиентов с
одинаковым session_token и зеркалит байты между ними 1:1. Этого достаточно,
потому что трафик уже зашифрован end-to-end (SessionCrypto) — relay видит
только шифротекст и не может ни прочитать, ни подменить данные, то есть его
не обязательно держать самому пользователю — можно поднять один дешёвый
сервер (VPS за $3-5/мес) и переиспользовать для всех пар, как fallback.

Если у пользователя нет своего relay-сервера — TURN-фолбэк просто
недоступен и P2P-сессия не устанавливается (UI должен явно сообщить:
"оба игрока за Symmetric NAT, требуется relay-сервер").
"""

import socket
import threading

PAIR_TIMEOUT = 20.0


class TurnRelayServer:
    """Сервер пары: запускается на VPS, слушает TCP, сводит по 2 клиента
    с одинаковым 16-байтным токеном и зеркалит трафик."""

    def __init__(self, host="0.0.0.0", port=27460):
        self.host = host
        self.port = port
        self._waiting: dict[bytes, socket.socket] = {}
        self._lock = threading.Lock()
        self._srv = None

    def serve_forever(self):
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind((self.host, self.port))
        self._srv.listen(64)
        while True:
            conn, _addr = self._srv.accept()
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket):
        conn.settimeout(PAIR_TIMEOUT)
        try:
            token = conn.recv(16)
            if len(token) != 16:
                conn.close()
                return
        except OSError:
            conn.close()
            return

        with self._lock:
            partner = self._waiting.pop(token, None)
            if partner is None:
                self._waiting[token] = conn
                return  # ждём второго участника пары
        conn.settimeout(None)
        partner.settimeout(None)
        self._pipe(conn, partner)

    @staticmethod
    def _pipe(a: socket.socket, b: socket.socket):
        def forward(src, dst):
            try:
                while True:
                    data = src.recv(65536)
                    if not data:
                        break
                    dst.sendall(data)
            except OSError:
                pass
            finally:
                try:
                    src.close()
                except OSError:
                    pass
                try:
                    dst.close()
                except OSError:
                    pass

        threading.Thread(target=forward, args=(a, b), daemon=True).start()
        threading.Thread(target=forward, args=(b, a), daemon=True).start()


def connect_via_relay(relay_host: str, relay_port: int, session_token: bytes,
                       timeout: float = 15.0) -> socket.socket | None:
    """Клиентская сторона: подключается к relay-серверу, отправляет токен сессии,
    блокируется до тех пор, пока relay не сведёт нас со вторым участником пары
    (после чего соединение становится прозрачным TCP-каналом до пира)."""
    try:
        sock = socket.create_connection((relay_host, relay_port), timeout=timeout)
        sock.sendall(session_token)
        return sock
    except OSError:
        return None


if __name__ == "__main__":
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 27460
    TurnRelayServer(port=port).serve_forever()
