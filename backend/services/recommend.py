"""Итоговая рекомендация: базовая сборка героя плюс поправка под вражеский пик."""

import json

from database.database import get_connection
from services.aggregator import build_guide
from services.matchups import lineup_from_cache


def load_guide(hero_id, position):
    """Берёт готовое руководство из БД; считает на лету только если его нет."""

    connection = get_connection()

    row = connection.execute(
        "SELECT data FROM guides WHERE hero_id = ? AND position = ?",
        (hero_id, position),
    ).fetchone()

    connection.close()

    if row:
        return json.loads(row["data"])

    return build_guide(hero_id, position)


# Насколько сильно должен сдвинуться предмет против пика, чтобы
# отметить его как ситуативно важный.
BOOST_THRESHOLD = 3.0


def recommend(hero_id, position, enemy_hero_ids=None):
    """Собирает руководство на матч.

    Базовая сборка отвечает на вопрос «что обычно берут на этом герое
    и этой позиции». Поправка по вражескому пику отвечает на вопрос
    «что в этой конкретной игре важнее обычного».
    """

    guide = load_guide(hero_id, position)

    if not guide["enough_data"]:
        return guide

    guide["enemies"] = list(enemy_hero_ids or [])

    if not enemy_hero_ids:
        guide["adjusted"] = False

        return guide

    lineup = lineup_from_cache(hero_id, enemy_hero_ids, top=40)

    boost = {entry["item"]: entry["score"] for entry in lineup["items"]}

    # Помечаем предметы базовой сборки, которые против этого пика
    # берут заметно чаще обычного.
    for phase in ("early", "core", "late"):
        for entry in guide[phase]:
            entry["boost"] = round(boost.get(entry["item"], 0.0), 1)

        guide[phase].sort(
            key=lambda entry: (-entry["boost"], -entry["share"])
        )

    in_build = {
        entry["item"]
        for phase in ("early", "core", "late")
        for entry in guide[phase]
    }

    # То, чего в типовой сборке нет, но против этого пика берут ощутимо чаще.
    guide["situational"] = [
        entry
        for entry in lineup["items"]
        if entry["item"] not in in_build and entry["score"] >= BOOST_THRESHOLD
    ][:8]

    guide["adjusted"] = True
    guide["enemies_used"] = lineup["enemies_used"]
    guide["enemies_skipped"] = lineup["enemies_skipped"]

    return guide
