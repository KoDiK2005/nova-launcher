"""
Тесты backend/minecraft.py.

Регрессия: is_installed() возвращал True только по наличию <id>.json,
который minecraft_launcher_lib пишет до того, как скачивает client.jar.
Если скачка jar прерывалась (сеть/закрыли лаунчер), версия считалась
"установленной" навечно, install_version() больше не вызывался, и игра
падала при запуске с "Could not find or load main class ...
ClassNotFoundException" — classpath ссылался на несуществующий jar.
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import minecraft


class FakeVersionInfo(dict):
    """minecraft_launcher_lib возвращает TypedDict-подобные объекты с полем 'id'."""


class TestIsInstalled(unittest.TestCase):
    def _with_game_dir(self, tmp):
        return mock.patch.object(minecraft, "GAME_DIR", tmp)

    def test_false_when_not_in_installed_list(self):
        with mock.patch.object(minecraft.mll.utils, "get_installed_versions", return_value=[]):
            self.assertFalse(minecraft.is_installed("26.2"))

    def test_false_when_json_present_but_jar_missing(self):
        """Главная регрессия: json есть в списке minecraft_launcher_lib,
        но файла .jar физически нет — должно считаться НЕ установленным."""
        with tempfile.TemporaryDirectory() as tmp:
            version_dir = os.path.join(tmp, "versions", "26.2")
            os.makedirs(version_dir)
            with open(os.path.join(version_dir, "26.2.json"), "w") as f:
                f.write("{}")
            # .jar не создаём — имитируем прервавшуюся закачку

            with self._with_game_dir(tmp), \
                 mock.patch.object(minecraft.mll.utils, "get_installed_versions",
                                    return_value=[FakeVersionInfo(id="26.2")]):
                self.assertFalse(minecraft.is_installed("26.2"))

    def test_false_when_jar_exists_but_too_small(self):
        """Битая/недокачанная закачка часто оставляет крошечный файл вместо
        полноценного client.jar (десятки МБ) — это тоже не "установлено"."""
        with tempfile.TemporaryDirectory() as tmp:
            version_dir = os.path.join(tmp, "versions", "26.2")
            os.makedirs(version_dir)
            with open(os.path.join(version_dir, "26.2.jar"), "wb") as f:
                f.write(b"\x00" * 100)  # 100 байт — заведомо битый jar

            with self._with_game_dir(tmp), \
                 mock.patch.object(minecraft.mll.utils, "get_installed_versions",
                                    return_value=[FakeVersionInfo(id="26.2")]):
                self.assertFalse(minecraft.is_installed("26.2"))

    def test_true_when_jar_present_and_big_enough(self):
        with tempfile.TemporaryDirectory() as tmp:
            version_dir = os.path.join(tmp, "versions", "1.21.4")
            os.makedirs(version_dir)
            with open(os.path.join(version_dir, "1.21.4.jar"), "wb") as f:
                f.write(b"\x00" * (minecraft.MIN_CLIENT_JAR_SIZE + 1024))

            with self._with_game_dir(tmp), \
                 mock.patch.object(minecraft.mll.utils, "get_installed_versions",
                                    return_value=[FakeVersionInfo(id="1.21.4")]):
                self.assertTrue(minecraft.is_installed("1.21.4"))


if __name__ == "__main__":
    unittest.main()
