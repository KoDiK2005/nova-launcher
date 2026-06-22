import json
import os
import time
import threading

import webview

from . import minecraft

ROOT = os.path.dirname(os.path.dirname(__file__))
CONFIG_PATH = os.path.join(ROOT, "config.json")
VCACHE_PATH = os.path.join(ROOT, "versions_cache.json")
AUTH_PATH   = os.path.join(ROOT, "auth.json")


# ─── config ───────────────────────────────────────────────────────────────────

def _load_config() -> dict:
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            d = json.load(f)
        return {"username": d.get("username", "Mark"), "version": d.get("version", ""), "loader": d.get("loader", "vanilla")}
    except (FileNotFoundError, json.JSONDecodeError):
        return {"username": "Mark", "version": "", "loader": "vanilla"}


def _save_config(username: str, version: str, loader: str = "vanilla") -> None:
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"username": username, "version": version, "loader": loader}, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_PATH)


# ─── versions cache ───────────────────────────────────────────────────────────

def _get_versions_cached() -> list[str]:
    try:
        versions = [v["id"] for v in minecraft.get_versions()]
        with open(VCACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(versions, f)
        return versions
    except Exception:
        try:
            with open(VCACHE_PATH, encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return ["1.21.1", "1.20.4", "1.20.1", "1.19.4", "1.18.2", "1.17.1", "1.16.5"]


# ─── auth ─────────────────────────────────────────────────────────────────────

def _load_auth() -> dict | None:
    try:
        with open(AUTH_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _save_auth(account: dict) -> None:
    tmp = AUTH_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(account, f, ensure_ascii=False, indent=2)
    os.replace(tmp, AUTH_PATH)


# ─── Api ──────────────────────────────────────────────────────────────────────

class Api:
    def __init__(self):
        self.window = None
        self._ms_state         = None
        self._ms_code_verifier = None

    def _emit(self, js_fn: str, *args):
        if self.window is None:
            return
        payload = ", ".join(json.dumps(a) for a in args)
        self.window.evaluate_js(f"{js_fn}({payload})")

    # --- загрузка данных для UI ---

    def list_versions(self) -> dict:
        cfg = _load_config()
        auth = _load_auth()
        return {
            "versions":       _get_versions_cached(),
            "saved_username": cfg["username"],
            "saved_version":  cfg["version"],
            "saved_loader":   cfg["loader"],
            "ms_account":     auth["name"] if auth else None,
        }

    def get_auth_status(self) -> dict:
        auth = _load_auth()
        if auth:
            return {"logged_in": True, "username": auth.get("name", ""), "uuid": auth.get("id", "")}
        return {"logged_in": False}

    # --- Microsoft auth ---

    def start_ms_login(self) -> dict:
        """Открыть окно входа Microsoft."""
        threading.Thread(target=self._ms_login_worker, daemon=True).start()
        return {"ok": True}

    def logout(self) -> dict:
        try:
            os.remove(AUTH_PATH)
        except FileNotFoundError:
            pass
        return {"ok": True}

    def _ms_login_worker(self):
        try:
            import minecraft_launcher_lib as mll

            login_url, state, code_verifier = mll.microsoft_account.get_login_url()
            self._ms_state         = state
            self._ms_code_verifier = code_verifier

            redirect_url = [None]
            auth_win = [None]

            def on_loaded():
                try:
                    url = auth_win[0].get_current_url() or ""
                    # Microsoft редиректит сюда после логина
                    if "oauth20_desktop.srf" in url and "code=" in url:
                        redirect_url[0] = url
                        auth_win[0].destroy()
                except Exception:
                    pass

            # Создаём окно входа — работает из фонового потока в pywebview 4.x
            win = webview.create_window(
                "Microsoft Login", login_url,
                width=500, height=680, resizable=False
            )
            auth_win[0] = win
            win.events.loaded += on_loaded

            # Ждём пока пользователь войдёт (до 5 минут)
            for _ in range(600):
                if redirect_url[0]:
                    break
                time.sleep(0.5)

            if not redirect_url[0]:
                self._emit("onMsError", "Login timeout (5 min)")
                return

            # Обмениваем код на токены
            login_data = mll.microsoft_account.get_secure_login_data(
                state, redirect_url[0], code_verifier
            )
            account = mll.microsoft_account.complete_login(
                login_data["access_token"], login_data["client_token"]
            )
            _save_auth(account)
            self._emit("onMsLoggedIn", account["name"], account["id"])

        except Exception as e:
            self._emit("onMsError", str(e))

    # --- запуск игры ---

    def play(self, version_id: str, username: str, loader: str = "vanilla", use_ms: bool = False) -> dict:
        if not use_ms and not username.strip():
            return {"ok": False, "error": "Введи ник"}
        threading.Thread(
            target=self._play_worker,
            args=(version_id, username.strip(), loader, use_ms),
            daemon=True,
        ).start()
        return {"ok": True}

    def _play_worker(self, version_id: str, username: str, loader: str, use_ms: bool):
        try:
            _save_config(username, version_id, loader)

            callback = {
                "setStatus":   lambda text:  self._emit("onStatus", text),
                "setProgress": lambda value: self._emit("onProgress", value),
                "setMax":      lambda value: self._emit("onMax", value),
            }

            if loader == "fabric":
                self._emit("onStatus", f"Preparing Fabric for {version_id}...")
                launch_id = minecraft.install_fabric(version_id, callback)
            else:
                if not minecraft.is_installed(version_id):
                    self._emit("onStatus", f"Downloading {version_id}...")
                    minecraft.install_version(version_id, callback)
                launch_id = version_id

            self._emit("onStatus", "Launching...")

            if use_ms:
                auth = _load_auth()
                if not auth:
                    self._emit("onError", "Not logged in to Microsoft")
                    return
                proc = minecraft.launch_authenticated(
                    launch_id, auth["name"], auth["id"], auth["access_token"]
                )
            else:
                proc = minecraft.launch_offline(launch_id, username)

            time.sleep(4)
            if proc.poll() is not None:
                self._emit("onError", f"Game crashed (exit code {proc.poll()})")
                return

            self._emit("onLaunched")

        except Exception as e:
            self._emit("onError", str(e))
