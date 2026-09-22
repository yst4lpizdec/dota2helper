"""Загрузка иконок предметов и героев из открытого CDN Valve.

Качается один раз и лежит локально: во время игры интерфейс не должен
ходить в сеть. Иконки — основная часть того, что делает оверлей похожим
на нормальное приложение, а не на текстовый отчёт.
"""

import concurrent.futures

import requests

from config import DATA_DIR
from database.database import get_connection


CDN = "https://cdn.cloudflare.steamstatic.com/apps/dota2/images/dota_react"

ICONS_DIR = DATA_DIR / "icons"
ITEMS_DIR = ICONS_DIR / "items"
HEROES_DIR = ICONS_DIR / "heroes"

# Крупные портреты 400x250 — те же, что Valve показывает на сайте игры.
# Нужны шапке главного окна: мелкая иконка в баннере выглядит мылом.
CROPS_DIR = ICONS_DIR / "crops"
ABILITIES_DIR = ICONS_DIR / "abilities"


def _download(url, path):
    if path.exists() and path.stat().st_size > 0:
        return True

    try:
        response = requests.get(url, timeout=30)

        if response.status_code != 200 or not response.content:
            return False

        path.write_bytes(response.content)

        return True

    except requests.RequestException:
        return False


def download_all(workers=12):
    """Тянет иконки всех предметов и героев из нашей базы."""

    ITEMS_DIR.mkdir(parents=True, exist_ok=True)
    HEROES_DIR.mkdir(parents=True, exist_ok=True)
    CROPS_DIR.mkdir(parents=True, exist_ok=True)
    ABILITIES_DIR.mkdir(parents=True, exist_ok=True)

    connection = get_connection()

    items = [
        row["name"].replace("item_", "")
        for row in connection.execute("SELECT name FROM items")
        if row["name"]
    ]

    heroes = [
        row["name"].replace("npc_dota_hero_", "")
        for row in connection.execute("SELECT name FROM heroes")
        if row["name"]
    ]

    # Только способности из раскладки героев: в таблице лежат ещё
    # таланты и служебные записи, картинок для них нет.
    abilities = [
        row["name"]
        for row in connection.execute(
            """
            SELECT DISTINCT a.name
            FROM hero_abilities ha JOIN abilities a ON a.id = ha.ability_id
            WHERE a.is_talent = 0 AND a.name NOT LIKE 'generic%'
            """
        )
        if row["name"]
    ]

    connection.close()

    jobs = [
        (f"{CDN}/items/{name}.png", ITEMS_DIR / f"{name}.png") for name in items
    ] + [
        (f"{CDN}/heroes/{name}.png", HEROES_DIR / f"{name}.png")
        for name in heroes
    ] + [
        (f"{CDN}/heroes/crops/{name}.png", CROPS_DIR / f"{name}.png")
        for name in heroes
    ] + [
        (f"{CDN}/abilities/{name}.png", ABILITIES_DIR / f"{name}.png")
        for name in abilities
    ]

    done = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        results = pool.map(lambda job: _download(*job), jobs)

        done = sum(1 for ok in results if ok)

    return {
        "всего": len(jobs),
        "скачано": done,
        "не найдено": len(jobs) - done,
        "папка": str(ICONS_DIR),
    }


def item_icon(item_name):
    path = ITEMS_DIR / f"{item_name.replace('item_', '')}.png"

    return path if path.exists() else None


def hero_icon(hero_short):
    path = HEROES_DIR / f"{hero_short}.png"

    return path if path.exists() else None


def ability_icon(ability_name):
    if not ability_name:
        return None

    path = ABILITIES_DIR / f"{ability_name}.png"

    return path if path.exists() else None
