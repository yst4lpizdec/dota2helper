"""Превращает сырые матчи в рекомендации: герой + позиция -> что покупать,
что качать, какие таланты брать."""

from statistics import median

from database.database import get_connection


# Фазы игры по времени покупки, в секундах от начала матча.
# Покупки до 0:00 — это стартовая закупка на фонтане.
PHASES = [
    ("start", None, 0),
    ("early", 0, 600),
    ("core", 600, 1500),
    ("late", 1500, None),
]

# Расходники нужны в стартовой закупке (сколько тангошек, веток, флаконов),
# но в списке крупных предметов они только мешают.
CONSUMABLES = {
    "item_tango",
    "item_tango_single",
    "item_clarity",
    "item_flask",
    "item_faerie_fire",
    "item_enchanted_mango",
    "item_branches",
    "item_tpscroll",
    "item_smoke_of_deceit",
    "item_dust",
    "item_ward_observer",
    "item_ward_sentry",
    "item_ward_dispenser",
    "item_blood_grenade",
    "item_bottle",
    "item_infused_raindrop",
    # Лотосы с рек и «сыр» с Рошана — подбираются, а не покупаются
    # по плану, и в ряду билда стоят на месте настоящего предмета.
    "item_famango",
    "item_great_famango",
    "item_greater_famango",
    "item_cheese",
    "item_aegis",
    "item_refresher_shard",
    "item_fusion_rune",
}

# Предмет попадает в рекомендацию, только если встречается хотя бы
# в этой доле матчей. Порог низкий намеренно: ситуативные предметы
# редки по определению, но именно им нужен тайминг — иначе они висят
# отдельным списком без ответа на вопрос «когда покупать».
MIN_SHARE = 0.02

# Ниже этого числа матчей в разрезе статистика не считается вовсе.
MIN_MATCHES = 30


def load_components(connection):
    """Карта «предмет -> его компоненты» по именам."""

    rows = connection.execute(
        """
        SELECT parent.name AS item, child.name AS component
        FROM item_components ic
        JOIN items parent ON parent.id = ic.item_id
        JOIN items child ON child.id = ic.component_id
        """
    ).fetchall()

    components = {}

    for row in rows:
        components.setdefault(row["item"], []).append(row["component"])

    return components


def collapse_to_final(item_names, components):
    """Убирает из набора то, что игрок уже собрал во что-то большее.

    Купил Yasha и следом Sange and Yasha — в рекомендации должен попасть
    только финальный предмет, иначе список забит полуфабрикатами.
    """

    result = set(item_names)

    for name in item_names:
        stack = list(components.get(name, []))

        while stack:
            component = stack.pop()

            result.discard(component)
            stack.extend(components.get(component, []))

    return result


def _phase_of_median(median_time):
    """В какую фазу билда попадает предмет.

    Считаем по медиане тайминга, а не по каждой покупке: предмет,
    который обычно берут к 12-й минуте, должен стоять в коре целиком,
    а не половиной в ранней игре из-за тех, кто успел к девятой.
    """

    for name, start, end in PHASES:
        if name == "start":
            continue

        if end is None or median_time <= end:
            return name

    return "late"


def _phase_of(purchase_time):
    for name, start, end in PHASES:
        after_start = start is None or purchase_time > start
        before_end = end is None or purchase_time <= end

        if after_start and before_end:
            return name

    return "late"


def _players(connection, hero_id, position):
    """Все игроки этого героя на этой позиции, с исходом матча."""

    rows = connection.execute(
        """
        SELECT match_id, player_slot, win, neutral_item_id
        FROM match_players
        WHERE hero_id = ? AND position = ?
        """,
        (hero_id, position),
    ).fetchall()

    return {(row["match_id"], row["player_slot"]): row for row in rows}


def _items(connection, hero_id, position):
    rows = connection.execute(
        """
        SELECT p.match_id, p.player_slot, p.purchase_time,
               i.name, i.display_name, i.is_base_component, mp.win
        FROM player_purchases p
        JOIN match_players mp
          ON mp.match_id = p.match_id AND mp.player_slot = p.player_slot
        JOIN items i ON i.id = p.item_id
        WHERE mp.hero_id = ? AND mp.position = ?
        """,
        (hero_id, position),
    ).fetchall()

    return rows


