"""Сборка snapshot'а — единственного файла, который скачивает Helper.

Клиенту не нужны сырые матчи: ему нужен готовый агрегат. Snapshot держат
в памяти целиком, поэтому во время игры оверлей не обращается ни к сети,
ни к базе.
"""

import gzip
import json
import time

from config import DATA_DIR, USER_DIR
from database.database import get_connection
from services.patches import Weights


SNAPSHOT_PATH = DATA_DIR / "snapshot.json.gz"

# Скачанные обновлением данные. Лежат рядом с настройками: в папку
# программы писать нельзя, а обновляются они чаще самой программы.
USER_SNAPSHOT_PATH = USER_DIR / "snapshot.json.gz"


def snapshot_path():
    """Какими данными пользоваться.

    Скачанные важнее встроенных: их для того и качали. Но только если
    они новее — иначе после переустановки программа откатилась бы на
    старые цифры, уже лежащие в профиле.
    """

    try:
        if USER_SNAPSHOT_PATH.stat().st_mtime > SNAPSHOT_PATH.stat().st_mtime:
            return USER_SNAPSHOT_PATH

    except OSError:
        pass

    return SNAPSHOT_PATH


def build_snapshot():
    connection = get_connection()

    heroes = {
        str(row["id"]): {
            "name": row["name"],
            "localized_name": row["localized_name"],
        }
        for row in connection.execute("SELECT * FROM heroes")
    }

    items = {
        str(row["id"]): {
            "name": row["name"],
            "display": row["display_name"] or row["name"],
            "cost": row["cost"],
        }
        for row in connection.execute("SELECT * FROM items")
    }

    # Из чего собирается предмет. Нужно движку, чтобы не советовать
    # в ранней игре Wind Lace и Boots отдельной строкой, когда там же
    # рядом стоят собранные из них Tranquil Boots.
    components = {}

    for row in connection.execute(
        """
        SELECT parent.name AS item, child.name AS component
        FROM item_components ic
        JOIN items parent ON parent.id = ic.item_id
        JOIN items child ON child.id = ic.component_id
        """
    ):
        components.setdefault(row["item"], []).append(row["component"])

    abilities = {
        str(row["id"]): {
            "name": row["name"],
            "display": row["display_name"] or row["name"],
            "is_talent": bool(row["is_talent"]),
        }
        for row in connection.execute("SELECT * FROM abilities")
    }

    guides = {}

    for row in connection.execute("SELECT * FROM guides"):
        guides[f"{row['hero_id']}:{row['position']}"] = json.loads(row["data"])

    # Матчапы храним максимально компактно: герой -> враг -> предмет -> сдвиг.
    matchups = {}

    for row in connection.execute(
        "SELECT hero_id, enemy_hero_id, item_id, shift FROM matchup_items"
    ):
        hero = matchups.setdefault(str(row["hero_id"]), {})
        enemy = hero.setdefault(str(row["enemy_hero_id"]), {})

        enemy[str(row["item_id"])] = row["shift"]

    # Кто у кого выигрывает: герой -> враг -> [встреч, побед]. Нужно
    # разделу контрпиков; в матчапах выше лежат только сдвиги закупки.
    pairs = {}

    for row in connection.execute(
        "SELECT hero_id, enemy_hero_id, matches, wins FROM matchup_pairs"
    ):
        hero = pairs.setdefault(str(row["hero_id"]), {})

        hero[str(row["enemy_hero_id"])] = [row["matches"], row["wins"]]

    # Раскладка героя: номер способности и место таланта в дереве.
    layout = {}

    for row in connection.execute(
        """
        SELECT ha.hero_id, a.name, ha.slot
        FROM hero_abilities ha JOIN abilities a ON a.id = ha.ability_id
        """
    ):
        hero = layout.setdefault(str(row["hero_id"]), {"abilities": {}, "talents": {}})
        hero["abilities"][row["name"]] = row["slot"]

    for row in connection.execute(
        """
        SELECT ht.hero_id, a.name, ht.slot
        FROM hero_talents ht JOIN abilities a ON a.id = ht.ability_id
        """
    ):
        hero = layout.setdefault(str(row["hero_id"]), {"abilities": {}, "talents": {}})
        hero["talents"][row["name"]] = row["slot"]

    # Номер версии у STRATZ внутренний и вдобавок застрял на 7.40b, так
    # что патч определяем по датам матчей и списку патчей от Valve.
    weights = Weights()
    spread = weights.summary(connection)

    total_matches = connection.execute(
        "SELECT COUNT(*) FROM matches"
    ).fetchone()[0]

    connection.close()

    return {
        "built_at": int(time.time()),
        "patch": spread["current"],
        # Сколько матчей какого патча стоит за советом: это же объясняет,
        # почему у свежего патча цифры могут двигаться день ото дня.
        "patches": spread["by_patch"],
        "matches": total_matches,
        "heroes": heroes,
        "items": items,
        "components": components,
        "abilities": abilities,
        "layout": layout,
        "guides": guides,
        "matchups": matchups,
        "pairs": pairs,
    }


def export_snapshot(path=SNAPSHOT_PATH):
    """Пишет snapshot на диск сжатым: по сети он поедет именно так."""

    snapshot = build_snapshot()

    path.parent.mkdir(parents=True, exist_ok=True)

    raw = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))

    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write(raw)

    return {
        "path": str(path),
        "raw_mb": round(len(raw.encode("utf-8")) / 1048576, 1),
        "gzip_mb": round(path.stat().st_size / 1048576, 1),
        "guides": len(snapshot["guides"]),
        "heroes_with_matchups": len(snapshot["matchups"]),
        "matches": snapshot["matches"],
        "patch": snapshot["patch"],
    }


def load_snapshot(path=None):
    path = path or snapshot_path()

    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)
