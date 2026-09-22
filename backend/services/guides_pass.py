"""Расчёт всех руководств одним проходом по матчам.

Считать каждую связку герой+позиция отдельным запросом слишком дорого:
на 500+ связок это часы, потому что каждый раз перечитывается таблица
покупок. Здесь мы идём по матчам пачками и сразу раскладываем каждого
игрока в накопители его связки.
"""

import json

from database.database import get_connection
from services.patches import Weights
from services.aggregator import (
    CONSUMABLES,
    MIN_MATCHES,
    MIN_SHARE,
    PHASES,
    _phase_of,
    _phase_of_median,
    collapse_to_final,
    load_components,
)


BATCH_SIZE = 2000

# Тайминги храним гистограммой по минутам, а не списком: списки на
# миллионах покупок съедают память, а для медианы хватает корзин.
MAX_MINUTE = 90

# Сколько самых частых стартовых закупок хранить: из них движок выберет
# ту, что влезает в текущее золото игрока.
STARTING_SETS = 8


def _median_from_histogram(histogram):
    total = sum(histogram.values())

    if not total:
        return 0

    seen = 0

    for minute in sorted(histogram):
        seen += histogram[minute]

        if seen * 2 >= total:
            return minute * 60

    return 0


class Accumulator:
    """Копилка по одной связке герой+позиция."""

    def __init__(self):
        # Вес: сумма весов матчей, по ней считаются все доли. Матч старого
        # патча весит меньше свежего — см. services/patches.
        self.total = 0.0
        self.wins = 0.0
        # А это штуки: сколько матчей легло в эту копилку на самом деле.
        # По ним решается, хватает ли данных вообще, и это же число
        # показывается игроку — «N игр» должно означать N игр.
        self.players = 0
        # предмет -> [покупателей, побед, {минута: сколько}].
        # Фазы здесь нет намеренно: предмет попадает в фазу по медиане
        # своего тайминга, а не по тому, в какое окно угодила отдельная
        # покупка. Иначе один и тот же предмет раздваивался между
        # «ранней» и «кором» только потому, что кто-то успел к 9:50.
        self.items = {}
        # Расходники живут отдельно: их берут по нескольку раз за игру,
        # и в ряду крупных предметов они только мешают.
        self.consumables = {}
        # стартовая закупка считается штуками, а не игроками
        self.start_counts = {}
        # целые стартовые закупки: (предмет, штук)... -> сколько игроков так взяли
        self.start_sets = {}
        # уровень -> способность -> [взятий, побед]
        self.skills = {}
        # талант -> [взятий, побед], без порядка взятия
        self.talents = {}
        self.neutrals = {}


def _collect_batch(connection, match_ids, components, consumables):
    placeholders = ",".join("?" * len(match_ids))

    players = connection.execute(
        f"""
        SELECT match_id, player_slot, hero_id, position, win, neutral_item_id
        FROM match_players
        WHERE match_id IN ({placeholders}) AND position IS NOT NULL
        """,
        match_ids,
    ).fetchall()

    purchases = connection.execute(
        f"""
        SELECT p.match_id, p.player_slot, p.item_id, p.purchase_time,
               i.name, i.is_base_component
        FROM player_purchases p
        JOIN items i ON i.id = p.item_id
        WHERE p.match_id IN ({placeholders})
        """,
        match_ids,
    ).fetchall()

    abilities = connection.execute(
        f"""
        SELECT pa.match_id, pa.player_slot, pa.ability_id, pa.time,
               COALESCE(a.display_name, a.name) AS name
        FROM player_abilities pa
        JOIN abilities a ON a.id = pa.ability_id
        WHERE pa.match_id IN ({placeholders})
        ORDER BY pa.match_id, pa.player_slot, pa.time
        """,
        match_ids,
    ).fetchall()

    talents = connection.execute(
        f"""
        SELECT pt.match_id, pt.player_slot, pt.time,
               COALESCE(a.display_name, a.name) AS name
        FROM player_talents pt
        JOIN abilities a ON a.id = pt.talent_id
        WHERE pt.match_id IN ({placeholders})
        ORDER BY pt.match_id, pt.player_slot, pt.time
        """,
        match_ids,
    ).fetchall()

    return players, purchases, abilities, talents