def _starting_items(item_rows, total):
    """Стартовая закупка: важно не в скольких матчах предмет встречался,
    а сколько штук берут — 2 тангошки и 3 ветки, а не «тангошки в 100% игр»."""

    counts = {}
    names = {}

    for row in item_rows:
        names[row["name"]] = row["display_name"] or row["name"]

        if _phase_of(row["purchase_time"]) != "start":
            continue

        counts[row["name"]] = counts.get(row["name"], 0) + 1

    result = [
        {"item": name, "display": names.get(name, name),
         "avg_count": round(count / total, 1)}
        for name, count in counts.items()
        if count / total >= 0.2
    ]

    return sorted(result, key=lambda entry: -entry["avg_count"])


def _phase_items(
    item_rows,
    total,
    phase,
    skip_consumables,
    base_winrate=None,
    components=None,
):
    """Предметы фазы: доля матчей, винрейт и типичный тайминг покупки.

    Винрейт дорогих предметов обманчив: их успевает купить тот, кто уже
    выигрывает, а не наоборот. Поэтому в поздних фазах сортируем по
    популярности, а винрейт даём как отклонение от базового у героя.
    """

    seen = {}
    names = {}

    # Что каждый игрок собрал в итоге: полуфабрикаты вычёркиваем,
    # если из них потом собрали предмет покрупнее.
    final = {}

    if components and skip_consumables:
        by_player = {}

        for row in item_rows:
            by_player.setdefault(
                (row["match_id"], row["player_slot"]), set()
            ).add(row["name"])

        final = {
            key: collapse_to_final(names_set, components)
            for key, names_set in by_player.items()
        }

    for row in item_rows:
        names[row["name"]] = row["display_name"] or row["name"]

        if _phase_of(row["purchase_time"]) != phase:
            continue

        if row["name"].startswith("item_recipe_"):
            continue

        # Сырые компоненты вроде Ogre Axe нужны только в стартовой закупке.
        # В остальных фазах показываем то, во что они собираются.
        if skip_consumables and row["is_base_component"]:
            continue

        if skip_consumables and row["name"] in CONSUMABLES:
            continue

        if final:
            kept = final.get((row["match_id"], row["player_slot"]))

            if kept is not None and row["name"] not in kept:
                continue

        entry = seen.setdefault(
            row["name"], {"players": set(), "wins": set(), "times": []}
        )

        key = (row["match_id"], row["player_slot"])

        # Один игрок мог купить предмет дважды — считаем его один раз.
        if key not in entry["players"]:
            entry["players"].add(key)
            entry["times"].append(row["purchase_time"])

            if row["win"]:
                entry["wins"].add(key)

    result = []

    for name, entry in seen.items():
        bought = len(entry["players"])
        share = bought / total

        if share < MIN_SHARE:
            continue

        winrate = len(entry["wins"]) / bought * 100

        result.append(
            {
                "item": name,
                "display": names.get(name, name),
                "share": round(share * 100, 1),
                "winrate": round(winrate, 1),
                "lift": (
                    round(winrate - base_winrate, 1)
                    if base_winrate is not None
                    else None
                ),
                "median_time": int(median(entry["times"])),
                "matches": bought,
            }
        )

    return sorted(result, key=lambda entry: -entry["share"])


def _skill_order(connection, hero_id, position, levels=10):
    """Что качают на каждом уровне героя. Порядок восстанавливаем по времени."""

    rows = connection.execute(
        """
        SELECT ord, name, SUM(win) AS wins, COUNT(*) AS total
        FROM (
            SELECT a.name AS name, mp.win AS win,
                   ROW_NUMBER() OVER (
                       PARTITION BY pa.match_id, pa.player_slot
                       ORDER BY pa.time
                   ) AS ord
            FROM player_abilities pa
            JOIN match_players mp
              ON mp.match_id = pa.match_id AND mp.player_slot = pa.player_slot
            JOIN abilities a ON a.id = pa.ability_id
            WHERE mp.hero_id = ? AND mp.position = ?
        )
        WHERE ord <= ?
        GROUP BY ord, name
        ORDER BY ord, total DESC
        """,
        (hero_id, position, levels),
    ).fetchall()

    order = {}

    for row in rows:
        if row["ord"] in order:
            continue

        order[row["ord"]] = {
            "level": row["ord"],
            "ability": row["name"],
            "share": None,
            "winrate": round(row["wins"] / row["total"] * 100, 1),
            "picks": row["total"],
        }

    totals = {}

    for row in rows:
        totals[row["ord"]] = totals.get(row["ord"], 0) + row["total"]

    for level, entry in order.items():
        entry["share"] = round(entry["picks"] / totals[level] * 100, 1)

    return [order[level] for level in sorted(order)]


