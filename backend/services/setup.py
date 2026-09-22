"""Готова ли Dota отдавать нам данные.

Без двух вещей приложение молчит и выглядит сломанным:

1. Конфиг GSI в папке игры — им Dota узнаёт, куда слать состояние матча.
2. Параметр запуска `-gamestateintegration` — без него игра этот конфиг
   не читает вовсе.

Обе вещи можно проверить, а не просить игрока верить на слово: конфиг —
это файл, а параметры запуска Steam хранит у себя в localconfig.vdf.
Поэтому здесь именно проверка, а не напоминание: напоминание одинаково
мозолит глаза и тому, у кого всё настроено, и тому, у кого нет.
"""

import re
import sys
from pathlib import Path


# Имя нашего конфига. С приставкой gamestate_integration_ — иначе Dota
# его не подхватит, она перебирает файлы именно по ней.
CONFIG_NAME = "gamestate_integration_dota2helper.cfg"

LAUNCH_OPTION = "-gamestateintegration"

DOTA_APP_ID = "570"

# Путь к конфигу внутри установленной игры.
CONFIG_SUBPATH = Path("game") / "dota" / "cfg" / "gamestate_integration"

CONFIG_TEMPLATE = """"Dota2Helper"
{{
    "uri"           "http://localhost:{port}/"
    "timeout"       "5.0"
    "buffer"        "0.1"
    "throttle"      "0.1"
    "heartbeat"     "30.0"
    "data"
    {{
        "provider"      "1"
        "map"           "1"
        "player"        "1"
        "hero"          "1"
        "abilities"     "1"
        "items"         "1"
        "draft"         "1"
        "wearables"     "1"
        "buildings"     "1"
        "events"        "1"
        "minimap"       "1"
        "couriers"      "1"
        "neutralitems"  "1"
        "roshan"        "1"
        "allplayers"    "1"
        "player_ids"    "1"
    }}
}}
"""


def _tokens(text):
    """Строки и скобки из VDF по порядку.

    Полноценный разбор VDF нам не нужен: достаточно уметь спускаться по
    вложенным ключам. Формат простой — кавычки и фигурные скобки.
    """

    return re.findall(r'"((?:[^"\\]|\\.)*)"|([{}])', text)


def vdf_lookup(text, path):
    """Значение по пути ключей, регистр не важен.

    Steam пишет ключи то как `Software`, то как `software`, и в разных
    файлах по-разному. Полноценный разбор VDF нам не нужен: достаточно
    спуститься по вложенным блокам и забрать одну строку.
    """

    wanted = [part.lower() for part in path]

    stack = []
    pending = None

    for quoted, brace in _tokens(text):
        if brace == "{":
            stack.append(pending or "")
            pending = None

            continue

        if brace == "}":
            if stack:
                stack.pop()

            pending = None

            continue

        # Строки идут парами «ключ, значение», а ключ блока — последняя
        # строка перед открывающей скобкой.
        if pending is None:
            pending = quoted.lower()

            continue

        if stack + [pending] == wanted:
            return quoted

        pending = None

    return None


