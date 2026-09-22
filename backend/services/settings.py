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
}


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
