import json
import os
import time
import threading

from . import minecraft

CONFIG_PATH  = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.json")
VCACHE_PATH  = os.path.join(os.path.dirname(os.path.dirname(__file__)), "versions_cache.json")


def _load_config() -> dict:
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return {
            "username": data.get("username", "Mark"),
            "version":  data.get("version", ""),
        }
    except (FileNotFoundError, json.JSONDecodeError):
        return {"username": "Mark", "version": ""}


def _save_config(username: str, version: str) -> None:
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"username": username, "version": version}, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_PATH)


def _get_versions_cached() -> list[str]:
    """Список версий с кэшем на диске. Если сеть упала — берём старый список."""
    try:
        versions = [v["id"] for v in minecraft.get_versions()]
        # сохраняем свежий список
        with open(VCACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(versions, f)
        return versions
    except Exception:
        # сеть упала — пробуем кэш
        try:
            with open(VCACHE_PATH, encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            # кэша тоже нет — возвращаем хоть что-то
            return ["1.21.1", "1.20.4", "1.20.1", "1.19.4", "1.18.2", "1.17.1", "1.16.5"]


class Api:
    def __init__(self):
        self.window = None

    def _emit(self, js_fn: str, *args):
        if self.window is None:
            return
        payload = ", ".join(json.dumps(a) for a in args)
        self.window.evaluate_js(f"{js_fn}({payload})")

    def list_versions(self) -> dict:
        cfg = _load_config()
        return {
            "versions":       _get_versions_cached(),
            "saved_username": cfg["username"],
            "saved_version":  cfg["version"],
        }

    def play(self, version_id: str, username: str) -> dict:
        if not username.strip():
            return {"ok": False, "error": "Vvedi nik"}
        threading.Thread(
            target=self._play_worker,
            args=(version_id, username.strip()),
            daemon=True,
        ).start()
        return {"ok": True}

    def _play_worker(self, version_id: str, username: str):
        try:
            _save_config(username, version_id)
            if not minecraft.is_installed(version_id):
                callback = {
                    "setStatus":   lambda text:  self._emit("onStatus", text),
                    "setProgress": lambda value: self._emit("onProgress", value),
                    "setMax":      lambda value: self._emit("onMax", value),
                }
                self._emit("onStatus", "Downloading " + version_id + "...")
                minecraft.install_version(version_id, callback)

            self._emit("onStatus", "Launching...")
            proc = minecraft.launch_offline(version_id, username)

            time.sleep(4)
            code = proc.poll()
            if code is not None:
                self._emit("onError", f"Game crashed (exit code {code}). Check Java version.")
                return

            self._emit("onLaunched")
        except Exception as e:
            self._emit("onError", str(e))
