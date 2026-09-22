"""Поправки к сборке под конкретный вражеский пик.

Точного совпадения пятёрки противников в данных не найти — комбинаций
слишком много. Поэтому вклад каждого врага считается отдельно, а потом
поправки складываются.
"""

from database.database import get_connection
from services.aggregator import CONSUMABLES


# Ниже этого числа встреч пара героев не считается: слишком мало игр,
# чтобы отличить закономерность от случайности.
MIN_PAIR_MATCHES = 40

# Предмет учитывается, только если его покупает хотя бы столько игроков
# в этом матчапе.
MIN_ITEM_BUYS = 15


def _pair_players(connection, hero_id, enemy_hero_id):
    """Игроки за hero_id в матчах, где во вражеской команде был enemy_hero_id."""

    rows = connection.execute(
        """
        SELECT mp.match_id, mp.player_slot, mp.win
        FROM match_players mp
        JOIN match_players enemy
          ON enemy.match_id = mp.match_id
         AND enemy.is_radiant != mp.is_radiant
         AND enemy.hero_id = ?
        WHERE mp.hero_id = ?
        """,
        (enemy_hero_id, hero_id),
    ).fetchall()

    return rows


def _item_buyers(connection, hero_id, enemy_hero_id):
    """Кто из этих игроков какие предметы покупал."""

    rows = connection.execute(
        """
        SELECT DISTINCT p.match_id, p.player_slot, i.name,
               i.display_name, i.is_base_component
        FROM match_players mp
        JOIN match_players enemy
          ON enemy.match_id = mp.match_id
         AND enemy.is_radiant != mp.is_radiant
         AND enemy.hero_id = ?
        JOIN player_purchases p
          ON p.match_id = mp.match_id AND p.player_slot = mp.player_slot
        JOIN items i ON i.id = p.item_id
        WHERE mp.hero_id = ?
        """,
        (enemy_hero_id, hero_id),
    ).fetchall()

    return rows


def _baseline_shares(connection, hero_id):
    """Как часто герой покупает каждый предмет вообще, без учёта врагов."""

    total = connection.execute(
        "SELECT COUNT(*) FROM match_players WHERE hero_id = ?",
        (hero_id,),
    ).fetchone()[0]

    if not total:
        return {}, 0

    rows = connection.execute(
        """
        SELECT i.name, COUNT(DISTINCT p.match_id || ':' || p.player_slot) AS buyers
        FROM match_players mp
        JOIN player_purchases p
          ON p.match_id = mp.match_id AND p.player_slot = mp.player_slot
        JOIN items i ON i.id = p.item_id
        WHERE mp.hero_id = ?
        GROUP BY i.name
        """,
        (hero_id,),
    ).fetchall()

    return {row["name"]: row["buyers"] / total for row in rows}, total


def item_effects(hero_id, enemy_hero_id):
    """Насколько иначе собираются против этого врага.

    Главный сигнал — сдвиг популярности: насколько чаще предмет берут
    против этого противника, чем против всех остальных. Винрейт здесь
    обманчив (дорогой предмет успевает купить тот, кто уже выигрывает),
    поэтому он идёт только справочно.
    """

    connection = get_connection()

    players = _pair_players(connection, hero_id, enemy_hero_id)
    total = len(players)

    if total < MIN_PAIR_MATCHES:
        connection.close()

        return {
            "hero_id": hero_id,
            "enemy_hero_id": enemy_hero_id,
            "matches": total,
            "enough_data": False,
            "items": [],
        }

    outcome = {
        (row["match_id"], row["player_slot"]): row["win"] for row in players
    }

    wins = sum(outcome.values())
    base_winrate = wins / total * 100

    baseline, _ = _baseline_shares(connection, hero_id)

    bought = {}
    names = {}

    for row in _item_buyers(connection, hero_id, enemy_hero_id):
        if row["name"].startswith("item_recipe_") or row["name"] in CONSUMABLES:
            continue

        # Сырые компоненты сигналят верно, но игроку бесполезны:
        # ему нужен Sange, а не Ogre Axe с Belt of Strength.
        if row["is_base_component"]:
            continue

        names[row["name"]] = row["display_name"] or row["name"]

        bought.setdefault(row["name"], set()).add(
            (row["match_id"], row["player_slot"])
        )

    connection.close()

    effects = []

    for name, buyers in bought.items():
        if len(buyers) < MIN_ITEM_BUYS:
            continue

        non_buyers = set(outcome) - buyers

        if len(non_buyers) < MIN_ITEM_BUYS:
            continue

        with_item = sum(outcome[key] for key in buyers) / len(buyers) * 100
        without_item = (
            sum(outcome[key] for key in non_buyers) / len(non_buyers) * 100
        )

        share = len(buyers) / total
        base_share = baseline.get(name, 0)

        effects.append(
            {
                "item": name,
                "display": names.get(name, name),
                # Основной сигнал: насколько чаще берут против этого врага.
                "shift": round((share - base_share) * 100, 1),
                "share": round(share * 100, 1),
                "base_share": round(base_share * 100, 1),
                # Справочно, доверять как причине нельзя.
                "winrate_delta": round(with_item - without_item, 1),
                "buyers": len(buyers),
            }
        )

    effects.sort(key=lambda entry: -entry["shift"])

    return {
        "hero_id": hero_id,
        "enemy_hero_id": enemy_hero_id,
        "matches": total,
        "base_winrate": round(base_winrate, 1),
        "enough_data": True,
        "items": effects,
    }


