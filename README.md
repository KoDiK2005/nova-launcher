# NOVA Launcher

Десктопный лаунчер Minecraft с кастомным UI. Без Microsoft аккаунта, с поддержкой модов и мультиплеером напрямую между друзьями.

![Python](https://img.shields.io/badge/Python-3.12-blue)
![pywebview](https://img.shields.io/badge/pywebview-4.4.1-green)
![Platform](https://img.shields.io/badge/platform-Windows-lightgrey)
![License](https://img.shields.io/badge/license-MIT-orange)

## Возможности

### Игра
- Запуск любой версии Minecraft (release) в offline-режиме
- Поддержка Fabric мод-лоадера (версии 1.14+)
- Выбор объёма RAM (512 MB — 8 GB)
- Настройка разрешения окна игры
- Кастомный путь к Java и дополнительные JVM аргументы
- Ник и настройки сохраняются между сессиями
- Стабильный UUID на основе ника (инвентарь не теряется)

### Мультиплеер (без Microsoft и сторонних программ)
- Встроенный Minecraft сервер — запуск одной кнопкой
- Автоматический проброс порта через **UPnP** (не нужно лезть в настройки роутера)
- Онлайн список друзей — видно кто сейчас хостит и на какой версии
- Кнопка **▶ ИГРАТЬ** прямо в карточке друга
- `online-mode=false` выставляется автоматически — аккаунт не нужен

### Интерфейс
- Три вкладки: Главная / Мультиплеер / Настройки
- Список серверов с быстрым подключением
- Кнопка открытия папки модов и папки игры

## Зависимости

- Python 3.12+
- Java 17+ в PATH (`java -version`)

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

| Компонент       | Технология                    |
|-----------------|-------------------------------|
| UI              | HTML / CSS (в pywebview окне) |
| Backend         | Python 3.12                   |
| WebView         | pywebview 4.4.1 (EdgeChromium)|
| Minecraft       | minecraft-launcher-lib        |
| Моды            | Fabric API                    |
| Мультиплеер     | Встроенный MC сервер + UPnP   |
| Онлайн-статусы  | MQTT (paho-mqtt, emqx broker) |

## Структура

```
launcher/
├── main.py                — точка входа
├── requirements.txt
├── config.json            — настройки пользователя (gitignore)
├── backend/
│   ├── api.py             — мост JS ↔ Python, все методы API
│   ├── minecraft.py       — загрузка, Fabric, запуск игры
│   ├── server_manager.py  — встроенный MC сервер
│   ├── upnp.py            — UPnP проброс порта
│   └── relay.py           — MQTT обнаружение друзей онлайн
├── ui/
│   └── index.html         — весь интерфейс (3 страницы)
└── server_data/           — файлы серверов (gitignore)
```

## Мультиплеер с друзьями

1. **Хозяин** открывает вкладку **Мультиплеер** → выбирает версию → **▶ ХОСТИТЬ**
2. Лаунчер сам запускает сервер и пробрасывает порт через UPnP
3. **Друг** добавляет хозяина по нику → видит **🟢 онлайн** → жмёт **▶ ИГРАТЬ**

Если UPnP не поддерживается роутером — лаунчер покажет внешний IP и инструкцию по ручному пробросу порта 25565.

## Лицензия

MIT
