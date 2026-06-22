"""
punch.py — UDP hole punching ("simultaneous open") с ICE-подобным выбором
лучшего кандидата.

Алгоритм (упрощённый ICE):
  1. Каждая сторона собирает кандидатов: host (локальный IP:port) + srflx
     (внешний IP:port через STUN, см. stun.py) — на одном и том же сокете.
  2. Кандидаты обмениваются через signaling.py (MQTT offer/answer).
  3. Обе стороны одновременно и многократно шлют короткие "punch"-пакеты на
     ВСЕ кандидаты друг друга (host и srflx) — для Cone NAT первый же ответный
     пакет извне "открывает" сессию в таблице роутера, после чего трафик
     начинает проходить в обе стороны.
  4. Первый кандидат, на который пришёл валидный ответ — выбирается как
     рабочий путь (как ICE connectivity check, но без приоритетов SDP).
  5. Если за PUNCH_TIMEOUT никто не ответил — оба Symmetric NAT, прямой путь
     невозможен => вызывающий код переключается на turn_relay.py.
"""

import os
import socket
import threading
import time

MAGIC = b"NOVA-PUNCH-v1:"
PUNCH_INTERVAL = 0.2
PUNCH_TIMEOUT = 8.0


def gather_candidates(sock: socket.socket):
    """Возвращает список (ip, port, kind) кандидатов для этого сокета."""
    from . import stun
    cands = []
    local_ip = sock.getsockname()[0]
    local_port = sock.getsockname()[1]
    if local_ip not in ("0.0.0.0", "127.0.0.1"):
        cands.append((local_ip, local_port, "host"))
    else:
        # сокет забинден на 0.0.0.0 — берём локальный IP отдельно для host-кандидата
        try:
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            probe.connect(("8.8.8.8", 80))
            cands.append((probe.getsockname()[0], local_port, "host"))
            probe.close()
        except OSError:
            pass

    best, _all = stun.best_reflexive_address(sock)
    if best:
        cands.append((best[0], best[1], "srflx"))
    return cands


def punch(sock: socket.socket, my_token: bytes, remote_candidates: list,
          timeout: float = PUNCH_TIMEOUT):
    """Шлёт punch-пакеты на все remote_candidates параллельно, слушает ответы
    на том же сокете. Возвращает рабочий peer_addr или None, если все кандидаты
    недостижимы (вероятно Symmetric NAT с обеих сторон -> нужен relay).

    my_token — общий секрет сессии (например nonce из offer/answer), чтобы не
    реагировать на случайный мусор/сканеры на UDP-порту."""
    stop_flag = threading.Event()
    found = {"addr": None}

    def sender():
        msg = MAGIC + my_token
        while not stop_flag.is_set():
            for ip, port, _kind in remote_candidates:
                try:
                    sock.sendto(msg, (ip, port))
                except OSError:
                    pass
            time.sleep(PUNCH_INTERVAL)

    t = threading.Thread(target=sender, daemon=True)
    t.start()

    old_timeout = sock.gettimeout()
    sock.settimeout(0.3)
    deadline = time.monotonic() + timeout
    expected = MAGIC + my_token
    try:
        while time.monotonic() < deadline and found["addr"] is None:
            try:
                data, addr = sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            if data == expected or data.startswith(MAGIC):
                # подтверждаем (на случай если peer ещё не получил наш пакет первым)
                try:
                    sock.sendto(expected, addr)
                except OSError:
                    pass
                found["addr"] = addr
    finally:
        stop_flag.set()
        sock.settimeout(old_timeout)

    return found["addr"]
