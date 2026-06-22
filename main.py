"""
main.py — точка входа. Создаёт окно лаунчера и связывает его с Python-логикой.
"""

import os

import webview

from backend.api import Api
from backend import server_manager

HERE = os.path.dirname(__file__)
UI_FILE = os.path.join(HERE, "ui", "index.html")


def main() -> None:
    api = Api()
    window = webview.create_window(
        title="NOVA Launcher",
        url=UI_FILE,
        js_api=api,
        width=1000,
        height=640,
        min_size=(820, 560),
        background_color="#0b0e16",
        resizable=True,
    )
    api.window = window

    def on_closing():
        """При закрытии окна уходим оффлайн в MQTT и гарантированно глушим
        сервер — иначе java-процесс остаётся осиротевшим и блокирует папку
        мира при следующем запуске лаунчера."""
        try:
            server_manager.shutdown_blocking()
        except Exception:
            pass
        try:
            api.clear_own_presence()
        except Exception:
            pass
        return True  # разрешаем закрытие

    window.events.closing += on_closing

    webview.start()


if __name__ == "__main__":
    main()