def steam_root():
    """Где стоит сам Steam."""

    if sys.platform == "win32":
        try:
            import winreg

            for hive, key in (
                (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"),
            ):
                try:
                    with winreg.OpenKey(hive, key) as handle:
                        for name in ("SteamPath", "InstallPath"):
                            try:
                                path = Path(winreg.QueryValueEx(handle, name)[0])

                            except OSError:
                                continue

                            if path.exists():
                                return path

                except OSError:
                    continue

        except ImportError:
            pass

    for guess in (
        Path(r"C:\Program Files (x86)\Steam"),
        Path(r"C:\Program Files\Steam"),
    ):
        if guess.exists():
            return guess

    return None


def dota_installs():
    """Все установки Доты, которые знает Steam.

    У игрока их может быть несколько — например, рабочая на системном
    диске и пустая заготовка в другой библиотеке. Проверять и чинить надо
    ту, в которой игра действительно стоит, поэтому возвращаем все.
    """

    root = steam_root()

    if root is None:
        return []

    libraries = [root]

    listing = root / "steamapps" / "libraryfolders.vdf"

    if listing.exists():
        try:
            text = listing.read_text(encoding="utf-8", errors="ignore")

        except OSError:
            text = ""

        # Библиотека нас интересует, только если в ней лежит сама Дота:
        # иначе мы предложим положить конфиг в пустую папку.
        for block in re.split(r'"\d+"\s*\{', text)[1:]:
            path = re.search(r'"path"\s*"([^"]+)"', block)

            if not path or f'"{DOTA_APP_ID}"' not in block:
                continue

            libraries.append(Path(path.group(1).replace("\\\\", "\\")))

    found = []

    for library in libraries:
        game = library / "steamapps" / "common" / "dota 2 beta"

        if game.exists() and game not in found:
            found.append(game)

    return found


def config_state(port=3000):
    """Где лежит наш конфиг GSI и лежит ли вообще."""

    installs = []

    for game in dota_installs():
        config = game / CONFIG_SUBPATH / CONFIG_NAME

        here = {
            "game": str(game),
            "config": str(config),
            "installed": config.exists(),
            "port_ok": False,
        }

        if here["installed"]:
            try:
                text = config.read_text(encoding="utf-8", errors="ignore")

            except OSError:
                text = ""

            here["port_ok"] = f"localhost:{port}" in text

        installs.append(here)

    return installs


def install_config(port=3000):
    """Кладёт конфиг во все найденные установки игры."""

    written = []

    for game in dota_installs():
        folder = game / CONFIG_SUBPATH

        try:
            folder.mkdir(parents=True, exist_ok=True)

            (folder / CONFIG_NAME).write_text(
                CONFIG_TEMPLATE.format(port=port), encoding="utf-8"
            )

            written.append(str(folder / CONFIG_NAME))

        except OSError as error:
            written.append(f"не получилось: {error}")

    return written


def launch_options():
    """Параметры запуска Доты у каждой учётки Steam на этом компьютере.

    Steam держит их в localconfig.vdf той учётки, под которой их задавали.
    Учёток бывает несколько, и нужная — та, под которой играют, поэтому
    возвращаем все и не гадаем.
    """

    root = steam_root()

    if root is None:
        return []

    accounts = []

    userdata = root / "userdata"

    if not userdata.exists():
        return []

    for folder in sorted(userdata.iterdir()):
        config = folder / "config" / "localconfig.vdf"

        if not config.exists():
            continue

        try:
            text = config.read_text(encoding="utf-8", errors="ignore")

        except OSError:
            continue

        options = vdf_lookup(
            text,
            [
                "UserLocalConfigStore",
                "Software",
                "Valve",
                "Steam",
                "apps",
                DOTA_APP_ID,
                "LaunchOptions",
            ],
        )

        # Учётка без Доты в списке приложений нам ничего не говорит —
        # это чужой аккаунт с этого же компьютера.
        if options is None:
            continue

        accounts.append(
            {
                "account": folder.name,
                "options": options,
                "ok": LAUNCH_OPTION in options.lower(),
                "modified": config.stat().st_mtime,
            }
        )

    return accounts


def report(port=3000, heard_game=False):
    """Всё, что нужно знать о готовности, одним куском — для экрана."""

    installs = config_state(port)
    accounts = launch_options()

    config_ok = any(item["installed"] and item["port_ok"] for item in installs)

    # Достаточно одной учётки с параметром: играют под какой-то одной, и
    # какой именно — мы не знаем.
    launch_ok = any(item["ok"] for item in accounts)

    return {
        "dota_found": bool(installs),
        "installs": installs,
        "config_ok": config_ok,
        "accounts": accounts,
        "launch_ok": launch_ok,
        # Пакеты от игры — доказательство сильнее любой проверки файлов.
        "heard_game": bool(heard_game),
        "option": LAUNCH_OPTION,
        "ready": bool(heard_game) or (config_ok and launch_ok),
    }
