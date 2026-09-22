"""Приёмник Game State Integration: Dota 2 сама шлёт сюда состояние матча.

Игра делает POST на локальный порт каждые несколько долей секунды.
Никаких обращений к игре с нашей стороны нет — только приём, поэтому
это законный и штатный механизм Valve, а не чтение чужой памяти.

Запуск разведки:  python -m gsi.server
"""

import json
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


PORT = 3000

# Куда складываем присланное — чтобы потом разобрать, что игра вообще даёт.
DUMP_DIR = Path(__file__).resolve().parent.parent / "data" / "gsi"

# Последнее состояние держим в памяти: его будет читать оверлей.
LATEST = {"state": None, "updated_at": 0}

# Какие наборы блоков уже видели — чтобы не засорять вывод повторами.
_seen_shapes = set()

# Стадии матча, снимки которых уже сохранили.
_seen_stages = set()


def describe(state, prefix=""):
    """Короткое описание структуры: какие блоки пришли и что внутри."""

    lines = []

    for key, value in sorted(state.items()):
        if isinstance(value, dict):
            lines.append(f"{prefix}{key}: {len(value)} полей")
        elif isinstance(value, list):
            lines.append(f"{prefix}{key}: список из {len(value)}")
        else:
            lines.append(f"{prefix}{key} = {value}")

    return lines


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)

        self.send_response(200)
        self.end_headers()

        try:
            state = json.loads(raw)

        except json.JSONDecodeError:
            return

        LATEST["state"] = state
        LATEST["updated_at"] = time.time()

        # Всегда держим на диске последнее состояние: по нему смотрим,
        # что игра отдаёт прямо сейчас, а не когда набор блоков сменился.
        DUMP_DIR.mkdir(parents=True, exist_ok=True)

        (DUMP_DIR / "latest.json").write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # Отдельный снимок на каждую стадию матча: драфт проходит быстро,
        # и без этого момент с пиками легко потерять.
        stage = (state.get("map") or {}).get("game_state")

        if stage and stage not in _seen_stages:
            _seen_stages.add(stage)

            draft = state.get("draft") or {}
            minimap = state.get("minimap") or {}
            heroes = [
                u.get("unitname", "").replace("npc_dota_hero_", "")
                for u in minimap.values()
                if (u.get("unitname") or "").startswith("npc_dota_hero_")
            ]

            print(f"\n[стадия] {stage}")
            print(f"   draft: {'ПУСТО' if not draft else list(draft)}")
            print(f"   героев видно: {len(heroes)} {heroes}")

            (DUMP_DIR / f"stage_{stage}.json").write_text(
                json.dumps(state, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        shape = tuple(sorted(state))

        if shape not in _seen_shapes:
            _seen_shapes.add(shape)

            print(f"\n=== новый набор блоков: {', '.join(shape)} ===")

            for line in describe(state):
                print("   " + line)

            DUMP_DIR.mkdir(parents=True, exist_ok=True)

            name = "_".join(shape)[:60] or "empty"
            path = DUMP_DIR / f"{name}.json"

            path.write_text(
                json.dumps(state, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            print(f"   сохранено: {path}")

    def log_message(self, *args):
        # Стандартный лог HTTP-сервера забивает консоль на каждом пакете.
        pass


def run(port=PORT):
    print(f"GSI-приёмник слушает http://localhost:{port}/")
    print("Запусти Dota 2 и зайди в любой матч (подойдёт демо-режим).")
    print("Остановка — Ctrl+C.\n")

    HTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    run()
