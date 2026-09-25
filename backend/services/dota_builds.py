"""Наши сборки в магазине самой Доты.

Панель поверх игры легко забыть, а список предметов в магазине перед
глазами при каждой покупке. Поэтому основную сборку (старт, ранняя игра,
кор, поздняя) кладём туда, где Дота берёт «Стандартные предметы», —
а ситуативное против пика, прокачка и таланты остаются на панели.

Что проверено на живой игре (25.09.2026):

* Дота читает `game/dota/itembuilds/default_<герой>.txt` с диска сама.
  Руководства из userdata/…/guides/ она берёт только через Steam Cloud,
  и положенный туда файл не видит вовсе.
* Понимает только старый формат `"itembuilds"` и только предметы:
  прокачку и таланты из этого файла игра не показывает.
* Файл героя читается один раз за запуск игры — при его пике или первом
  открытии. Под конкретный матч сборку так не подстроить, поэтому она
  одна на героя, по его самой частой роли.

Чужие сборки перед первой заменой сохраняются у нас и возвращаются
кнопкой: папку до нас могла заполнить другая программа.
"""

import shutil
import struct
import zlib
from pathlib import Path

from config import USER_DIR
from services import dota
from services import setup as setup_check


AUTHOR = "Dota2Helper"

BUILDS_SUBPATH = Path("game") / "dota" / "itembuilds"

# Файл, которого нет в пустой заготовке Доты в другой библиотеке Steam.
GAME_MARKER = Path("game") / "dota" / "gameinfo.gi"

BACKUP_DIR = USER_DIR / "itembuilds_backup"

ROLE_NAMES = {
    "POSITION_1": "керри",
    "POSITION_2": "мид",
    "POSITION_3": "оффлейн",
    "POSITION_4": "роумер",
    "POSITION_5": "саппорт",
}

PHASES = [
    ("early", "Ранняя игра"),
    ("core", "Кор"),
    ("late", "Поздняя игра"),
]


def folders():
    """Папки сборок во всех установках, где игра действительно стоит."""

    return [
        game / BUILDS_SUBPATH
        for game in setup_check.dota_installs()
        if (game / GAME_MARKER).exists()
    ]


def is_ours(path):
    try:
        head = path.read_text(encoding="utf-8", errors="ignore")[:200]

    except OSError:
        return False

    return f'"{AUTHOR}"' in head


def build_text(engine, hero):
    """Сборка героя в формате, который понимает магазин Доты.

    Считается тем же движком, что и панель, только без врагов: в магазине
    должно стоять то же, что игрок видит на панели в начале матча.
    """

    advice = engine.recommend(hero)

    if advice.get("error"):
        return None

    role = ROLE_NAMES.get(advice["position"], "")

    sections = []

    # Стартовая закупка — целым набором, как её собирают игроки. Две
    # ветки — это две иконки, иначе в магазине не видно, сколько брать.
    start = []

    for entry in advice["starting_purchase"]["items"]:
        start += [entry["item"]] * max(1, entry.get("count", 1))

    sections.append((f"Старт · {role}" if role else "Старт", start))

    for phase, title in PHASES:
        sections.append((title, [entry["item"] for entry in advice[phase]]))

    lines = [
        '"itembuilds"',
        "{",
        f'\t"Author"\t\t"{AUTHOR}"',
        f'\t"Hero"\t\t\t"npc_dota_hero_{hero}"',
        f'\t"Title"\t\t\t"{AUTHOR} · {advice["hero_display"]} · {role}"',
        "",
        '\t"Items"',
        "\t{",
    ]

    for title, items in sections:
        if not items:
            continue

        lines += [f'\t\t"{title}"', "\t\t{"]
        lines += [f'\t\t\t"item"\t\t"{item}"' for item in items]
        lines += ["\t\t}"]

    lines += ["\t}", "}", ""]

    return "\n".join(lines)


