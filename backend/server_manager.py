"""
server_manager.py — скачивание и запуск Minecraft-сервера.

Важные инварианты, нарушение которых ловило баг "сервер не запускается /
файл заблокирован другим процессом":
  - на один SERVER_DATA/<version> может существовать только один живой
    java-процесс одновременно — это гарантируется _instance_lock и флагом
    _starting, который выставляется ДО старта фонового потока (а не внутри
    него — иначе между двумя быстрыми кликами "Хостить" успевали проскочить
    два потока, оба видели _running == False и оба запускали java на одну и
    ту же папку мира -> второй процесс падал с IOException на session.lock)
  - stop() ждёт реального завершения процесса (а не просто отправляет
    команду "stop" и сразу возвращается) — иначе мир остаётся залоченным
    ещё какое-то время после того как UI уже показал "Остановлен"
  - PID активного процесса сохраняется в pid-файл; при следующем запуске
    лаунчер проверяет, не висит ли с прошлого раза osирotевший java-процесс
    (например лаунчер закрыли крестиком, не дождавшись остановки сервера) —
    и завершает его перед тем как поднимать новый, чтобы не ловить тот же
    file-lock конфликт.
"""

import os
import json
import socket
import subprocess
import threading
import time

import requests

SERVER_DATA = os.path.join(os.path.dirname(os.path.dirname(__file__)), "server_data")
MANIFEST_URL = "https://launchermeta.mojang.com/mc/game/version_manifest_v2.json"

STOP_TIMEOUT = 15.0   # сколько ждём корректной остановки до force-kill

DEFAULT_SERVER_SETTINGS = {
    "motd":          "NOVA Launcher Server",
    "gamemode":      "survival",     # survival | creative | adventure | spectator
    "difficulty":    "easy",         # peaceful | easy | normal | hard
    "max_players":   20,
    "pvp":           True,
    "hardcore":      False,
    "level_seed":    "",
    "view_distance": 10,
    "white_list":    False,
}

_instance_lock = threading.Lock()
_proc: subprocess.Popen | None = None
_running = False
_starting = False
_current_version: str | None = None
# Если stop() сам подтвердил смерть процесса (force-kill/taskkill), он
# финализирует состояние немедленно, не дожидаясь фонового _run_worker —
# на некоторых JVM после жёсткого килла поток, блокированный на чтении
# stdout, может не увидеть EOF ещё некоторое время (флаки Windows-пайпов),
# и без этого флага is_running() лгал бы "True" уже после успешного stop().
_finalized_externally = False
_cb_on_stopped = None   # колбэк текущей сессии — нужен stop(), если он финализирует сам


# ─── Утилиты ──────────────────────────────────────────────────────────────────

def get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def is_running() -> bool:
    return _running


def is_starting() -> bool:
    return _starting


def normalize_settings(settings: dict | None) -> dict:
    merged = dict(DEFAULT_SERVER_SETTINGS)
    if settings:
        for k in DEFAULT_SERVER_SETTINGS:
            if k in settings and settings[k] is not None:
                merged[k] = settings[k]
    merged["max_players"] = max(1, min(200, int(merged["max_players"])))
    merged["view_distance"] = max(3, min(32, int(merged["view_distance"])))
    if merged["difficulty"] not in ("peaceful", "easy", "normal", "hard"):
        merged["difficulty"] = "easy"
    if merged["gamemode"] not in ("survival", "creative", "adventure", "spectator"):
        merged["gamemode"] = "survival"
    return merged


def _get_server_url(version_id: str) -> str:
    manifest = requests.get(MANIFEST_URL, timeout=10).json()
    for v in manifest["versions"]:
        if v["id"] == version_id and v["type"] == "release":
            vdata = requests.get(v["url"], timeout=10).json()
            return vdata["downloads"]["server"]["url"]
    raise ValueError(f"Версия {version_id} не найдена или не является релизом")


def _download_jar(version_id: str, on_progress=None) -> str:
    os.makedirs(SERVER_DATA, exist_ok=True)
    jar_path = os.path.join(SERVER_DATA, f"server-{version_id}.jar")
    if os.path.exists(jar_path):
        return jar_path
    url = _get_server_url(version_id)
    r = requests.get(url, stream=True, timeout=60)
    r.raise_for_status()  # бросит исключение если HTTP-ошибка (4xx/5xx)
    total = int(r.headers.get("content-length", 0))
    done = 0
    with open(jar_path + ".tmp", "wb") as f:
        for chunk in r.iter_content(65536):
            f.write(chunk)
            done += len(chunk)
            if on_progress and total:
                on_progress(done, total)
    os.replace(jar_path + ".tmp", jar_path)
    return jar_path


