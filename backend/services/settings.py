"""Пользовательские настройки оверлея.

Один файл на всё приложение: его читают и панель, и окно настроек,
поэтому хранение живёт отдельно от них обоих.
"""

import json

from config import USER_DIR


SETTINGS_PATH = USER_DIR / "settings.json"

# Блоки панели, которые игрок может выключить. Порядок — как на самой
# панели, чтобы список настроек читался вместе с ней.
BLOCKS = [
    ("enemies", "вражеские герои"),
    ("lane", "кто против тебя на линии"),
    ("legend", "пояснение к рамкам"),
    ("starting", "СТАРТ"),
    ("early", "РАННЯЯ"),
    ("consumables", "РАСХОДНИКИ И ВАРДЫ"),
    ("core", "КОР"),
    ("situational", "СИТУАТИВНО ПРОТИВ ПИКА"),
    ("skills", "ПОРЯДОК ПРОКАЧКИ"),
    ("talents", "ТАЛАНТЫ"),
    ("hint", "строка подсказок"),
]

DEFAULTS = {
    "position": None,
    "compact": False,
    "scale": 1.0,
    "opacity": 100,
    "hidden": [],
    # Разделы, свёрнутые игроком: оверлей должен помнить это между
    # запусками, иначе панель каждый раз разворачивается целиком.
    #
    # По умолчанию свёрнуто то, что смотрят один раз за игру: прокачка и
    # таланты нужны на первых уровнях, редкие предметы — когда партия
    # пошла не по плану. Разворачивается одним кликом по заголовку.
    "folded": ["прокачка", "реже", "таланты"],
    # Прятать панель, пока Dota 2 не запущена.
    "hide_without_dota": True,
    # Крестик главного окна прячет его в значок у часов, а не выходит.
    "close_to_tray": True,
    # Открывать главное окно при запуске. При запуске вместе с Windows
    # оно не открывается никогда — см. autostart.TRAY_FLAG.
    "open_window": True,
    # Свежие данные качать самим, вне матча, а не только предлагать.
    "auto_data": False,
    # Искать новые версии программы.
    "check_app": True,
    # Сообщения у часов о свежих данных и новых версиях.
    "notify": True,
    # На каком мониторе держать панель: имя экрана, пусто — основной.
    "panel_screen": "",
    # Режимы панели сразу при запуске.
    "start_compact": False,
    "start_click_through": False,
    # Горячие клавиши панели.
    "hotkeys": {"compact": "Ctrl+Alt+D", "click_through": "Ctrl+Alt+F"},
    # Роль по умолчанию: пусто — угадывать по составу. Выбор на панели
    # во время матча всё равно главнее.
    "default_position": "",
}

# Что можно поменять со страницы настроек одним общим вызовом. Остальное
# (положение, свёрнутые блоки) панель пишет сама.
OPTIONS = {
    "close_to_tray",
    "open_window",
    "auto_data",
    "check_app",
    "notify",
    "panel_screen",
    "start_compact",
    "start_click_through",
    "default_position",
}


def reset():
    """Всё по умолчанию: файл настроек просто удаляется."""

    try:
        SETTINGS_PATH.unlink()

    except OSError:
        pass

    return load()


def load():
    try:
        stored = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))

    except (OSError, json.JSONDecodeError):
        stored = {}

    return {**DEFAULTS, **stored}


def save(settings):
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def update(**changes):
    """Меняет часть настроек, не трогая остальные."""

    settings = load()
    settings.update(changes)

    save(settings)

    return settings
