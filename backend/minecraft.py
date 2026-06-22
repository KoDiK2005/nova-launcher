"""
minecraft.py — логика работы с игрой.
"""

import gzip
import hashlib
import os
import struct
import subprocess
import uuid

import minecraft_launcher_lib as mll

GAME_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "game_data")


# --- Версии -------------------------------------------------------------------

def get_versions() -> list[dict]:
    try:
        all_versions = mll.utils.get_version_list()
    except Exception:
        all_versions = mll.utils.get_installed_versions(GAME_DIR)
    return [v for v in all_versions if v["type"] == "release"]


def is_installed(version_id: str) -> bool:
    return version_id in {v["id"] for v in mll.utils.get_installed_versions(GAME_DIR)}


def install_version(version_id: str, callback: dict | None = None) -> None:
    os.makedirs(GAME_DIR, exist_ok=True)
    mll.install.install_minecraft_version(version_id, GAME_DIR, callback=callback or {})


# --- Fabric -------------------------------------------------------------------

def get_fabric_loader_version() -> str:
    loaders = mll.fabric.get_all_loader_versions()
    stable = [l for l in loaders if l.get("stable", True)]
    return (stable or loaders)[0]["version"]


def fabric_version_id(mc_version: str) -> str:
    return f"fabric-loader-{get_fabric_loader_version()}-{mc_version}"


def is_fabric_installed(mc_version: str) -> bool:
    return fabric_version_id(mc_version) in {v["id"] for v in mll.utils.get_installed_versions(GAME_DIR)}


def install_fabric(mc_version: str, callback: dict | None = None) -> str:
    os.makedirs(GAME_DIR, exist_ok=True)
    if not is_installed(mc_version):
        install_version(mc_version, callback)
    mll.fabric.install_fabric(mc_version, GAME_DIR, callback=callback or {})
    return fabric_version_id(mc_version)


# --- Папка модов --------------------------------------------------------------

def get_mods_dir() -> str:
    path = os.path.join(GAME_DIR, "mods")
    os.makedirs(path, exist_ok=True)
    return path


def open_mods_folder() -> None:
    os.startfile(get_mods_dir())


# --- servers.dat (синхронизация с Minecraft) ----------------------------------

def _nbt_str(s: str) -> bytes:
    enc = s.encode("utf-8")
    return struct.pack(">H", len(enc)) + enc


def sync_servers_dat(servers: list) -> None:
    """Записывает список серверов в game_data/servers.dat — они появятся
    в мультиплеере прямо в игре."""
    entries = []
    for srv in servers:
        ip = srv["ip"]
        if int(srv.get("port", 25565)) != 25565:
            ip = f"{ip}:{srv['port']}"
        name = srv.get("name", ip)

        payload = b""
        payload += bytes([8]) + _nbt_str("ip")   + _nbt_str(ip)
        payload += bytes([8]) + _nbt_str("name") + _nbt_str(name)
        payload += b"\x00"  # TAG_End
        entries.append(payload)

    # TAG_List (9) named "servers" of TAG_Compound (10)
    list_payload  = bytes([10])
    list_payload += struct.pack(">i", len(entries))
    for e in entries:
        list_payload += e

    # Root TAG_Compound contents
    root  = bytes([9]) + _nbt_str("servers") + list_payload
    root += b"\x00"  # TAG_End

    # Полный NBT: TAG_Compound (10) с пустым именем
    nbt = bytes([10]) + _nbt_str("") + root

    os.makedirs(GAME_DIR, exist_ok=True)
    with gzip.open(os.path.join(GAME_DIR, "servers.dat"), "wb") as f:
        f.write(nbt)


# --- Запуск -------------------------------------------------------------------

def _offline_uuid(username: str) -> str:
    data = hashlib.md5(f"OfflinePlayer:{username}".encode()).digest()
    return str(uuid.UUID(bytes=data, version=3))


def _build_options(username, uuid_str, token, ram_mb, width, height,
                   server, port, java_path, extra_jvm) -> dict:
    jvm = [f"-Xmx{ram_mb}M", "-Xms512M"] + (extra_jvm or [])
    opts = {
        "username":         username,
        "uuid":             uuid_str,
        "token":            token,
        "jvmArguments":     jvm,
        "resolutionWidth":  str(width),
        "resolutionHeight": str(height),
    }
    if java_path and java_path.strip() and java_path.strip() != "java":
        opts["executablePath"] = java_path.strip()
    if server:
        opts["server"] = server
    if port:
        opts["port"] = str(port)
    return opts


def launch_offline(version_id: str, username: str,
                   ram_mb=2048, width=854, height=480,
                   server=None, port=None,
                   java_path="java", extra_jvm=None) -> subprocess.Popen:
    opts    = _build_options(username, _offline_uuid(username), "",
                             ram_mb, width, height, server, port, java_path, extra_jvm)
    command = mll.command.get_minecraft_command(version_id, GAME_DIR, opts)
    return subprocess.Popen(command)


def launch_authenticated(version_id: str, username: str, uuid_str: str, token: str,
                          ram_mb=2048, width=854, height=480,
                          server=None, port=None,
                          java_path="java", extra_jvm=None) -> subprocess.Popen:
    opts    = _build_options(username, uuid_str, token,
                             ram_mb, width, height, server, port, java_path, extra_jvm)
    command = mll.command.get_minecraft_command(version_id, GAME_DIR, opts)
    return subprocess.Popen(command)