def _write_server_properties(srv_dir: str, settings: dict) -> None:
    """Полностью перезаписывает server.properties на основе настроек лаунчера.
    Источник правды — config.json, а не файл (если игрок поменял что-то
    командой в игре, при следующем запуске это будет перезатёрто настройками
    из лаунчера — это осознанный трейдофф ради предсказуемости UI)."""
    props = {
        "motd":            settings["motd"],
        "gamemode":        settings["gamemode"],
        "difficulty":      settings["difficulty"],
        "max-players":     str(settings["max_players"]),
        "pvp":             "true" if settings["pvp"] else "false",
        "hardcore":        "true" if settings["hardcore"] else "false",
        "level-seed":      settings["level_seed"],
        "view-distance":   str(settings["view_distance"]),
        "white-list":      "true" if settings["white_list"] else "false",
        "online-mode":     "false",   # обязательно — без него не зайти offline-аккаунтом
        "server-port":     "25565",
        "level-name":      "world",
        "enable-command-block": "true",
    }
    path = os.path.join(srv_dir, "server.properties")
    existing: dict[str, str] = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                existing[k] = v
    existing.update(props)
    with open(path, "w", encoding="utf-8") as f:
        for k, v in sorted(existing.items()):
            f.write(f"{k}={v}\n")


def _pid_file(version_id: str) -> str:
    return os.path.join(SERVER_DATA, version_id, ".server.pid")


def _is_process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid
            )
            if not handle:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        else:
            os.kill(pid, 0)
            return True
    except (OSError, AttributeError):
        return False


def cleanup_orphan(version_id: str, on_log=None) -> bool:
    """Если с прошлого запуска (например лаунчер закрыли без штатной
    остановки сервера) остался живой java-процесс на эту версию — убивает
    его, чтобы не ловить file-lock на world. Возвращает True, если что-то
    почистил."""
    pid_path = _pid_file(version_id)
    if not os.path.exists(pid_path):
        return False
    try:
        with open(pid_path, encoding="utf-8") as f:
            pid = int(f.read().strip())
    except (ValueError, OSError):
        try:
            os.remove(pid_path)
        except OSError:
            pass
        return False

    cleaned = False
    if _is_process_alive(pid):
        if on_log:
            on_log(f"⚠ Найден незавершённый процесс сервера (PID {pid}) — закрываю...")
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/F", "/T"],
                    capture_output=True, timeout=10,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            else:
                os.kill(pid, 9)
            cleaned = True
        except Exception:
            pass
        time.sleep(0.5)
    try:
        os.remove(pid_path)
    except OSError:
        pass

    if sweep_stray_processes():
        cleaned = True
    return cleaned


def sweep_stray_processes() -> bool:
    """Находит и убивает ЛЮБОЙ java-процесс, чья командная строка похожа на
    наш сервер (-jar server.jar --nogui), независимо от PID.

    Зачем это нужно при том что PID уже отслеживается через pid-файл: на
    части систем "java" в PATH — это редиректор (например
    javapath\\java.exe от инсталлятора Oracle), который запускает настоящую
    JVM КАК ОТДЕЛЬНЫЙ дочерний процесс. Если редиректор завершается раньше
    своего потомка (или ОС успевает переродить процесс другому предку),
    обычный kill()/taskkill по отслеженному PID убивает только редиректор,
    а настоящая JVM остаётся жить и держит файловый лок на world — именно
    так ловился баг "файл заблокирован другим процессом" при следующем
    запуске. Так как лаунчер поддерживает только один работающий сервер
    одновременно (глобальный _running), безопасно считать совпадающим по
    командной строке процессом именно "наш" сервер."""
    if os.name != "nt":
        return False
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='java.exe'\" | "
             "Where-Object { $_.CommandLine -like '*server.jar*--nogui*' } | "
             "ForEach-Object { Stop-Process -Id $_.ProcessId -Force; "
             "Write-Output $_.ProcessId }"],
            capture_output=True, text=True, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return bool(result.stdout.strip())
    except (subprocess.TimeoutExpired, OSError):
        return False


# ─── Запуск / останов ─────────────────────────────────────────────────────────

def start(version_id: str, ram_mb: int, java_path: str,
          on_log, on_started, on_stopped, on_error, settings: dict | None = None):
    global _starting, _finalized_externally, _cb_on_stopped
    with _instance_lock:
        if _running or _starting:
            on_error("Сервер уже запущен")
            return
        _starting = True
        _finalized_externally = False
        _cb_on_stopped = on_stopped

    threading.Thread(
        target=_run_worker,
        args=(version_id, ram_mb, java_path, on_log, on_started, on_stopped, on_error,
              normalize_settings(settings)),
        daemon=True
    ).start()


