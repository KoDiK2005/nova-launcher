import json
import os
import time
import threading

import webview

from . import minecraft
from . import server_manager
from . import upnp
from . import relay

ROOT        = os.path.dirname(os.path.dirname(__file__))
CONFIG_PATH = os.path.join(ROOT, "config.json")
VCACHE_PATH = os.path.join(ROOT, "versions_cache.json")
AUTH_PATH   = os.path.join(ROOT, "auth.json")

MS_CLIENT_ID    = "00000000402b5328"
MS_REDIRECT_URI = "https://login.live.com/oauth20_desktop.srf"

DEFAULTS = {
    "username":       "Mark",
    "version":        "",
    "loader":         "vanilla",
    "ram":            2048,
    "width":          854,
    "height":         480,
    "servers":        [],
    "friends":        [],
    "java_path":      "java",
    "jvm_extra":      "",
    "server_version": "",
    "server_ram":     1024,
}


def _load_config() -> dict:
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            d = json.load(f)
        return {k: d.get(k, v) for k, v in DEFAULTS.items()}
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(DEFAULTS)


def _save_config(data: dict):
    current = _load_config()
    current.update(data)
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(current, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_PATH)


def _get_versions_cached():
    FALLBACK = ["1.21.4", "1.21.1", "1.20.4", "1.20.1", "1.19.4", "1.18.2", "1.17.1", "1.16.5"]
    try:
        versions = [v["id"] for v in minecraft.get_versions()]
        if versions:
            with open(VCACHE_PATH, "w", encoding="utf-8") as f:
                json.dump(versions, f)
            return versions
    except Exception:
        pass
    # Сеть упала или список пустой — читаем кэш
    try:
        with open(VCACHE_PATH, encoding="utf-8") as f:
            cached = json.load(f)
            if cached:
                return cached
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return FALLBACK


