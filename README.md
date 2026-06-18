# NOVA Launcher

Offline-лаунчер Minecraft с чистым UI. Не требует Microsoft-аккаунта.

![Python](https://img.shields.io/badge/Python-3.12-blue) ![pywebview](https://img.shields.io/badge/pywebview-6.x-green) ![Platform](https://img.shields.io/badge/platform-Windows-lightgrey)

## Возможности

- Запуск любой версии Minecraft в offline-режиме
- Автоматическая загрузка версии при первом запуске
- Прогресс-бар скачивания
- Ник сохраняется между сессиями
- Стабильный UUID на основе ника (инвентарь и миры не теряются)

## Зависимости

- Python 3.12+
- Java 17+ в PATH

## Установка

```powershell
git clone https://github.com/KoDiK2005/nova-launcher.git
cd nova-launcher
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

## Стек

| Компонент | Технология |
|-----------|-----------|
| UI | HTML / CSS (pywebview) |
| Backend | Python 3.12 |
| WebView | EdgeChromium (pywebview) |
| Minecraft | minecraft-launcher-lib |

## Структура

```
launcher/
├── main.py              — точка входа
├── requirements.txt
├── backend/
│   ├── api.py           — мост JS ↔ Python
│   └── minecraft.py     — загрузка и запуск игры
└── ui/
    └── index.html       — интерфейс
```

## Лицензия

MIT
