"""Повтор сохранённого пакета GSI — панель с реальной каткой, без игры.

Dota пишет своё состояние в data/gsi/: по одному снимку на стадию плюс
latest.json. Этот скрипт шлёт такой снимок в приложение ровно так же, как
это делает игра, поэтому панель показывает настоящую рекомендацию —
с героем, врагами и закупкой из той катки.

Запуск (при запущенном app.py):

    python replay.py                 — показ: старт матча с полным пиком
    python replay.py pre_game        — снимок до горна как есть
    python replay.py in_progress     — середина той же катки
    python replay.py latest          — последнее, что присылала игра
    python replay.py --list          — какие снимки вообще есть

Скрипт держит состояние, пока его не закрыть: Ctrl+C — и панель снова
ждёт игру.
"""

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


DUMP_DIR = Path(__file__).resolve().parent / "data" / "gsi"

URL = "http://localhost:3000/"

# Как часто повторяем пакет. Сама игра шлёт чаще, но здесь состояние не
# меняется, и повтор нужен только чтобы пережить перезапуск app.py.
EVERY = 3.0

# Короткие имена вместо DOTA_GAMERULES_STATE_*: их набирать руками.
STAGES = {
    "hero_selection": "stage_DOTA_GAMERULES_STATE_HERO_SELECTION",
    "strategy": "stage_DOTA_GAMERULES_STATE_STRATEGY_TIME",
    "showcase": "stage_DOTA_GAMERULES_STATE_TEAM_SHOWCASE",
    "loading": "stage_DOTA_GAMERULES_STATE_WAIT_FOR_MAP_TO_LOAD",
    "pre_game": "stage_DOTA_GAMERULES_STATE_PRE_GAME",
    "in_progress": "stage_DOTA_GAMERULES_STATE_GAME_IN_PROGRESS",
    "post_game": "stage_DOTA_GAMERULES_STATE_POST_GAME",
    "latest": "latest",
}

DEFAULT = "demo"

# Показ с полным пиком. Все сохранённые снимки сняты в демо-режиме, где
# вся десятка стоит за одну команду, — врагов в них попросту нет, и
# половина панели остаётся пустой. Поэтому для показа к настоящему
# снимку дописываем вражескую пятёрку: свой герой, золото, предметы и
# стадия остаются как в той катке, выдуман только пик противника.
DEMO_BASE = "pre_game"

DEMO_ENEMIES = [
    "npc_dota_hero_axe",
    "npc_dota_hero_lina",
    "npc_dota_hero_pudge",
    "npc_dota_hero_sven",
    "npc_dota_hero_bane",
]

# Команды в GSI: 2 — Radiant, 3 — Dire.
TEAMS = {"radiant": 2, "dire": 3}


def with_enemies(state, heroes):
    """Дописывает на миникарту вражескую пятёрку.

    Панель делит героев по команде из миникарты, поэтому и добавлять их
    надо туда же: ставим их за ту сторону, за которую игрок не играет.
    """

    # Сторону берём с миникарты по своему же герою: приложение делит
    # героев именно так, а блок player в снимках с ней расходится.
    own = (state.get("hero") or {}).get("name")
    mine = TEAMS.get((state.get("player") or {}).get("team_name"))

    for unit in (state.get("minimap") or {}).values():
        if unit.get("unitname") == own and unit.get("team") in (2, 3):
            mine = unit["team"]
            break

    theirs = 2 if mine == 3 else 3

    minimap = state.setdefault("minimap", {})

    for index, hero in enumerate(heroes):
        minimap[f"demo{index}"] = {
            "xpos": 0,
            "ypos": 0,
            "image": "minimap_heroicon",
            "team": theirs,
            "yaw": 0,
            "unitname": hero,
            "visionrange": 800,
        }

    return state


def dumps():
    """Снимки, которые реально лежат на диске."""

    return {
        short: DUMP_DIR / f"{name}.json"
        for short, name in STAGES.items()
        if (DUMP_DIR / f"{name}.json").exists()
    }


def describe(state):
    """Что именно мы сейчас отправляем — чтобы не гадать по панели."""

    hero = (state.get("hero") or {}).get("name") or "?"
    stage = (state.get("map") or {}).get("game_state") or "?"
    clock = (state.get("map") or {}).get("clock_time")
    gold = (state.get("player") or {}).get("gold")

    return f"{hero} · {stage} · время {clock} · золото {gold}"


def post(path, payload):
    request = urllib.request.Request(
        URL + path.lstrip("/"),
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )

    urllib.request.urlopen(request, timeout=5)


def current():
    with urllib.request.urlopen(URL + "state", timeout=5) as answer:
        return json.loads(answer.read())


def main():
    available = dumps()

    if not available:
        raise SystemExit(
            f"В {DUMP_DIR} нет ни одного снимка. Запусти игру хотя бы раз — "
            f"приложение сохранит их само."
        )

    choice = (sys.argv[1] if len(sys.argv) > 1 else DEFAULT).lstrip("-")

    if choice in ("list", "help"):
        print("Есть снимки:")
        print("  demo  — снимок до горна плюс выдуманный пик противника")

        for short in available:
            print(f"  {short}")

        return

    source = DEMO_BASE if choice == "demo" else choice

    if source not in available:
        raise SystemExit(
            f"Нет снимка «{choice}». Доступны: demo, {', '.join(available)}"
        )

    state = json.loads(available[source].read_text(encoding="utf-8"))

    if choice == "demo":
        state = with_enemies(state, DEMO_ENEMIES)

    # Своя нумерация матча. В снимках matchid нулевой, и приложение
    # считало бы все повторы одной и той же игрой: герои из прошлого
    # снимка оставались бы в пике. Свежий номер сбрасывает трекер, и
    # показ всегда начинается с чистого листа.
    state.setdefault("map", {})["matchid"] = int(time.time())

    print(f"Шлю: {describe(state)}")

    try:
        post("/", state)

    except (urllib.error.URLError, OSError):
        raise SystemExit(
            "Приложение не отвечает на localhost:3000. Сначала запусти app.py."
        )

    # Рекомендация считается не мгновенно, поэтому первый ответ читаем
    # с небольшой паузой — иначе печатаем «жду» на ровном месте.
    time.sleep(0.5)

    answer = current()

    if answer.get("waiting"):
        print("Приложение пакет приняло, но героя в нём не увидело.")
    elif answer.get("error"):
        print(f"Приложение ответило: {answer['error']}")
    else:
        print(
            f"Панель показывает: {answer['hero']} · {answer['position']}"
            f" · врагов известно: {answer['enemies_known']}"
        )

    print("Держу состояние. Ctrl+C — и панель снова ждёт игру.")

    try:
        while True:
            time.sleep(EVERY)
            post("/", state)

    except KeyboardInterrupt:
        print("\nОстановлено.")


if __name__ == "__main__":
    main()