def _load_auth():
    try:
        with open(AUTH_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _save_auth(account):
    tmp = AUTH_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(account, f, ensure_ascii=False, indent=2)
    os.replace(tmp, AUTH_PATH)


class Api:
    def __init__(self):
        self.window = None
        self._idle_timer = None
        self._current_username = ""

    def _emit(self, js_fn, *args):
        if self.window is None:
            return
        payload = ", ".join(json.dumps(a) for a in args)
        self.window.evaluate_js(f"{js_fn}({payload})")

    # --- Presence / статус активности -----------------------------------------

    def go_online(self):
        cfg = _load_config()
        self._current_username = cfg["username"]
        relay.publish_presence(self._current_username, "online")
        self._reset_idle_timer()
        try:
            minecraft.sync_servers_dat(cfg["servers"])
        except Exception:
            pass
        return {"ok": True}

    def keep_alive(self):
        if self._current_username:
            relay.publish_presence(self._current_username, "online")
            self._reset_idle_timer()
        return {"ok": True}

    def _reset_idle_timer(self):
        if self._idle_timer:
            self._idle_timer.cancel()
        if self._current_username:
            self._idle_timer = threading.Timer(600, self._go_idle)
            self._idle_timer.daemon = True
            self._idle_timer.start()

    def _go_idle(self):
        if self._current_username:
            relay.publish_presence(self._current_username, "idle")

    def _monitor_game(self, proc, username):
        proc.wait()
        relay.publish_presence(username, "online")
        self._reset_idle_timer()
        self._emit("onGameClosed")

    def clear_own_presence(self):
        if self._idle_timer:
            self._idle_timer.cancel()
        if self._current_username:
            relay.clear_presence(self._current_username)
            relay.clear_session(self._current_username)
        relay.disconnect()

    # --- Версии / конфиг ------------------------------------------------------

    def list_versions(self):
        cfg  = _load_config()
        auth = _load_auth()
        return {
            "versions":        _get_versions_cached(),
            "saved_username":  cfg["username"],
            "saved_version":   cfg["version"],
            "saved_loader":    cfg["loader"],
            "saved_ram":       cfg["ram"],
            "saved_width":     cfg["width"],
            "saved_height":    cfg["height"],
            "saved_servers":   cfg["servers"],
            "saved_friends":   cfg["friends"],
            "saved_java":      cfg["java_path"],
            "saved_jvm_extra": cfg["jvm_extra"],
            "saved_srv_ver":   cfg["server_version"],
            "saved_srv_ram":   cfg["server_ram"],
            "ms_account":      auth["name"] if auth else None,
        }

    # --- Настройки ------------------------------------------------------------

    def save_settings(self, java_path, jvm_extra, ram, width, height):
        _save_config({
            "java_path": java_path,
            "jvm_extra": jvm_extra,
            "ram":       int(ram),
            "width":     int(width),
            "height":    int(height),
        })
        return {"ok": True}

    def get_game_dir(self):
        return {"path": minecraft.GAME_DIR}

    def open_game_dir(self):
        path = minecraft.GAME_DIR
        os.makedirs(path, exist_ok=True)
        os.startfile(path)
        return {"ok": True}

    # --- Серверы (список) -----------------------------------------------------

    def add_server(self, name: str, ip: str, port: int = 25565):
        cfg = _load_config()
        cfg["servers"].append({"name": name, "ip": ip, "port": port})
        _save_config(cfg)
        minecraft.sync_servers_dat(cfg["servers"])
        return {"ok": True, "servers": cfg["servers"]}

    def remove_server(self, index: int):
        cfg = _load_config()
        if 0 <= index < len(cfg["servers"]):
            cfg["servers"].pop(index)
            _save_config(cfg)
        servers = _load_config()["servers"]
        minecraft.sync_servers_dat(servers)
        return {"ok": True, "servers": servers}

    # --- Папка модов ----------------------------------------------------------

    def open_mods_folder(self):
        minecraft.open_mods_folder()
        return {"ok": True}

    # --- Microsoft auth -------------------------------------------------------

    def get_auth_status(self):
        auth = _load_auth()
        if auth:
            return {"logged_in": True, "username": auth.get("name", ""), "uuid": auth.get("id", "")}
        return {"logged_in": False}

    def start_ms_login(self):
        threading.Thread(target=self._ms_login_worker, daemon=True).start()
        return {"ok": True}

    def logout(self):
        try:
            os.remove(AUTH_PATH)
        except FileNotFoundError:
            pass
        return {"ok": True}

    def _ms_login_worker(self):
        try:
            import minecraft_launcher_lib as mll
            login_url = mll.microsoft_account.get_login_url(MS_CLIENT_ID, MS_REDIRECT_URI)
            redirect_url = [None]
            auth_win     = [None]

            def on_loaded():
                try:
                    url = auth_win[0].get_current_url() or ""
                    if mll.microsoft_account.url_contains_auth_code(url):
                        redirect_url[0] = url
                        auth_win[0].destroy()
                except Exception:
                    pass

            win = webview.create_window("Microsoft Login", login_url, width=500, height=680, resizable=False)
            auth_win[0] = win
            win.events.loaded += on_loaded

            for _ in range(600):
                if redirect_url[0]:
                    break
                time.sleep(0.5)

            if not redirect_url[0]:
                self._emit("onMsError", "Login timeout (5 min)")
                return

            auth_code = mll.microsoft_account.get_auth_code_from_url(redirect_url[0])
            if not auth_code:
                self._emit("onMsError", "Failed to extract auth code")
                return

            account = mll.microsoft_account.complete_login(
                MS_CLIENT_ID, None, MS_REDIRECT_URI, auth_code, None
            )
            _save_auth(account)
            self._emit("onMsLoggedIn", account["name"], account["id"])

        except Exception as e:
            self._emit("onMsError", str(e))

    # --- Запуск игры ----------------------------------------------------------

    def play(self, version_id, username, loader="vanilla", use_ms=False,
             ram=2048, width=854, height=480, server=None, port=25565):
        if not use_ms and not username.strip():
            return {"ok": False, "error": "Введи ник"}
        cfg = _load_config()
        threading.Thread(
            target=self._play_worker,
            args=(version_id, username.strip(), loader, use_ms,
                  int(ram), int(width), int(height),
                  server, port,
                  cfg["java_path"], cfg["jvm_extra"]),
            daemon=True,
        ).start()
        return {"ok": True}

    def _play_worker(self, version_id, username, loader, use_ms,
                     ram, width, height, server, port, java_path, jvm_extra):
        try:
            _save_config({"username": username, "version": version_id,
                          "loader": loader, "ram": ram, "width": width, "height": height})

            self._current_username = username

            callback = {
                "setStatus":   lambda text:  self._emit("onStatus", text),
                "setProgress": lambda value: self._emit("onProgress", value),
                "setMax":      lambda value: self._emit("onMax", value),
            }

            if loader == "fabric":
                self._emit("onStatus", "Preparing Fabric for " + version_id + "...")
                launch_id = minecraft.install_fabric(version_id, callback)
            else:
                if not minecraft.is_installed(version_id):
                    self._emit("onStatus", "Downloading " + version_id + "...")
                    minecraft.install_version(version_id, callback)
                launch_id = version_id

            self._emit("onStatus", "Launching...")

            extra_jvm = [a for a in jvm_extra.split() if a] if jvm_extra.strip() else []
            srv  = server if server else None
            prt  = int(port) if server and port else None

            # Записываем servers.dat прямо перед запуском (на случай если go_online не успел)
            try:
                minecraft.sync_servers_dat(_load_config()["servers"])
            except Exception:
                pass

            if use_ms:
                auth = _load_auth()
                if not auth:
                    self._emit("onError", "Not logged in to Microsoft")
                    return
                proc = minecraft.launch_authenticated(
                    launch_id, auth["name"], auth["id"], auth["access_token"],
                    ram_mb=ram, width=width, height=height,
                    server=srv, port=prt,
                    java_path=java_path, extra_jvm=extra_jvm
                )
            else:
                proc = minecraft.launch_offline(
                    launch_id, username,
                    ram_mb=ram, width=width, height=height,
                    server=srv, port=prt,
                    java_path=java_path, extra_jvm=extra_jvm
                )

            time.sleep(4)
            if proc.poll() is not None:
                self._emit("onError", "Game crashed (exit code " + str(proc.poll()) + ")")
                return

            self._emit("onLaunched")

            if self._idle_timer:
                self._idle_timer.cancel()
                self._idle_timer = None
            relay.publish_presence(username, "playing", ver=launch_id)
            threading.Thread(
                target=self._monitor_game, args=(proc, username), daemon=True
            ).start()

        except Exception as e:
            import traceback, os as _os
            try:
                _dbg = _os.path.join(_os.path.dirname(_os.path.dirname(__file__)), "play_error.log")
                with open(_dbg, "w", encoding="utf-8") as _f:
                    _f.write(traceback.format_exc())
            except Exception:
                pass
            self._emit("onError", str(e))

    # --- Мультиплеер-сервер ---------------------------------------------------

    def get_local_ip(self):
        return {"ip": server_manager.get_local_ip()}

    def get_server_status(self):
        return {"running": server_manager.is_running()}

    def start_server(self, version_id: str, ram: int = 1024):
        cfg = _load_config()
        _save_config({"server_version": version_id, "server_ram": int(ram)})

        def on_log(line):
            self._emit("onServerLog", line)

        def on_started():
            self._emit("onServerStarted")
            try:
                ext_ip = upnp.get_external_ip_fallback()
                relay.publish_presence(cfg["username"], "hosting", ver=version_id, ip=ext_ip)
                relay.publish_session(cfg["username"], ext_ip, 25565, version_id)
            except Exception:
                pass

        def on_stopped():
            relay.clear_session(cfg["username"])
            relay.publish_presence(cfg["username"], "online")
            self._reset_idle_timer()
            self._emit("onServerStopped")

        def on_error(msg):
            relay.clear_session(cfg["username"])
            relay.publish_presence(cfg["username"], "online")
            self._reset_idle_timer()
            self._emit("onServerError", msg)

        server_manager.start(
            version_id, int(ram), cfg["java_path"],
            on_log, on_started, on_stopped, on_error
        )
        return {"ok": True}

    def stop_server(self):
        cfg = _load_config()
        relay.clear_session(cfg["username"])
        relay.publish_presence(cfg["username"], "online")
        self._reset_idle_timer()
        server_manager.stop()
        upnp.close_port(25565)
        return {"ok": True}

    def server_command(self, cmd: str):
        server_manager.send_command(cmd)
        return {"ok": True}

    # --- Relay / онлайн-статус друзей -----------------------------------------

    def watch_friends(self):
        cfg = _load_config()
        for f in cfg["friends"]:
            name = f["name"]
            def make_session_cb(n):
                def cb(data):
                    self._emit("onFriendSession", n, data)
                return cb
            def make_pres_cb(n):
                def cb(data):
                    self._emit("onFriendPresence", n, data)
                return cb
            relay.watch_friend(name, make_session_cb(name))
            relay.watch_presence(name, make_pres_cb(name))
        return {"ok": True}

    def stop_watching(self):
        cfg = _load_config()
        for f in cfg["friends"]:
            relay.unwatch_friend(f["name"])
            relay.unwatch_presence(f["name"])
        return {"ok": True}

    # --- Друзья ---------------------------------------------------------------

    def add_friend(self, name: str, ip: str, port: int = 25565):
        cfg = _load_config()
        if any(f["name"].lower() == name.lower() for f in cfg["friends"]):
            return {"ok": False, "error": "Уже в списке", "friends": cfg["friends"]}
        cfg["friends"].append({"name": name, "ip": ip, "port": int(port)})
        _save_config(cfg)
        def session_cb(data):
            self._emit("onFriendSession", name, data)
        def pres_cb(data):
            self._emit("onFriendPresence", name, data)
        relay.watch_friend(name, session_cb)
        relay.watch_presence(name, pres_cb)
        return {"ok": True, "friends": cfg["friends"]}

    def remove_friend(self, index: int):
        cfg = _load_config()
        if 0 <= index < len(cfg["friends"]):
            relay.unwatch_friend(cfg["friends"][index]["name"])
            relay.unwatch_presence(cfg["friends"][index]["name"])
            cfg["friends"].pop(index)
            _save_config(cfg)
        return {"ok": True, "friends": _load_config()["friends"]}

    # --- UPnP / хостинг -------------------------------------------------------

    def open_port_upnp(self, port: int = 25565):
        try:
            ext_ip, method = upnp.open_port(int(port))
            return {"ok": True, "ip": ext_ip, "method": method}
        except Exception as e:
            return {"ok": False, "error": str(e), "ip": "", "method": "manual"}

    def close_port_upnp(self, port: int = 25565):
        upnp.close_port(int(port))
        return {"ok": True}