def _backup_for(folder):
    # Установок может быть несколько — копии каждой в своей папке.
    name = "".join(ch if ch.isalnum() else "_" for ch in str(folder))

    return BACKUP_DIR / name


def install(engine):
    """Кладёт наши сборки на всех героев, сохранив прежние."""

    texts = {}

    for hero in sorted(engine.hero_by_name):
        text = build_text(engine, hero)

        if text:
            texts[hero] = text

    written = 0
    places = []

    for folder in folders():
        folder.mkdir(parents=True, exist_ok=True)

        backup = _backup_for(folder)

        # Копию снимаем один раз, до первой замены. При повторной
        # установке в папке уже наши файлы, и копировать их незачем.
        if not backup.exists():
            backup.mkdir(parents=True)

            for path in folder.glob("default_*.txt"):
                if not is_ours(path):
                    shutil.copy2(path, backup / path.name)

        for hero, text in texts.items():
            (folder / f"default_{hero}.txt").write_text(
                text, encoding="utf-8", newline="\n"
            )

            written += 1

        places.append(str(folder))

    print(f"сборки в Доте: {len(texts)} героев в {len(places)} папк(ах)")

    return {"heroes": len(texts), "written": written, "folders": places}


def restore():
    """Убирает наши сборки и возвращает то, что лежало до нас."""

    returned = 0

    for folder in folders():
        backup = _backup_for(folder)

        for path in folder.glob("default_*.txt"):
            if is_ours(path):
                path.unlink()

        if backup.exists():
            for path in backup.glob("default_*.txt"):
                shutil.copy2(path, folder / path.name)
                returned += 1

            shutil.rmtree(backup, ignore_errors=True)

    print(f"сборки в Доте: вернули прежние ({returned})")

    return {"returned": returned}


def state():
    """Стоят ли наши сборки — для строки в настройках."""

    places = folders()

    if not places:
        return {"found": False, "installed": False, "count": 0, "backup": 0}

    folder = places[0]
    count = sum(1 for path in folder.glob("default_*.txt") if is_ours(path))

    backup = _backup_for(folder)
    saved = len(list(backup.glob("default_*.txt"))) if backup.exists() else 0

    return {
        "found": True,
        "installed": count > 0,
        "count": count,
        "backup": saved,
    }


def refresh(engine):
    """Пересобирает наши сборки по свежим данным, если они уже стоят.

    Выбор руководства у героев здесь не трогаем: если игрок потом сам
    выбрал в игре другое, обновление данных не должно это отменять.
    """

    if state()["installed"]:
        return install(engine)

    return None


# ---------------------------------------------------------------------------
# Какое руководство выбрано у героя.
#
# Дота помнит выбор по каждому герою в userdata/<учётка>/570/remote/cfg/
# herobuilds.cfg: «workshop: <номер>», «plus:<герой>», «cloud: guides/…» или
# «itembuild: <герой>» — последнее и есть «Стандартные предметы», то есть
# наш файл. Если у героя стоит руководство из Мастерской, нашу сборку игрок
# не увидит, пока не переключит сам — а у кого как, никто не помнит.
#
# Файл — двоичные KeyValues Valve: «VBKV», CRC32 остатка, дальше узлы
# «тип, имя\0, значение», где 0 — блок, 1 — строка, 2 — int32, 0x0B или
# 0x08 — конец блока. Писать его можно только при закрытой Доте: она
# держит выбор в памяти и на выходе перезапишет файл своим.

SELECTION_SUBPATH = Path("570") / "remote" / "cfg" / "herobuilds.cfg"

KV_BLOCK, KV_STRING, KV_INT = 0, 1, 2
KV_ENDS = (0x08, 0x0B)