def _talents(connection, hero_id, position):
    """Таланты по слотам: первый взятый — это уровень 10, дальше 15, 20, 25."""

    rows = connection.execute(
        """
        SELECT ord, name, SUM(win) AS wins, COUNT(*) AS total
        FROM (
            SELECT a.name AS name, mp.win AS win,
                   ROW_NUMBER() OVER (
                       PARTITION BY pt.match_id, pt.player_slot
                       ORDER BY pt.time
                   ) AS ord
            FROM player_talents pt
            JOIN match_players mp
              ON mp.match_id = pt.match_id AND mp.player_slot = pt.player_slot
            JOIN abilities a ON a.id = pt.talent_id
            WHERE mp.hero_id = ? AND mp.position = ?
        )
        WHERE ord <= 4
        GROUP BY ord, name
        ORDER BY ord, total DESC
        """,
        (hero_id, position),
    ).fetchall()

    levels = {1: 10, 2: 15, 3: 20, 4: 25}
    totals = {}

    for row in rows:
        totals[row["ord"]] = totals.get(row["ord"], 0) + row["total"]

    result = []
    taken = set()

    for row in rows:
        if row["ord"] in taken:
            continue

        taken.add(row["ord"])

        result.append(
            {
                "level": levels[row["ord"]],
                "talent": row["name"],
                "share": round(row["total"] / totals[row["ord"]] * 100, 1),
                "winrate": round(row["wins"] / row["total"] * 100, 1),
            }
        )

    return result


def _neutral(connection, players):
    counts = {}

    for row in players.values():
        item_id = row["neutral_item_id"]

        if item_id:
            counts[item_id] = counts.get(item_id, 0) + 1

    if not counts:
        return None

    best = max(counts, key=counts.get)

    name = connection.execute(
        "SELECT name FROM items WHERE id = ?", (best,)
    ).fetchone()

    return {
        "item": name["name"] if name else str(best),
        "share": round(counts[best] / len(players) * 100, 1),
    }


def build_guide(hero_id, position):
    """Собирает руководство по одному герою на одной позиции."""

    connection = get_connection()

    players = _players(connection, hero_id, position)
    total = len(players)

    if total < MIN_MATCHES:
        connection.close()

        return {
            "hero_id": hero_id,
            "position": position,
            "matches": total,
            "enough_data": False,
        }

    item_rows = _items(connection, hero_id, position)
    components = load_components(connection)
    wins = sum(1 for row in players.values() if row["win"])
    base_winrate = wins / total * 100

    guide = {
        "hero_id": hero_id,
        "position": position,
        "matches": total,
        "winrate": round(base_winrate, 1),
        "enough_data": True,
        "starting": _starting_items(item_rows, total),
        "early": _phase_items(
            item_rows, total, "early", False, base_winrate
        ),
        "core": _phase_items(
            item_rows, total, "core", True, base_winrate, components
        ),
        "late": _phase_items(
            item_rows, total, "late", True, base_winrate, components
        ),
        "skills": _skill_order(connection, hero_id, position),
        "talents": _talents(connection, hero_id, position),
        "neutral": _neutral(connection, players),
    }

    connection.close()

    return guide


def available_builds(min_matches=MIN_MATCHES):
    """Какие связки герой+позиция уже набрали достаточно матчей."""

    connection = get_connection()

    rows = connection.execute(
        """
        SELECT mp.hero_id, h.localized_name, mp.position, COUNT(*) AS games
        FROM match_players mp
        JOIN heroes h ON h.id = mp.hero_id
        WHERE mp.position IS NOT NULL
        GROUP BY mp.hero_id, mp.position
        HAVING games >= ?
        ORDER BY games DESC
        """,
        (min_matches,),
    ).fetchall()

    connection.close()

    return [dict(row) for row in rows]