def build_all(progress=True, limit=None, current=None):
    connection = get_connection()

    components = load_components(connection)

    names = {}
    costs = {}

    for row in connection.execute("SELECT name, display_name, cost FROM items"):
        names[row["name"]] = row["display_name"] or row["name"]
        costs[row["name"]] = row["cost"] or 0

    # Время начала нужно, чтобы понять патч, а патч — чтобы понять вес.
    times = {
        row["id"]: row["start_time"]
        for row in connection.execute("SELECT id, start_time FROM matches")
    }

    # current — чем считать нынешним патчем. Нужен для проверки правил
    # на уже собранных матчах: по свежему патчу игр ещё нет.
    weights = Weights(current=current)

    match_ids = list(times)

    # limit — только для проверки правок: полный проход идёт минуты.
    if limit:
        match_ids = match_ids[:limit]

    buckets = {}
    done = 0

    for start in range(0, len(match_ids), BATCH_SIZE):
        batch = match_ids[start:start + BATCH_SIZE]

        players, purchases, abilities, talents = _collect_batch(
            connection, batch, components, CONSUMABLES
        )

        # Веса пачки считаем здесь, а не внутри _collect_batch: тому нужны
        # только строки из базы, а вес — это уже наше правило.

        slot_key = {}

        mass = {}

        for row in players:
            key = (row["match_id"], row["player_slot"])
            slot_key[key] = (row["hero_id"], row["position"])

            weight = weights.of(times.get(row["match_id"]))
            mass[key] = weight

            bucket = buckets.setdefault(slot_key[key], Accumulator())
            bucket.total += weight
            bucket.wins += row["win"] * weight
            bucket.players += 1

            if row["neutral_item_id"]:
                bucket.neutrals[row["neutral_item_id"]] = (
                    bucket.neutrals.get(row["neutral_item_id"], 0) + weight
                )

        wins = {
            (r["match_id"], r["player_slot"]): r["win"] * mass[
                (r["match_id"], r["player_slot"])
            ]
            for r in players
        }

        # Сначала узнаём, что каждый игрок собрал в итоге,
        # чтобы выбросить полуфабрикаты.
        owned = {}

        for row in purchases:
            key = (row["match_id"], row["player_slot"])

            if key in slot_key:
                owned.setdefault(key, set()).add(row["name"])

        final = {
            key: collapse_to_final(items, components)
            for key, items in owned.items()
        }

        # (игрок, предмет) -> когда предмет появился у него впервые.
        # Считаем именно первый раз: покупки в логе не упорядочены, а
        # второй Wraith Band не делает тайминг первого хуже.
        first = {}
        first_consumable = {}
        opening = {}

        for row in purchases:
            key = (row["match_id"], row["player_slot"])
            target = slot_key.get(key)

            if target is None:
                continue

            bucket = buckets[target]
            name = row["name"]

            if _phase_of(row["purchase_time"]) == "start":
                bucket.start_counts[name] = (
                    bucket.start_counts.get(name, 0) + mass[key]
                )

                # Внутри одной закупки считаем штуки, а не вес: «две ветки
                # и тангошки» — это набор предметов, весит он целиком.
                basket = opening.setdefault(key, {})
                basket[name] = basket.get(name, 0) + 1

                continue

            if name.startswith("item_recipe_"):
                continue

            moment = row["purchase_time"]

            if name in CONSUMABLES:
                marker = (key, name)

                if moment < first_consumable.get(marker, 10 ** 9):
                    first_consumable[marker] = moment

                continue

            # Полуфабрикаты и сырые компоненты игроку не советуют: ему
            # нужен Sange and Yasha, а не Yasha с Ogre Axe.
            if row["is_base_component"]:
                continue

            if name not in final.get(key, set()):
                continue

            marker = (key, name)

            if moment < first.get(marker, 10 ** 9):
                first[marker] = moment

        for source, field in ((first, "items"), (first_consumable, "consumables")):
            for (key, name), moment in source.items():
                store = getattr(buckets[slot_key[key]], field)
                entry = store.setdefault(name, [0, 0, {}])

                entry[0] += mass[key]
                entry[1] += wins.get(key, 0)

                minute = min(max(moment // 60, 0), MAX_MINUTE)
                entry[2][minute] = entry[2].get(minute, 0) + mass[key]

        for key, basket in opening.items():
            bucket = buckets[slot_key[key]]
            signature = tuple(sorted(basket.items()))
            bucket.start_sets[signature] = (
                bucket.start_sets.get(signature, 0) + mass[key]
            )

        _accumulate_order(abilities, slot_key, buckets, wins, mass, "skills", 10)
        _accumulate_picks(talents, slot_key, buckets, wins, mass, "talents")

        done += len(batch)

        if progress:
            print(f"  обработано матчей: {done}/{len(match_ids)}")

    connection.close()

    return _store(buckets, names, costs)


def _accumulate_order(rows, slot_key, buckets, wins, mass, field, limit):
    """Раскладывает прокачку и таланты по порядковому номеру взятия."""

    current = None
    order = 0

    for row in rows:
        key = (row["match_id"], row["player_slot"])

        if key != current:
            current = key
            order = 0

        order += 1

        if order > limit:
            continue

        target = slot_key.get(key)

        if target is None:
            continue

        store = getattr(buckets[target], field)
        entry = store.setdefault(order, {}).setdefault(row["name"], [0, 0])

        entry[0] += mass.get(key, 0)
        entry[1] += wins.get(key, 0)


def _accumulate_picks(rows, slot_key, buckets, wins, mass, field):
    """Сколько игроков взяли каждый талант — неважно, каким по счёту.

    По порядку взятия считать нельзя: кто берёт талант 10-го уровня
    последним, сдвигает всю картину. У Abaddon на оффлейне четвёртым
    по счёту чаще всего шёл талант 10-го уровня, и что берут на 15-м,
    в гайд не попадало вовсе. Уровень и сторону потом ставит дерево.
    """

    seen = set()

    for row in rows:
        key = (row["match_id"], row["player_slot"])
        target = slot_key.get(key)

        if target is None or (key, row["name"]) in seen:
            continue

        seen.add((key, row["name"]))

        store = getattr(buckets[target], field)
        entry = store.setdefault(row["name"], [0, 0])

        entry[0] += mass.get(key, 0)
        entry[1] += wins.get(key, 0)


def _top(counter_map):
    if not counter_map:
        return None

    name, counters = max(counter_map.items(), key=lambda item: item[1][0])
    total = sum(value[0] for value in counter_map.values())

    return {
        "name": name,
        "share": round(counters[0] / total * 100, 1),
        "winrate": round(counters[1] / counters[0] * 100, 1),
    }


def _store(buckets, names, costs=None):
    connection = get_connection()

    connection.execute("DELETE FROM guides")

    saved = 0
    rows = []

    for (hero_id, position), bucket in buckets.items():
        # Порог — по настоящим матчам, а не по весу: иначе связка с
        # тысячей старых игр выглядела бы пустой только из-за возраста.
        if bucket.players < MIN_MATCHES or not bucket.total:
            continue

        base_winrate = bucket.wins / bucket.total * 100

        guide = {
            "hero_id": hero_id,
            "position": position,
            "matches": bucket.players,
            # Сумма весов: по ней считаются все доли ниже. Нужна для
            # отладки — понять, на чём именно стоит цифра.
            "weight": round(bucket.total, 1),
            "winrate": round(base_winrate, 1),
            "enough_data": True,
            "starting": sorted(
                [
                    {
                        "item": name,
                        "display": names.get(name, name),
                        "avg_count": round(count / bucket.total, 1),
                    }
                    for name, count in bucket.start_counts.items()
                    if count / bucket.total >= 0.2
                ],
                key=lambda entry: -entry["avg_count"],
            ),
        }

        costs = costs or {}
        with_opening = sum(bucket.start_sets.values()) or 1

        guide["starting_sets"] = [
            {
                "items": [
                    {
                        "item": name,
                        "display": names.get(name, name),
                        "count": count,
                        "cost": costs.get(name, 0),
                    }
                    for name, count in signature
                ],
                "total": sum(costs.get(name, 0) * count for name, count in signature),
                "share": round(players / with_opening * 100, 1),
            }
            for signature, players in sorted(
                bucket.start_sets.items(), key=lambda pair: -pair[1]
            )[:STARTING_SETS]
        ]

        phases = {"early": [], "core": [], "late": []}

        def described(name, buyers, item_wins, histogram):
            winrate = item_wins / buyers * 100

            return {
                "item": name,
                "display": names.get(name, name),
                "share": round(buyers / bucket.total * 100, 1),
                "winrate": round(winrate, 1),
                "lift": round(winrate - base_winrate, 1),
                "median_time": _median_from_histogram(histogram),
                "matches": buyers,
            }

        for name, (buyers, item_wins, histogram) in bucket.items.items():
            if buyers / bucket.total < MIN_SHARE:
                continue

            entry = described(name, buyers, item_wins, histogram)

            phases[_phase_of_median(entry["median_time"])].append(entry)

        # Расходники — всегда ранняя игра: докупают их и потом, но совет
        # «возьми тангошек» относится к началу.
        for name, (buyers, item_wins, histogram) in bucket.consumables.items():
            if buyers / bucket.total < MIN_SHARE:
                continue

            phases["early"].append(
                described(name, buyers, item_wins, histogram)
            )

        # Внутри фазы порядок — по времени: ряд читается слева направо
        # как порядок покупок. Популярность показывает подпись на иконке.
        for phase, items in phases.items():
            guide[phase] = sorted(
                items,
                key=lambda entry: (
                    entry["median_time"] or 10 ** 6,
                    -entry["share"],
                ),
            )

        guide["skills"] = [
            {
                "level": level,
                "ability": top["name"],
                "share": top["share"],
                "winrate": top["winrate"],
            }
            for level, top in (
                (level, _top(bucket.skills.get(level, {})))
                for level in sorted(bucket.skills)
            )
            if top
        ]

        # Все взятые таланты, без уровня: его и сторону ставит движок по
        # дереву героя, а там же сравнивает таланты внутри пары.
        guide["talents"] = sorted(
            (
                {
                    "talent": name,
                    "picked": round(taken / bucket.total * 100, 1),
                    "winrate": round(won / taken * 100, 1),
                }
                for name, (taken, won) in bucket.talents.items()
                if taken
            ),
            key=lambda entry: -entry["picked"],
        )

        if bucket.neutrals:
            best = max(bucket.neutrals, key=bucket.neutrals.get)

            guide["neutral"] = {
                "item_id": best,
                "share": round(
                    bucket.neutrals[best] / bucket.total * 100, 1
                ),
            }
        else:
            guide["neutral"] = None

        rows.append(
            (
                hero_id,
                position,
                bucket.players,
                json.dumps(guide, ensure_ascii=False),
            )
        )

        saved += 1

    connection.executemany(
        """
        INSERT OR REPLACE INTO guides (hero_id, position, matches, data)
        VALUES (?, ?, ?, ?)
        """,
        rows,
    )

    connection.commit()
    connection.close()

    return saved