def _read_kv(data):
    if data[:4] != b"VBKV":
        raise ValueError("не VBKV")

    body = data[8:]

    if zlib.crc32(body) != struct.unpack("<I", data[4:8])[0]:
        raise ValueError("контрольная сумма не сходится")

    pos = 0

    def text():
        nonlocal pos
        end = body.index(b"\0", pos)
        value = body[pos:end].decode("utf-8")
        pos = end + 1
        return value

    def block():
        nonlocal pos
        out = []

        while True:
            kind = body[pos]
            pos += 1

            if kind in KV_ENDS:
                return out

            key = text()

            if kind == KV_BLOCK:
                out.append((key, block()))
            elif kind == KV_STRING:
                out.append((key, text()))
            elif kind == KV_INT:
                out.append((key, struct.unpack("<i", body[pos:pos + 4])[0]))
                pos += 4
            else:
                raise ValueError(f"незнакомый тип узла {kind}")

    return block()


def _write_kv(nodes):
    def block(items):
        out = b""

        for key, value in items:
            name = key.encode("utf-8") + b"\0"

            if isinstance(value, list):
                out += bytes([KV_BLOCK]) + name + block(value)
            elif isinstance(value, int):
                out += bytes([KV_INT]) + name + struct.pack("<i", value)
            else:
                out += bytes([KV_STRING]) + name + value.encode("utf-8") + b"\0"

        return out + bytes([0x0B])

    body = block(nodes)

    return b"VBKV" + struct.pack("<I", zlib.crc32(body)) + body


def selection_path():
    root = setup_check.steam_root()
    account = setup_check.active_account()

    if root is None or not account:
        return None

    return root / "userdata" / account / SELECTION_SUBPATH


def _selection_backup(path):
    # Номер учётки в имени: копия одной учётки не должна уйти другой.
    return BACKUP_DIR / f"herobuilds_{path.parts[-5]}.cfg"


def _hero_choices(nodes):
    for key, value in nodes:
        if isinstance(value, list):
            if key == "HeroBuilds":
                return value

            found = _hero_choices(value)

            if found is not None:
                return found

    return None


def select_ours(heroes):
    """Ставит всем героям «Стандартные предметы» — то есть нашу сборку."""

    path = selection_path()

    if path is None or not path.exists():
        return {"selected": False, "reason": "no_file"}

    if dota.is_running():
        return {"selected": False, "reason": "dota_running"}

    data = path.read_bytes()
    nodes = _read_kv(data)
    choices = _hero_choices(nodes)

    if choices is None:
        return {"selected": False, "reason": "no_file"}

    backup = _selection_backup(path)

    if not backup.exists():
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_bytes(data)

    ours = {hero: f"itembuild: {hero}" for hero in heroes}
    seen = set()

    for index, (hero, _) in enumerate(choices):
        if hero in ours:
            choices[index] = (hero, ours[hero])
            seen.add(hero)

    choices.extend((hero, value) for hero, value in ours.items() if hero not in seen)

    path.write_bytes(_write_kv(nodes))

    print(f"сборки в Доте: выбраны у {len(ours)} героев")

    return {"selected": True}


def restore_selection():
    """Возвращает прежний выбор руководств, если мы его меняли."""

    path = selection_path()

    if path is None:
        return {"restored": False, "reason": "no_file"}

    backup = _selection_backup(path)

    if not backup.exists():
        return {"restored": False, "reason": "no_backup"}

    if dota.is_running():
        return {"restored": False, "reason": "dota_running"}

    shutil.copy2(backup, path)
    backup.unlink()

    return {"restored": True}


def selection_state(heroes):
    """Сколько героев смотрят на «Стандартные предметы»."""

    path = selection_path()

    try:
        choices = _hero_choices(_read_kv(path.read_bytes())) if path else None

    except (OSError, ValueError):
        choices = None

    if choices is None:
        return {"known": False, "ours": 0, "saved": False}

    picked = dict(choices)
    ours = sum(1 for hero in heroes if picked.get(hero, "").startswith("itembuild"))

    return {
        "known": True,
        "ours": ours,
        "saved": _selection_backup(path).exists(),
    }
