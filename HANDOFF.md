# HANDOFF — записка для следующей сессии Claude

> Читай это первым. Проект ведём в режиме **парного программирования** с Mark.
> Mark УЧИТСЯ кодить сам — НЕ пиши за него весь код. Объясняй, давай куски с
> `TODO(Mark)`, проверяй и правь то, что он написал. Можно материться и шутить
> черно (его просьба). Каждый пройденный шаг — конспектируй в `docs/` для Obsidian.
> Стек Mark: HTML/CSS, Python/Go/C++, PostgreSQL/MySQL/SQLite, Git, Docker.
> Гит: https://github.com/KoDiK2005

## Что это
Десктопный лаунчер Minecraft "NOVA". Стек: **Python + pywebview + minecraft-launcher-lib**.
UI — HTML/CSS в окне pywebview. Логика — Python. Связь JS↔Python через js_api.

## Текущее состояние (на 2026-06-18)

### ✅ Готово
- `backend/minecraft.py` — get_versions, install_version (прогресс-колбэк),
  launch_offline (offline-uuid как у настоящего MC), is_installed.
- `backend/api.py` — list_versions() возвращает `{versions, saved_username, saved_version}`.
  play() запускает воркер в фоне. _play_worker сохраняет config.json перед запуском.
  _load_config / _save_config — атомарная запись через tmp+rename.
- `ui/index.html` — полностью переделан под стиль оригинального макета:
  сайдбар (NOVA лого, nav, game item, user-row), hero-секция (MINECRAFT, пульсирующий PLAY),
  ник+версия+прогресс+статус, три инфо-карточки внизу.
  Шрифты: Chakra Petch (логотип, кнопки, лейблы), DM Sans (тело).
  Цвета: зелёный #2ff5a8, фиолетовый #7e62ff, фон #06070a.
  init() восстанавливает saved_username и saved_version из API.
- `config.json` — создаётся автоматически при первом запуске игры.
- `main.py` — точка входа, размер окна 1000×640.

### ❌ Не проверено вживую
Реальное скачивание и старт java — нужна машина Mark с Java 17+ в PATH.

## Как запустить (на Windows Mark)
```powershell
cd "C:\Users\zingo\OneDrive\Рабочий стол\launcher"
.venv\Scripts\activate
python main.py
```
Нужна **Java 17+** в PATH (`java -version`). Без неё игра не стартует.

## Следующие шаги (по приоритету)
1. **[СРОЧНО] Тест вживую** — запустить `python main.py`, нажать ИГРАТЬ, посмотреть что сломалось.
2. Выбор объёма RAM (jvmArguments `-Xmx`) — добавить слайдер или поле в Settings.
3. Этап 2: вход через Microsoft (`mll.microsoft_account`) — ТОЛЬКО после того как offline стабилен.
4. Возможно: своя папка игры в настройках (сейчас хардкодом `game_data/`).

## Известные нюансы / грабли
- OneDrive иногда роняет чтение ("null bytes") — если py_compile ругнётся, просто повтори.
- `game_data/`, `.venv/`, `config.json` уже в .gitignore — НЕ коммитить.
- `NOVA Launcher.html` — дизайн-референс, его кнопки к Python не прикручены.

## Карта файлов
```
launcher/
├── main.py              точка входа
├── requirements.txt
├── config.json          ник + версия (создаётся при первом запуске)
├── .gitignore
├── HANDOFF.md           ← ты здесь
├── backend/
│   ├── api.py           мост JS<->Python + config read/write
│   └── minecraft.py     логика игры
├── ui/index.html        рабочий UI (стиль NOVA Launcher)
├── docs/                конспекты для Obsidian
└── NOVA Launcher.html   исходный макет (референс)
```
