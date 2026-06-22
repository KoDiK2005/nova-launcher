"""
Тесты backend/server_manager.py.

Покрывают регрессию, из-за которой сервер падал с
"IOException: файл заблокирован другим процессом":
  - start() не должен давать запустить второй процесс на ту же версию,
    пока первый ещё стартует/работает (race condition между двумя кликами
    "Хостить" до того как _running успевал выставиться)
  - cleanup_orphan() корректно убирает протухший pid-файл и убивает
    осиротевший процесс с прошлого запуска
  - normalize_settings() даёт безопасные значения для server.properties
  - _write_server_properties() детерминирована и не теряет произвольные
    ключи, выставленные пользователем вручную
"""

import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import server_manager as sm


class ResetStateMixin:
    """Сбрасывает модульные глобалы server_manager перед/после каждого теста,
    иначе тесты влияют друг на друга через общий модульный стейт."""

    def setUp(self):
        sm._proc = None
        sm._running = False
        sm._starting = False
        sm._current_version = None
        sm._finalized_externally = False
        sm._cb_on_stopped = None

    def tearDown(self):
        sm._proc = None
        sm._running = False
        sm._starting = False
        sm._current_version = None
        sm._finalized_externally = False
        sm._cb_on_stopped = None


class TestNormalizeSettings(unittest.TestCase):
    def test_defaults_when_none(self):
        s = sm.normalize_settings(None)
        self.assertEqual(s, sm.DEFAULT_SERVER_SETTINGS)

    def test_partial_overrides_merge_with_defaults(self):
        s = sm.normalize_settings({"motd": "Hi"})
        self.assertEqual(s["motd"], "Hi")
        self.assertEqual(s["gamemode"], "survival")  # дефолт сохранился

    def test_max_players_clamped(self):
        self.assertEqual(sm.normalize_settings({"max_players": 0})["max_players"], 1)
        self.assertEqual(sm.normalize_settings({"max_players": 99999})["max_players"], 200)
        self.assertEqual(sm.normalize_settings({"max_players": 30})["max_players"], 30)

    def test_view_distance_clamped(self):
        self.assertEqual(sm.normalize_settings({"view_distance": 0})["view_distance"], 3)
        self.assertEqual(sm.normalize_settings({"view_distance": 999})["view_distance"], 32)

    def test_invalid_difficulty_falls_back(self):
        self.assertEqual(sm.normalize_settings({"difficulty": "nightmare"})["difficulty"], "easy")

    def test_invalid_gamemode_falls_back(self):
        self.assertEqual(sm.normalize_settings({"gamemode": "godmode"})["gamemode"], "survival")

    def test_none_values_ignored_uses_default(self):
        s = sm.normalize_settings({"motd": None, "pvp": None})
        self.assertEqual(s["motd"], sm.DEFAULT_SERVER_SETTINGS["motd"])
        self.assertEqual(s["pvp"], sm.DEFAULT_SERVER_SETTINGS["pvp"])


class TestWriteServerProperties(unittest.TestCase):
    def test_creates_file_with_expected_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = sm.normalize_settings({"motd": "Test Server", "max_players": 5})
            sm._write_server_properties(tmp, settings)
            path = os.path.join(tmp, "server.properties")
            self.assertTrue(os.path.exists(path))
            with open(path, encoding="utf-8") as f:
                content = f.read()
            self.assertIn("motd=Test Server", content)
            self.assertIn("max-players=5", content)
            self.assertIn("online-mode=false", content)

    def test_preserves_unknown_existing_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "server.properties")
            with open(path, "w", encoding="utf-8") as f:
                f.write("rcon.password=secret123\nmotd=Old\n")
            sm._write_server_properties(tmp, sm.normalize_settings({"motd": "New"}))
            with open(path, encoding="utf-8") as f:
                content = f.read()
            self.assertIn("rcon.password=secret123", content)  # не потеряли
            self.assertIn("motd=New", content)                 # перезаписали

    def test_difficulty_and_gamemode_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = sm.normalize_settings({"difficulty": "hard", "gamemode": "creative"})
            sm._write_server_properties(tmp, settings)
            with open(os.path.join(tmp, "server.properties"), encoding="utf-8") as f:
                content = f.read()
            self.assertIn("difficulty=hard", content)
            self.assertIn("gamemode=creative", content)


