"""Предрасчёт матчапов одним проходом по всем матчам.

Считать каждую пару героев отдельным запросом нельзя: пар около 16 000,
и это часы работы. Вместо этого идём по матчам пачками, и каждый матч
сразу разносим по всем 25 парам «мой герой против вражеского».
"""

from database.database import get_connection
from services.aggregator import CONSUMABLES, collapse_to_final, load_components
from services.patches import Weights


# Сколько матчей обрабатываем за раз. Ограничивает память: держать
# все покупки сразу — это миллионы строк.
BATCH_SIZE = 2000

# Пара героев попадает в таблицу, только если встречалась достаточно часто.
MIN_PAIR_MATCHES = 40

# Предмет учитывается, если его в этой паре покупали хотя бы столько раз.
MIN_ITEM_BUYS = 10

# Мелкие сдвиги — шум, хранить их смысла нет.
MIN_ABS_SHIFT = 1.0


def _load_batch(connection, match_ids, components, consumable_ids):
    """Возвращает по каждому игроку пачки его финальный набор предметов."""

    placeholders = ",".join("?" * len(match_ids))

    players = connection.execute(
        f"""
        SELECT match_id, player_slot, hero_id, is_radiant, win
        FROM match_players
        WHERE match_id IN ({placeholders})
        """,
        match_ids,
    ).fetchall()

    purchases = connection.execute(
        f"""
        SELECT p.match_id, p.player_slot, p.item_id, i.name
        FROM player_purchases p
        JOIN items i ON i.id = p.item_id
        WHERE p.match_id IN ({placeholders})
        """,
        match_ids,
    ).fetchall()

    by_player = {}

    for row in purchases:
        if row["item_id"] in consumable_ids:
            continue

        by_player.setdefault((row["match_id"], row["player_slot"]), {})[
            row["name"]
        ] = row["item_id"]

    items = {}

    for key, name_to_id in by_player.items():
        kept = collapse_to_final(set(name_to_id), components)

        items[key] = {name_to_id[name] for name in kept}

    return players, items


def _consumable_ids(connection):
    """Расходники и рецепты — в анализ матчапов не идут."""

    rows = connection.execute(
        """
        SELECT id, name FROM items
        WHERE name LIKE 'item_recipe_%' OR is_base_component = 1
        """
    ).fetchall()

    ids = {row["id"] for row in rows}

    for name in CONSUMABLES:
        row = connection.execute(
            "SELECT id FROM items WHERE name = ?", (name,)
        ).fetchone()

        if row:
            ids.add(row["id"])

    return ids


def build_matchups(progress=True, current=None):
    """Пересчитывает таблицы matchup_pairs и matchup_items с нуля."""

    connection = get_connection()

    components = load_components(connection)
    consumable_ids = _consumable_ids(connection)

    times = {
        row["id"]: row["start_time"]
        for row in connection.execute("SELECT id, start_time FROM matches")
    }

    weights = Weights(current=current)

    match_ids = list(times)

    # Всюду пара чисел: штуки и вес. Штуки решают, хватает ли данных
    # (порог должен означать «столько раз это правда видели»), а доли
    # считаются по весу — матч старого патча говорит тише.
    #
    # pair[(hero, enemy)] = [встреч, побед, вес]
    pair = {}
    # pair_items[(hero, enemy)][item] = [покупок, вес]
    pair_items = {}
    # base[hero] = [сыгранных, вес]
    base = {}
    # base_items[hero][item] = вес покупок
    base_items = {}

    done = 0

    for start in range(0, len(match_ids), BATCH_SIZE):
        batch = match_ids[start:start + BATCH_SIZE]

        players, items = _load_batch(
            connection, batch, components, consumable_ids
        )

        by_match = {}

        for row in players:
            by_match.setdefault(row["match_id"], []).append(row)

        for match_id, roster in by_match.items():
            weight = weights.of(times.get(match_id))

            for player in roster:
                hero = player["hero_id"]
                key = (match_id, player["player_slot"])
                bought = items.get(key, set())

                seen = base.setdefault(hero, [0, 0.0])
                seen[0] += 1
                seen[1] += weight

                hero_items = base_items.setdefault(hero, {})

                for item_id in bought:
                    hero_items[item_id] = hero_items.get(item_id, 0) + weight

                for enemy in roster:
                    if enemy["is_radiant"] == player["is_radiant"]:
                        continue

                    pair_key = (hero, enemy["hero_id"])

                    counters = pair.setdefault(pair_key, [0, 0, 0.0])
                    counters[0] += 1
                    counters[1] += player["win"]
                    counters[2] += weight

                    slot = pair_items.setdefault(pair_key, {})

                    for item_id in bought:
                        buys = slot.setdefault(item_id, [0, 0.0])
                        buys[0] += 1
                        buys[1] += weight

        done += len(batch)

        if progress:
            print(f"  обработано матчей: {done}/{len(match_ids)}")

    connection.execute("DELETE FROM matchup_pairs")
    connection.execute("DELETE FROM matchup_items")

    connection.executemany(
        """
        INSERT INTO matchup_pairs (hero_id, enemy_hero_id, matches, wins)
        VALUES (?, ?, ?, ?)
        """,
        [
            (hero, enemy, counters[0], counters[1])
            for (hero, enemy), counters in pair.items()
            if counters[0] >= MIN_PAIR_MATCHES
        ],
    )

    rows = []

    for (hero, enemy), counters in pair.items():
        total = counters[0]

        if total < MIN_PAIR_MATCHES or not counters[2]:
            continue

        hero_total = base.get(hero, [0, 0.0])

        if not hero_total[1]:
            continue

        for item_id, buys in pair_items.get((hero, enemy), {}).items():
            if buys[0] < MIN_ITEM_BUYS:
                continue

            share = buys[1] / counters[2] * 100
            base_share = (
                base_items.get(hero, {}).get(item_id, 0) / hero_total[1] * 100
            )
            shift = share - base_share

            if abs(shift) < MIN_ABS_SHIFT:
                continue

            rows.append(
                (
                    hero,
                    enemy,
                    item_id,
                    buys[0],
                    round(share, 2),
                    round(base_share, 2),
                    round(shift, 2),
                )
            )

    connection.executemany(
        """
        INSERT INTO matchup_items (
            hero_id, enemy_hero_id, item_id, buyers, share, base_share, shift
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )

    connection.commit()
    connection.close()

    return {
        "matches": len(match_ids),
        "pairs": sum(1 for c in pair.values() if c[0] >= MIN_PAIR_MATCHES),
        "item_rows": len(rows),
    }


def build_guides(progress=True):
    """Считает и складывает в БД руководства по всем связкам герой+позиция."""

    import json

    from services.aggregator import available_builds, build_guide

    builds = available_builds()

    connection = get_connection()

    connection.execute("DELETE FROM guides")
    connection.commit()
    connection.close()

    saved = 0

    for index, build in enumerate(builds, start=1):
        guide = build_guide(build["hero_id"], build["position"])

        if not guide["enough_data"]:
            continue

        connection = get_connection()

        connection.execute(
            """
            INSERT OR REPLACE INTO guides (hero_id, position, matches, data)
            VALUES (?, ?, ?, ?)
            """,
            (
                build["hero_id"],
                build["position"],
                guide["matches"],
                json.dumps(guide, ensure_ascii=False),
            ),
        )

        connection.commit()
        connection.close()

        saved += 1

        if progress and index % 25 == 0:
            print(f"  посчитано {index}/{len(builds)}")

    return saved