def lineup_from_cache(hero_id, enemy_hero_ids, top=40):
    """То же, что against_lineup, но по предрасчитанным таблицам.

    Считать матчапы запросами на лету — секунды на каждого врага.
    Оверлею нужны миллисекунды, поэтому берём готовое из matchup_items.
    """

    if not enemy_hero_ids:
        return {"hero_id": hero_id, "items": [], "enemies_used": [],
                "enemies_skipped": []}

    connection = get_connection()

    placeholders = ",".join("?" * len(enemy_hero_ids))

    covered = {
        row["enemy_hero_id"]
        for row in connection.execute(
            f"""
            SELECT enemy_hero_id FROM matchup_pairs
            WHERE hero_id = ? AND enemy_hero_id IN ({placeholders})
            """,
            [hero_id, *enemy_hero_ids],
        )
    }

    rows = connection.execute(
        f"""
        SELECT m.enemy_hero_id, m.item_id, m.shift, m.share,
               i.name, i.display_name
        FROM matchup_items m
        JOIN items i ON i.id = m.item_id
        WHERE m.hero_id = ? AND m.enemy_hero_id IN ({placeholders})
        """,
        [hero_id, *enemy_hero_ids],
    ).fetchall()

    connection.close()

    totals = {}

    for row in rows:
        entry = totals.setdefault(
            row["name"],
            {
                "item": row["name"],
                "display": row["display_name"] or row["name"],
                "score": 0.0,
                "from": [],
            },
        )

        entry["score"] += row["shift"]
        entry["from"].append(
            {
                "enemy_hero_id": row["enemy_hero_id"],
                "shift": row["shift"],
                "share": row["share"],
            }
        )

    ranked = sorted(totals.values(), key=lambda entry: -entry["score"])

    for entry in ranked:
        entry["score"] = round(entry["score"], 1)

    return {
        "hero_id": hero_id,
        "enemies_used": [e for e in enemy_hero_ids if e in covered],
        "enemies_skipped": [e for e in enemy_hero_ids if e not in covered],
        "items": ranked[:top],
    }


def against_lineup(hero_id, enemy_hero_ids, top=10):
    """Складывает поправки по всем врагам пика в один список.

    Предмет, который помогает против нескольких героев сразу,
    поднимается выше того, что хорош против одного.
    """

    totals = {}
    covered = []
    skipped = []

    for enemy_id in enemy_hero_ids:
        effect = item_effects(hero_id, enemy_id)

        if not effect["enough_data"]:
            skipped.append(enemy_id)

            continue

        covered.append(enemy_id)

        for entry in effect["items"]:
            item = totals.setdefault(
                entry["item"],
                {
                    "item": entry["item"],
                    "display": entry["display"],
                    "score": 0.0,
                    "from": [],
                },
            )

            item["score"] += entry["shift"]
            item["from"].append(
                {
                    "enemy_hero_id": enemy_id,
                    "shift": entry["shift"],
                    "share": entry["share"],
                }
            )

    ranked = sorted(totals.values(), key=lambda entry: -entry["score"])

    for entry in ranked:
        entry["score"] = round(entry["score"], 1)

    return {
        "hero_id": hero_id,
        "enemies_used": covered,
        "enemies_skipped": skipped,
        "items": ranked[:top],
    }
