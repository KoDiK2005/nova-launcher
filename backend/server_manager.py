"""
server_manager.py — скачивание и запуск Minecraft-сервера.
"""

import os
import json
import socket
import subprocess
import threading

import requests

SERVER_DATA = os.path.join(os.path.dirname(os.path.dirname(__file__)), "server_data")
MANIFEST_URL = "https://launchermeta.mojang.com/mc/game/version_manifest_v2.json"

_instance_lock = threading.Lock()
_proc: subprocess.Popen | None = None
_running = False


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


# ─── Запуск / останов ─────────────────────────────────────────────────────────

def start(version_id: str, ram_mb: int, java_path: str,
          on_log, on_started, on_stopped, on_error):
    global _proc, _running
    if _running:
        on_error("Сервер уже запущен")
        return
    threading.Thread(
        target=_run_worker,
        args=(version_id, ram_mb, java_path, on_log, on_started, on_stopped, on_error),
        daemon=True
    ).start()


def _run_worker(version_id, ram_mb, java_path, on_log, on_started, on_stopped, on_error):
    global _proc, _running
    try:
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

        # принять EULA без лишних вопросов
        with open(os.path.join(srv_dir, "eula.txt"), "w") as f:
            f.write("eula=true\n")

        # offline-mode чтобы без MS аккаунта — важно!
        props_path = os.path.join(srv_dir, "server.properties")
        if not os.path.exists(props_path):
            with open(props_path, "w") as f:
                f.write("online-mode=false\n")
                f.write("motd=NOVA Launcher Server\n")
        else:
            # убедимся что online-mode=false
            with open(props_path, "r") as f:
                props = f.read()
            if "online-mode=true" in props:
                props = props.replace("online-mode=true", "online-mode=false")
                with open(props_path, "w") as f:
                    f.write(props)

        cmd = [java_path, f"-Xmx{ram_mb}M", "-Xms512M",
               "-jar", "server.jar", "--nogui"]

        flags = 0
        try:
            flags = subprocess.CREATE_NO_WINDOW  # Windows — не показывать чёрное окно
        except AttributeError:
            pass

        _proc = subprocess.Popen(
            cmd, cwd=srv_dir,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            creationflags=flags
        )
        _running = True
        on_started()

        # читаем лог в основном потоке
        for line in _proc.stdout:
            on_log(line.rstrip())

        _proc.wait()

    except Exception as e:
        on_error(str(e))
    finally:
        _running = False
        _proc = None
        on_stopped()


def stop():
    global _proc, _running
    if _proc and _running:
        try:
            _proc.stdin.write("stop\n")
            _proc.stdin.flush()
        except Exception:
            _proc.terminate()


def send_command(cmd: str):
    global _proc
    if _proc and _running:
        try:
            _proc.stdin.write(cmd + "\n")
            _proc.stdin.flush()
        except Exception:
            pass
