"""
minecraft.py — вся логика работы с самой игрой.

Идея модуля: тут НЕТ интерфейса. Только функции, которые умеют:
  1) сказать, какие версии существуют;
  2) скачать (установить) нужную версию;
  3) собрать команду запуска и стартануть игру в offline-режиме.

UI (pywebview) дёргает эти функции и не знает, КАК устроен Minecraft внутри.
Разделение ответственности: морда отдельно, логика отдельно.
"""

import hashlib
import os
import subprocess
import uuid

import minecraft_launcher_lib as mll

# Куда складываем игру. Не трогаем системный .minecraft, чтобы не мешать
# обычному лаунчеру. Своя папка рядом с проектом (она в .gitignore).
GAME_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "game_data")


def get_versions() -> list[dict]:
    """Список доступных версий Minecraft (только релизы, снапшоты отсекаем).
    При отсутствии сети возвращает только уже установленные версии."""
    try:
        all_versions = mll.utils.get_version_list()
    except Exception:
        all_versions = mll.utils.get_installed_versions(GAME_DIR)
    return [v for v in all_versions if v["type"] == "release"]


def _offline_uuid(username: str) -> str:
    """Сгенерить offline-UUID так же, как это делает сам Minecraft.

    В offline-режиме игра берёт md5 от строки "OfflinePlayer:<ник>" и
    превращает в UUID версии 3. Благодаря этому ник всегда даёт ОДИН и тот же
    uuid → миры, инвентарь и скин-привязка стабильны между запусками.
    """
    data = hashlib.md5(f"OfflinePlayer:{username}".encode("utf-8")).digest()
    return str(uuid.UUID(bytes=data, version=3))


def is_installed(version_id: str) -> bool:
    """Уже скачана ли версия (чтобы не качать повторно)."""
    installed = {v["id"] for v in mll.utils.get_installed_versions(GAME_DIR)}
    return version_id in installed


def install_version(version_id: str, callback: dict | None = None) -> None:
    """Скачать и установить версию по id ('1.20.4').

    callback — словарь с функциями setStatus(str)/setProgress(int)/setMax(int).
    minecraft-launcher-lib дёргает их во время загрузки → так мы рисуем
    полоску DOWNLOADING. Если callback не передали — качаем молча.
    """
    os.makedirs(GAME_DIR, exist_ok=True)
    mll.install.install_minecraft_version(version_id, GAME_DIR, callback=callback or {})


def launch_offline(version_id: str, username: str) -> subprocess.Popen:
    """Запустить игру в offline-режиме под ником username.

    1. options — три поля, которые нужны для offline:
       token пустой (лицензию не проверяем), uuid — стабильный из ника.
    2. get_minecraft_command — собирает готовый список аргументов для java.
    3. Popen — запускает java в отдельном процессе и НЕ блокирует лаунчер.
    """
    options = {
        "username": username,
        "uuid": _offline_uuid(username),
        "token": "",
    }
    command = mll.command.get_minecraft_command(version_id, GAME_DIR, options)
    return subprocess.Popen(command)


if __name__ == "__main__":
    # Ручной тест без UI. Скачает версию (может висеть пару минут — это норма).
    test_version = "1.20.4"
    print("Последние релизы:", [v["id"] for v in get_versions()[:5]])
    if not is_installed(test_version):
        print(f"Качаю {test_version}...")
        install_version(test_version)
    print(f"Запускаю {test_version}...")
    launch_offline(test_version, "Mark")
    print("Готово — окно Minecraft должно открыться.")