def _run_worker(version_id, ram_mb, java_path, on_log, on_started, on_stopped, on_error, settings):
    global _proc, _running, _starting, _current_version
    had_error = False
    try:
        cleanup_orphan(version_id, on_log)

        srv_dir = os.path.join(SERVER_DATA, version_id)
        os.makedirs(srv_dir, exist_ok=True)

        jar_src = os.path.join(SERVER_DATA, f"server-{version_id}.jar")
        if not os.path.exists(jar_src):
            on_log(f"⬇ Скачиваю сервер {version_id}...")
            def prog(done, total):
                on_log(f"  {done // 1024 // 1024} / {total // 1024 // 1024} MB")
            _download_jar(version_id, prog)

        import shutil
        jar_dst = os.path.join(srv_dir, "server.jar")
        if not os.path.exists(jar_dst):
            shutil.copy(jar_src, jar_dst)

        with open(os.path.join(srv_dir, "eula.txt"), "w") as f:
            f.write("eula=true\n")

        _write_server_properties(srv_dir, settings)

        cmd = [java_path, f"-Xmx{ram_mb}M", "-Xms512M",
               "-jar", "server.jar", "--nogui"]

        flags = 0
        try:
            flags = subprocess.CREATE_NO_WINDOW  # Windows — не показывать чёрное окно
        except AttributeError:
            pass

        proc = subprocess.Popen(
            cmd, cwd=srv_dir,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            creationflags=flags
        )
        with _instance_lock:
            _proc = proc
            _running = True
            _starting = False
            _current_version = version_id
        try:
            with open(_pid_file(version_id), "w", encoding="utf-8") as f:
                f.write(str(proc.pid))
        except OSError:
            pass
        on_started()

        for line in proc.stdout:
            on_log(line.rstrip())
            if "Failed to start the minecraft server" in line:
                had_error = True

        proc.wait()
        if proc.returncode not in (0, None) and not had_error:
            had_error = True
            on_error(f"Сервер завершился с кодом {proc.returncode} (см. лог)")

    except Exception as e:
        had_error = True
        on_error(str(e))
    finally:
        with _instance_lock:
            already_finalized = _finalized_externally
            if not already_finalized:
                _running = False
                _starting = False
                _proc = None
                _current_version = None
        if not already_finalized:
            try:
                os.remove(_pid_file(version_id))
            except OSError:
                pass
            if not had_error:
                on_stopped()


def stop(timeout: float = STOP_TIMEOUT) -> bool:
    """Останавливает сервер и ЖДЁТ реального завершения процесса (иначе мир
    остаётся залоченным ещё некоторое время после возврата из функции).
    Возвращает True если сервер завершился штатно/принудительно за timeout.

    На практике встречаются JVM, которые не реагируют на обычный kill()
    (зависший shutdown hook / JIT-компиляция в моменте остановки) — Popen.kill()
    в редких случаях не успевает завершить процесс за разумное время, хотя
    тот же сигнал через taskkill /F /T отрабатывает мгновенно. Поэтому
    добавлена финальная эскалация через taskkill перед тем как сдаться.

    Как только смерть процесса подтверждена — состояние финализируется
    ЗДЕСЬ ЖЕ, а не оставляется фоновому _run_worker: после force-kill поток,
    блокированный на чтении stdout, может не увидеть EOF ещё какое-то время
    (флаки Windows-пайпов), и без явной финализации is_running() лгал бы
    "True" уже после успешного stop() — следующий start() ловил бы ложное
    "уже запущен"."""
    global _proc, _running, _starting, _current_version, _finalized_externally
    with _instance_lock:
        proc = _proc
        running = _running
        version_id = _current_version
    if not (proc and running):
        return True

    try:
        proc.stdin.write("stop\n")
        proc.stdin.flush()
    except Exception:
        proc.terminate()

    died = False
    try:
        proc.wait(timeout=timeout)
        died = True
    except subprocess.TimeoutExpired:
        pass

    if not died:
        proc.kill()
        try:
            proc.wait(timeout=5)
            died = True
        except subprocess.TimeoutExpired:
            pass

    if not died and os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/F", "/T"],
                capture_output=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            proc.wait(timeout=5)
            died = True
        except (subprocess.TimeoutExpired, OSError):
            pass

    if died:
        with _instance_lock:
            _running = False
            _starting = False
            _proc = None
            _current_version = None
            _finalized_externally = True
            cb = _cb_on_stopped
        if version_id:
            try:
                os.remove(_pid_file(version_id))
            except OSError:
                pass
        # Подчищаем возможных "осиротевших внуков" от java-редиректора —
        # см. docstring sweep_stray_processes(). Не блокирует возврат UI
        # надолго (до ~15с в худшем случае), но гарантирует чистый мир
        # к моменту следующего запуска.
        sweep_stray_processes()
        if cb:
            cb()
    return died


def send_command(cmd: str):
    with _instance_lock:
        proc = _proc
        running = _running
    if proc and running:
        try:
            proc.stdin.write(cmd + "\n")
            proc.stdin.flush()
        except Exception:
            pass


def shutdown_blocking():
    """Вызывается при закрытии лаунчера — гарантирует, что java-процесс не
    останется висеть осиротевшим (та самая причина file-lock конфликтов при
    следующем запуске)."""
    stop(timeout=10.0)
