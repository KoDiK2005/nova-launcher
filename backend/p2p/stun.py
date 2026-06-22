"""
stun.py — минимальный STUN-клиент (RFC 5389 Binding Request) для определения
публичного IP:port (server-reflexive адрес) и грубой классификации NAT.

Важно: STUN-запрос отправляется с того же UDP-сокета, который потом будет
использоваться для hole punching — иначе для Symmetric NAT отражённый адрес
будет бесполезен (на каждый новый 5-tuple роутер выберет новый внешний порт).
"""

import os
import socket
import struct
import time

# Публичные бесплатные STUN-серверы (без регистрации).
PUBLIC_STUN_SERVERS = [
    ("stun.l.google.com", 19302),
    ("stun1.l.google.com", 19302),
    ("stun2.l.google.com", 19302),
    ("stun.cloudflare.com", 3478),
    ("stun.miwifi.com", 3478),
    ("stun.nextcloud.com", 443),
]

_MAGIC_COOKIE = 0x2112A442
_BINDING_REQUEST = 0x0001
_BINDING_RESPONSE = 0x0101
_ATTR_XOR_MAPPED_ADDRESS = 0x0020
_ATTR_MAPPED_ADDRESS = 0x0001


def _build_binding_request(tx_id: bytes) -> bytes:
    # Header: type(2) + length(2) + magic cookie(4) + transaction id(12)
    return struct.pack("!HHI12s", _BINDING_REQUEST, 0, _MAGIC_COOKIE, tx_id)


def _parse_xor_mapped_address(body: bytes, tx_id: bytes):
    i = 0
    while i + 4 <= len(body):
        attr_type, attr_len = struct.unpack_from("!HH", body, i)
        val = body[i + 4: i + 4 + attr_len]
        if attr_type in (_ATTR_XOR_MAPPED_ADDRESS, _ATTR_MAPPED_ADDRESS) and len(val) >= 8:
            family = val[1]
            if attr_type == _ATTR_XOR_MAPPED_ADDRESS:
                port = struct.unpack("!H", val[2:4])[0] ^ (_MAGIC_COOKIE >> 16)
                if family == 0x01:  # IPv4
                    xip = struct.unpack("!I", val[4:8])[0] ^ _MAGIC_COOKIE
                    ip = socket.inet_ntoa(struct.pack("!I", xip))
                    return ip, port
            else:
                port = struct.unpack("!H", val[2:4])[0]
                ip = socket.inet_ntoa(val[4:8])
                return ip, port
        i += 4 + attr_len + (attr_len % 4)
    return None


def stun_query(sock: socket.socket, server: tuple, timeout: float = 1.5):
    """Одиночный запрос к STUN-серверу через переданный (уже bind-нутый) UDP-сокет.
    Возвращает (ip, port) или None. Не блокирует сокет навсегда — короткий таймаут."""
    tx_id = os.urandom(12)
    req = _build_binding_request(tx_id)
    old_timeout = sock.gettimeout()
    try:
        sock.settimeout(timeout)
        sock.sendto(req, server)
        while True:
            data, addr = sock.recvfrom(2048)
            if len(data) < 20:
                continue
            msg_type, msg_len, cookie, resp_tx = struct.unpack_from("!HHI12s", data, 0)
            if resp_tx != tx_id:
                continue  # чужой ответ, попавший в этот же сокет — игнорируем
            if msg_type != _BINDING_RESPONSE:
                return None
            return _parse_xor_mapped_address(data[20:20 + msg_len], tx_id)
    except (socket.timeout, OSError):
        return None
    finally:
        sock.settimeout(old_timeout)


def best_reflexive_address(sock: socket.socket, servers=None, attempts_per_server: int = 1):
    """Опрашивает несколько STUN-серверов и возвращает первый успешный результат
    с наименьшей задержкой (грубая 'race' — кто ответил быстрее всех).
    Использование одного сокета гарантирует одинаковый внешний порт у всех ответов
    (если NAT не Symmetric — тогда адреса будут отличаться, что само по себе диагностика)."""
    servers = servers or PUBLIC_STUN_SERVERS
    results = []
    for host, port in servers:
        try:
            resolved = socket.gethostbyname(host)
        except socket.gaierror:
            continue
        t0 = time.monotonic()
        for _ in range(attempts_per_server):
            res = stun_query(sock, (resolved, port))
            if res:
                results.append((time.monotonic() - t0, res, (host, port)))
                break
    if not results:
        return None, []
    results.sort(key=lambda r: r[0])
    best_ip_port = results[0][1]
    all_mappings = [r[1] for r in results]
    return best_ip_port, all_mappings


def detect_nat_type(sock: socket.socket, servers=None) -> str:
    """Грубая классификация NAT по RFC 3489-style сравнению отражённых адресов
    с разных STUN-серверов с одного и того же локального сокета.
      - 'open'      — внешний адрес == локальный (нет NAT / публичный IP)
      - 'cone'      — все серверы видят одинаковый внешний IP:port -> punching работает надёжно
      - 'symmetric' — внешний порт отличается между серверами -> punching ненадёжен, нужен relay
      - 'unknown'   — STUN недоступен (заблокирован файрволом/UDP)
    """
    local_ip = sock.getsockname()[0]
    best, mappings = best_reflexive_address(sock, servers, attempts_per_server=1)
    if not best:
        return "unknown"
    if best[0] == local_ip:
        return "open"
    unique = set(mappings)
    return "cone" if len(unique) <= 1 else "symmetric"
