# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec для NOVA Launcher.
Сборка: запусти build.bat
"""

import os
import site

block_cipher = None

# Путь к pywebview lib-папке с DLL-ками
webview_lib = os.path.join(
    next(p for p in site.getsitepackages() if 'site-packages' in p),
    'webview', 'lib'
)

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        # UI файлы
        ('ui',      'ui'),
        # pywebview DLL-ки (WebView2 interop)
        (webview_lib, 'webview/lib'),
    ],
    hiddenimports=[
        # pywebview бэкенды
        'webview',
        'webview.platforms.winforms',
        'webview.platforms.edgechromium',
        # pythonnet
        'clr',
        'clr_loader',
        'cffi',
        # paho MQTT
        'paho',
        'paho.mqtt',
        'paho.mqtt.client',
        # minecraft-launcher-lib
        'minecraft_launcher_lib',
        'minecraft_launcher_lib.command',
        'minecraft_launcher_lib.install',
        'minecraft_launcher_lib.fabric',
        'minecraft_launcher_lib.forge',
        'minecraft_launcher_lib.utils',
        'minecraft_launcher_lib.microsoft_account',
        'minecraft_launcher_lib.exceptions',
        # requests / сеть
        'requests',
        'certifi',
        'charset_normalizer',
        'idna',
        'urllib3',
        # miniupnpc
        'miniupnpc',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,      # --onedir (надёжнее чем onefile для pythonnet)
    name='NOVA Launcher',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,              # нет чёрного окна консоли
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    # icon='icon.ico',          # раскомментируй если добавишь иконку
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=['*.dll'],
    name='NOVA Launcher',
)
