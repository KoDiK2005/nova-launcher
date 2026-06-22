"""
minecraft.py — вся логика работы с самой игрой.
"""

import hashlib
import os
import subprocess
import uuid

import minecraft_launcher_lib as mll

GAME_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "game_data")


# ─── Версии ───────────────────────────────────────────────────────────────────

def get_versions() -> list[dict]:
    """Только релизы. При отсутствии сети — установленные."""
    try:
        all_versions = mll.utils.get_version_list()
    except Exception:
        all_versions = mll.utils.get_installed_versions(GAME_DIR)
    return [v for v in all_versions if v["type"] == "release"]


def is_installed(version_id: str) -> bool:
    installed = {v["id"] for v in mll.utils.get_installed_versions(GAME_DIR)}
    return version_id in installed


def install_version(version_id: str, callback: dict | None = None) -> None:
    os.makedirs(GAME_DIR, exist_ok=True)
    mll.install.install_minecraft_version(version_id, GAME_DIR, callback=callback or {})


# ─── Fabric ───────────────────────────────────────────────────────────────────

def get_fabric_loader_version() -> str:
    """Последняя стабильная версия Fabric-лоадера."""
    loaders = mll.fabric.get_all_loader_versions()
    # stable=True если есть поле, иначе берём первый
    stable = [l for l in loaders if l.get("stable", True)]
    return (stable or loaders)[0]["version"]


def fabric_version_id(mc_version: str) -> str:
    """Строит ID установленной Fabric-версии для запуска."""
    return f"fabric-loader-{get_fabric_loader_version()}-{mc_version}"


def is_fabric_installed(mc_version: str) -> bool:
    installed = {v["id"] for v in mll.utils.get_installed_versions(GAME_DIR)}
    return fabric_version_id(mc_version) in installed


def install_fabric(mc_version: str, callback: dict | None = None) -> str:
    """Устанавливает vanilla + Fabric. Возвращает version_id для запуска."""
    os.makedirs(GAME_DIR, exist_ok=True)
    # vanilla должна быть первой — Fabric на неё накладывается
    if not is_installed(mc_version):
        install_version(mc_version, callback)
    mll.fabric.install_fabric(mc_version, GAME_DIR, callback=callback or {})
    return fabric_version_id(mc_version)


# ─── Запуск ───────────────────────────────────────────────────────────────────

def _offline_uuid(username: str) -> str:
    """UUID из ника — как делает сам Minecraft в offline-режиме."""
    data = hashlib.md5(f"OfflinePlayer:{username}".encode()).digest()
    return str(uuid.UUID(bytes=data, version=3))


def launch_offline(version_id: str, username: str) -> subprocess.Popen:
    """Запуск без аккаунта."""
    options = {
        "username": username,
        "uuid": _offline_uuid(username),
        "token": "",
    }
    command = mll.command.get_minecraft_command(version_id, GAME_DIR, options)
    return subprocess.Popen(command)


def launch_authenticated(version_id: str, username: str, uuid_str: str, token: str) -> subprocess.Popen:
    """Запуск с реальным Microsoft-аккаунтом."""
    options = {
        "username": username,
        "uuid":     uuid_str,
        "token":    token,
    }
    command = mll.command.get_minecraft_command(version_id, GAME_DIR, options)
    return subprocess.Popen(command)
