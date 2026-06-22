"""
upnp.py — UPnP-пробрасывание порта через роутер.
Если роутер не поддерживает UPnP — пользователь делает вручную.
"""

import socket


def get_external_ip_fallback() -> str:
    """Берём внешний IP через публичный сервис."""
    import urllib.request
    try:
        with urllib.request.urlopen("https://api.ipify.org", timeout=5) as r:
            return r.read().decode().strip()
    except Exception:
        return ""


def open_port(port: int = 25565) -> tuple[str, str]:
    """
    Пробует открыть порт через UPnP.
    Возвращает (external_ip, method) где method = 'upnp' | 'manual'.
    Если UPnP не работает — возвращает внешний IP и инструкцию про ручной проброс.
    """
    try:
        import miniupnpc
        u = miniupnpc.UPnP()
        u.discoverdelay = 500
        found = u.discover()
        if found == 0:
            raise RuntimeError("UPnP устройства не найдены")
        u.selectigd()
        external_ip = u.externalipaddress()
        lan_ip = u.lanaddr or _local_ip()
        # добавляем маппинг (TCP)
        result = u.addportmapping(port, 'TCP', lan_ip, port, 'NOVA Launcher', '')
        if not result:
            raise RuntimeError("Не удалось добавить маппинг")
        return external_ip, "upnp"
    except Exception:
        # фолбэк — просто показываем внешний IP, пользователь сам прокидывает
        ext = get_external_ip_fallback()
        return ext, "manual"


def close_port(port: int = 25565) -> None:
    """Удаляет UPnP-маппинг при остановке сервера."""
    try:
        import miniupnpc
        u = miniupnpc.UPnP()
        u.discoverdelay = 300
        if u.discover() > 0:
            u.selectigd()
            u.deleteportmapping(port, 'TCP')
    except Exception:
        pass


def _local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"