class TestCleanupOrphan(ResetStateMixin, unittest.TestCase):
    def test_no_pidfile_returns_false(self):
        with mock.patch.object(sm, "SERVER_DATA", tempfile.mkdtemp()):
            self.assertFalse(sm.cleanup_orphan("1.21.4"))

    def test_dead_pid_removes_file_and_returns_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(sm, "SERVER_DATA", tmp), \
                 mock.patch.object(sm, "sweep_stray_processes", return_value=False):
                version_dir = os.path.join(tmp, "1.21.4")
                os.makedirs(version_dir)
                pid_path = sm._pid_file("1.21.4")
                # Заведомо несуществующий PID (очень большое число)
                with open(pid_path, "w") as f:
                    f.write("999999999")
                result = sm.cleanup_orphan("1.21.4")
                self.assertFalse(result)
                self.assertFalse(os.path.exists(pid_path))

    def test_corrupt_pidfile_is_removed_safely(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(sm, "SERVER_DATA", tmp):
                version_dir = os.path.join(tmp, "1.21.4")
                os.makedirs(version_dir)
                pid_path = sm._pid_file("1.21.4")
                with open(pid_path, "w") as f:
                    f.write("not-a-pid")
                # Сюда sweep не доходит — функция возвращается раньше при
                # ValueError на парсинге pid-файла.
                result = sm.cleanup_orphan("1.21.4")
                self.assertFalse(result)
                self.assertFalse(os.path.exists(pid_path))

    def test_cleanup_orphan_sweeps_stray_grandchildren_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(sm, "SERVER_DATA", tmp), \
                 mock.patch.object(sm, "sweep_stray_processes", return_value=True) as sweep_mock:
                version_dir = os.path.join(tmp, "1.21.4")
                os.makedirs(version_dir)
                with open(sm._pid_file("1.21.4"), "w") as f:
                    f.write("999999999")  # мёртвый PID — основная ветка ничего не убьёт
                result = sm.cleanup_orphan("1.21.4")
        sweep_mock.assert_called_once()
        self.assertTrue(result)  # cleaned=True благодаря sweep, хотя PID-ветка вернула False

    def test_alive_pid_is_killed(self):
        # Текущий процесс точно жив — используем его как "осиротевший",
        # но убиваем не его, а просто проверяем что _is_process_alive
        # отрабатывает корректно для живого PID (kill реального теста не
        # делаем, чтобы не убить тестовый раннер).
        self.assertTrue(sm._is_process_alive(os.getpid()))

    def test_is_process_alive_false_for_bogus_pid(self):
        self.assertFalse(sm._is_process_alive(999999999))


class TestStopEscalation(ResetStateMixin, unittest.TestCase):
    """Регрессия: на реальном сервере наблюдалось, что Popen.kill() сам по
    себе не всегда укладывается в таймаут (зависший shutdown hook JVM), хотя
    taskkill /F /T убивает процесс мгновенно. stop() должен эскалировать
    до taskkill, а не сдаваться после одного неудачного kill()."""

    def _fake_proc(self, wait_side_effects):
        proc = mock.Mock()
        proc.stdin = mock.Mock()
        proc.pid = 4242
        proc.wait = mock.Mock(side_effect=wait_side_effects)
        return proc

    def test_clean_stop_within_timeout(self):
        proc = self._fake_proc([None])  # первый wait() сразу успешен
        sm._proc, sm._running = proc, True
        with mock.patch.object(sm, "sweep_stray_processes", return_value=False):
            self.assertTrue(sm.stop(timeout=1))
        proc.kill.assert_not_called()

    def test_escalates_to_kill_then_taskkill(self):
        proc = self._fake_proc([
            sm.subprocess.TimeoutExpired(cmd="x", timeout=1),  # обычный wait не успел
            sm.subprocess.TimeoutExpired(cmd="x", timeout=5),  # kill() тоже не успел
            None,                                              # taskkill сработал
        ])
        sm._proc, sm._running = proc, True
        with mock.patch.object(sm.subprocess, "run") as run_mock, \
             mock.patch.object(sm, "sweep_stray_processes", return_value=False):
            result = sm.stop(timeout=1)
        self.assertTrue(result)
        proc.kill.assert_called_once()
        run_mock.assert_called_once()
        self.assertIn("taskkill", run_mock.call_args[0][0])

    def test_returns_false_if_even_taskkill_fails(self):
        proc = self._fake_proc([
            sm.subprocess.TimeoutExpired(cmd="x", timeout=1),
            sm.subprocess.TimeoutExpired(cmd="x", timeout=5),
            sm.subprocess.TimeoutExpired(cmd="x", timeout=5),
        ])
        sm._proc, sm._running = proc, True
        with mock.patch.object(sm.subprocess, "run"):
            result = sm.stop(timeout=1)
        self.assertFalse(result)

    def test_sweeps_stray_grandchild_processes_after_death_confirmed(self):
        """Регрессия: java-редиректор в PATH (например javapath\\java.exe)
        запускает настоящую JVM отдельным процессом — kill() по
        отслеженному PID убивает только редиректор. stop() должен дополнительно
        подчищать любые похожие java-процессы по командной строке."""
        proc = self._fake_proc([None])
        sm._proc, sm._running = proc, True
        with mock.patch.object(sm, "sweep_stray_processes", return_value=True) as sweep_mock:
            sm.stop(timeout=1)
        sweep_mock.assert_called_once()

    def test_stop_is_noop_when_not_running(self):
        sm._proc, sm._running = None, False
        self.assertTrue(sm.stop(timeout=1))

    def test_stop_finalizes_state_itself_without_waiting_for_worker(self):
        """Регрессия: после успешного stop() is_running() должен сразу
        стать False, даже если фоновый _run_worker (которого тут нет —
        чистый юнит-тест) ещё не успел бы это сделать сам."""
        proc = self._fake_proc([None])
        sm._proc, sm._running, sm._starting = proc, True, True
        sm._current_version = "1.21.4"
        with mock.patch.object(sm, "sweep_stray_processes", return_value=False):
            self.assertTrue(sm.stop(timeout=1))
        self.assertFalse(sm.is_running())
        self.assertFalse(sm.is_starting())
        self.assertIsNone(sm._proc)
        self.assertTrue(sm._finalized_externally)

    def test_stop_invokes_on_stopped_callback_registered_by_start(self):
        proc = self._fake_proc([None])
        sm._proc, sm._running = proc, True
        called = []
        sm._cb_on_stopped = lambda: called.append(True)
        with mock.patch.object(sm, "sweep_stray_processes", return_value=False):
            sm.stop(timeout=1)
        self.assertEqual(called, [True])


class TestConcurrentStartRace(ResetStateMixin, unittest.TestCase):
    """Главная регрессия: два почти одновременных start() не должны оба
    пройти проверку и одновременно полезть в _run_worker."""

    def test_second_start_rejected_while_first_is_starting(self):
        release_worker = threading.Event()
        errors = []

        def fake_run_worker(*args, **kwargs):
            # имитируем "тяжёлую" работу (скачивание/запуск) — держим
            # _starting=True, пока второй start() не попробует влезть
            release_worker.wait(timeout=2)

        with mock.patch.object(sm, "_run_worker", side_effect=fake_run_worker):
            sm.start("1.21.4", 1024, "java",
                     on_log=lambda *a: None, on_started=lambda: None,
                     on_stopped=lambda: None, on_error=lambda m: None)
            # Дожидаемся, чтобы фоновый поток реально вошёл в _starting=True
            for _ in range(50):
                if sm._starting:
                    break
                time.sleep(0.02)
            self.assertTrue(sm._starting, "первый start() должен выставить _starting=True")

            sm.start("1.21.4", 1024, "java",
                     on_log=lambda *a: None, on_started=lambda: None,
                     on_stopped=lambda: None, on_error=lambda m: errors.append(m))

        release_worker.set()
        time.sleep(0.1)
        self.assertEqual(len(errors), 1)
        self.assertIn("уже запущен", errors[0])

    def test_start_rejected_while_running(self):
        sm._running = True
        errors = []
        sm.start("1.21.4", 1024, "java",
                 on_log=lambda *a: None, on_started=lambda: None,
                 on_stopped=lambda: None, on_error=lambda m: errors.append(m))
        self.assertEqual(errors, ["Сервер уже запущен"])


if __name__ == "__main__":
    unittest.main()
