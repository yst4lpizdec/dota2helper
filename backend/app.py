"""Dota2Helper: приёмник GSI, трекер матча и движок рекомендаций в одном процессе.

Запуск:  python app.py

Игра шлёт состояние на localhost:3000, приложение держит snapshot в памяти
и отдаёт готовую рекомендацию на localhost:3000/state — её читает оверлей.
"""

import json
import re
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import parse_qs, urlparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from engine import Engine
from gsi.tracker import MatchTracker
from services.fonts import font_files
from config import USER_DIR
from services import dota_builds
from services.dota import is_running as dota_running
from services import setup as setup_check
from services import settings as settings_store
from services import updates
from services.snapshot import SNAPSHOT_PATH


PORT = 3000

BASE_DIR = Path(__file__).resolve().parent

# Снимки пакетов от игры — в личную папку: это отладочные данные, и
# в установленной программе писать их рядом с собой нельзя.
DUMP_DIR = USER_DIR / "gsi"
ICONS_DIR = BASE_DIR / "data" / "icons"
UI_DIR = BASE_DIR / "ui"

# Стадии, снимки которых уже сохранены за время работы приложения.
SEEN_STAGES = set()

# Как часто перезаписываем latest.json, в секундах.
DUMP_EVERY = 5.0

LAST_DUMP = [0.0]

# Свой заголовок, по которому Helper отличает просьбу уйти от чужого
# запроса: см. обработку /quit.
QUIT_HEADER = "X-Dota2Helper"

# Сколько секунд после последнего пакета GSI считаем, что игра идёт.
GSI_GRACE = 90

# Стадии до горна: пока идут они, золото на руках — это бюджет старта.
PRE_HORN_STAGES = {
    "DOTA_GAMERULES_STATE_HERO_SELECTION",
    "DOTA_GAMERULES_STATE_STRATEGY_TIME",
    "DOTA_GAMERULES_STATE_TEAM_SHOWCASE",
    "DOTA_GAMERULES_STATE_WAIT_FOR_MAP_TO_LOAD",
    "DOTA_GAMERULES_STATE_PRE_GAME",
}

# Стадии, на которых матч идёт и панели есть что показывать. Меню Доты,
# экран итогов и отключение сюда не входят: панель там только мешает.
MATCH_STAGES = {
    "DOTA_GAMERULES_STATE_INIT",
    "DOTA_GAMERULES_STATE_HERO_SELECTION",
    "DOTA_GAMERULES_STATE_STRATEGY_TIME",
    "DOTA_GAMERULES_STATE_TEAM_SHOWCASE",
    "DOTA_GAMERULES_STATE_WAIT_FOR_MAP_TO_LOAD",
    "DOTA_GAMERULES_STATE_WAIT_FOR_PLAYERS_TO_LOAD",
    "DOTA_GAMERULES_STATE_PRE_GAME",
    "DOTA_GAMERULES_STATE_GAME_IN_PROGRESS",
}

# Стадии, на которых пик уже определён и рекомендация имеет смысл.
LIVE_STAGES = {
    "DOTA_GAMERULES_STATE_STRATEGY_TIME",
    "DOTA_GAMERULES_STATE_PRE_GAME",
    "DOTA_GAMERULES_STATE_GAME_IN_PROGRESS",
}


def default_position():
    """Роль из настроек, если игрок её задал; иначе — угадывать."""

    chosen = settings_store.load().get("default_position") or None

    return chosen if chosen in POSITIONS else None


POSITIONS = {f"POSITION_{number}" for number in range(1, 6)}


class Helper:
    def __init__(self):
        print("загружаю snapshot...")

        started = time.time()

        self.engine = Engine()
        self.tracker = MatchTracker()
        self.lock = threading.Lock()
        self.result = None
        self.last_key = None
        # Кого игрок вручную отметил как противника на своей линии.
        self.lane_override = []
        # Позиция, выбранная игроком вручную. Пока пусто — угадываем сами.
        self.position_override = default_position()

        snapshot = self.engine.snapshot

        print(
            f"готово за {(time.time() - started) * 1000:.0f} мс: "
            f"патч {snapshot['patch']}, {snapshot['matches']} матчей, "
            f"{len(snapshot['guides'])} гайдов"
        )

        # Новая версия программы приходит со свежим snapshot — сборки в
        # магазине Доты, если игрок их ставил, догоняем по нему же.
        threading.Thread(target=self.refresh_builds, daemon=True).start()

    def refresh_builds(self):
        try:
            dota_builds.refresh(self.engine)

        except OSError as error:
            print(f"сборки в Доте не обновились: {error}")

    def _compute(self, info, lane, position):
        """Считает рекомендацию. Вызывается ВНЕ блокировки: пока идёт счёт,
        оверлей должен получать ответ на /state, иначе его окно висит."""

        result = self.engine.recommend(
            info["my_hero"],
            position=position,
            enemy_shorts=info["enemies"],
            owned=info["items"],
            ally_shorts=info["allies"],
            lane_against=lane,
            gold=self.start_budget(info),
        )
        result["match"] = info

        with self.lock:
            self.result = result

    def on_state(self, state):
        with self.lock:
            previous_match = self.tracker.match_id

            self.tracker.update(state)

            info = self.tracker.snapshot()

            # Новая игра — ручной выбор из прошлой уже не про неё. Роль
            # возвращается к той, что задана в настройках, или к догадке.
            if info["match_id"] != previous_match:
                self.lane_override = []
                self.position_override = default_position()

            # Матч кончился или игрок вернулся в меню — держать на экране
            # прошлую рекомендацию нельзя: в следующем матче она секунду
            # показывала бы чужого героя.
            if info["game_state"] not in MATCH_STAGES:
                self.result = None
                self.last_key = None

                return

            if not info["my_hero"]:
                return

            # Пересчитываем только когда что-то реально поменялось.
            key = (
                info["my_hero"],
                tuple(info["enemies"]),
                tuple(info["allies"]),
                tuple(info["items"]),
                tuple(self.lane_override),
                self.position_override,
                self.start_budget(info),
            )

            if key == self.last_key:
                return

            self.last_key = key
            lane = list(self.lane_override)
            position = self.position_override

        self._compute(info, lane, position)

        print(
            f"[{info['game_state']}] {info['my_hero']}"
            f" | врагов известно: {len(info['enemies'])}"
            f" | {', '.join(info['enemies']) or '-'}"
        )

    def set_lane(self, heroes):
        """Ручное уточнение: против кого игрок стоит на линии."""

        with self.lock:
            self.lane_override = list(heroes)
            # Сбрасываем ключ, иначе пересчёта не будет.
            self.last_key = None

            lane = list(self.lane_override)
            position = self.position_override

            info = self.tracker.snapshot()

        if info["my_hero"]:
            self._compute(info, lane, position)

    def set_position(self, position):
        """Ручное уточнение: на какой позиции играет сам игрок.

        Позицию мы выводим из пятёрки союзников, и на нестандартном пике
        она уезжает: Зевс на четвёрке получает сборку мида. Выбор игрока
        всегда главнее догадки.
        """

        with self.lock:
            self.position_override = position or None
            self.last_key = None

            lane = list(self.lane_override)
            chosen = self.position_override

            info = self.tracker.snapshot()

        if info["my_hero"]:
            self._compute(info, lane, chosen)

    def start_budget(self, info):
        """Бюджет стартовой закупки — золото на руках, но только до начала
        игры. Потом золото уже про следующие покупки, а не про старт."""

        if info["game_state"] in PRE_HORN_STAGES and info.get("gold"):
            return info["gold"]

        return None

    def heroes(self):
        """Все герои, по которым у нас вообще есть гайды."""

        snapshot = self.engine.snapshot
        known = set()

        for key in snapshot["guides"]:
            known.add(key.split(":", 1)[0])

        return {
            "built_at": snapshot["built_at"],
            "matches": snapshot["matches"],
            "heroes": sorted(
                (
                    {
                        "hero": value["name"].replace("npc_dota_hero_", ""),
                        "display": value["localized_name"],
                    }
                    for key, value in snapshot["heroes"].items()
                    if key in known
                ),
                key=lambda item: item["display"],
            ),
        }

    def guide(self, hero, position):
        """Рекомендация вне матча: только герой и позиция."""

        if not hero:
            return {"error": "герой не указан"}

        # Без врагов и без золота: это справочник, а не подсказка в игре.
        return self.engine.recommend(hero, position=position)

    def items(self):
        """Предметы, которые есть хотя бы в одной сборке.

        Раньше отдавали все предметы игры: ответ «его не берут почти
        нигде» тоже казался ответом. На деле половина пустых строк —
        удалённые из игры предметы, а остальные вроде Roshan's Banner
        никто и не покупает. Список засоряли, открыть по ним было нечего.
        """

        snapshot = self.engine.snapshot
        seen = self.engine.guide_items()

        return {
            "built_at": snapshot["built_at"],
            "matches": snapshot["matches"],
            "items": sorted(
                (
                    {
                        "item": value["name"],
                        "display": value["display"],
                        "cost": value.get("cost") or 0,
                        "guides": seen.get(value["name"], 0),
                    }
                    for value in snapshot["items"].values()
                    if not value["name"].startswith("item_recipe_")
                    and seen.get(value["name"])
                ),
                key=lambda entry: entry["display"],
            ),
        }

    def item(self, name):
        if not name:
            return {"error": "предмет не указан"}

        return self.engine.item_report(name)

    def updates(self, force=False):
        """Есть ли что-то новее: данные или сама программа."""

        found = updates.check(force=force)

        # Проверку версий выключили — не показываем и плашку о них. Кнопка
        # «проверить обновления» (force) спрашивает явно, ей отвечаем.
        if not force and not settings_store.load().get("check_app", True):
            found = {key: value for key, value in found.items() if key != "app"}

        snapshot = self.engine.snapshot

        return {
            "version": updates.VERSION,
            "patch": snapshot.get("patch"),
            "matches": snapshot.get("matches"),
            "built_at": snapshot.get("built_at"),
            **found,
        }

    def update_data(self):
        """Качает свежий snapshot и переучивает движок на ходу.

        Перезапуск не нужен: движок — это загруженный в память словарь,
        и заменить его можно между двумя пакетами от игры.
        """

        found = updates.check(force=True)

        if not (found.get("data") or {}).get("url"):
            return {"error": "свежих данных на GitHub нет"}

        report = updates.download_snapshot(found["data"]["url"])

        engine = Engine()

        with self.lock:
            self.engine = engine
            # Прошлая рекомендация посчитана по старым данным.
            self.result = None
            self.last_key = None

        self.refresh_builds()

        print(
            f"данные обновлены: патч {report['patch']}, "
            f"{report['matches']} матчей, {report['guides']} гайдов"
        )

        return {"ok": True, **report}

    def look_for_updates(self, delay=25, on_found=None):
        """Смотрит, нет ли свежих данных или новой версии, и только.

        Ничего не качает: решение за игроком. Задержка — чтобы не лезть
        в сеть одновременно с загрузкой snapshot и подъёмом окна.
        """

        def later():
            time.sleep(delay)

            try:
                found = updates.check()

            except Exception as error:
                print(f"проверка обновлений не удалась: {error}")

                return

            has_data = bool((found.get("data") or {}).get("newer"))
            has_app = bool(found.get("app")) and settings_store.load().get(
                "check_app", True
            )

            if (has_data or has_app) and on_found:
                on_found(found)

        threading.Thread(target=later, daemon=True).start()

    def update_app(self):
        """Скачивает установщик новой версии и запускает его."""

        found = updates.check(force=True)

        if not (found.get("app") or {}).get("url"):
            return {"error": "новой версии нет"}

        path = updates.download_installer(found["app"]["url"])

        if not updates.run_installer(path):
            return {"error": "запустить установщик не получилось",
                    "path": str(path)}

        return {"ok": True, "path": str(path)}

    def meta(self):
        return self.engine.meta()

    def counters(self, hero):
        if not hero:
            return {"error": "герой не указан"}

        return self.engine.counters(hero)

    def setup(self):
        """Готова ли Дота отдавать данные — и если нет, то чего не хватает."""

        return {
            **setup_check.report(
                port=PORT, heard_game=self.tracker.updated_at > 0
            ),
            "builds": {
                **dota_builds.state(),
                **dota_builds.selection_state(list(self.engine.hero_by_name)),
                "dota_running": dota_running(),
            },
        }

    def install_setup(self):
        written = setup_check.install_config(port=PORT)

        return {"written": written, **self.setup()}

    def install_builds(self):
        """Наши сборки — в «Стандартные предметы» магазина Доты."""

        try:
            dota_builds.install(self.engine)

            # У кого из героев в игре выбрано руководство из Мастерской,
            # тот нашу сборку не увидит — переключаем всех на неё. Только
            # по кнопке: при обновлении данных чужой выбор не трогаем.
            dota_builds.select_ours(list(self.engine.hero_by_name))

        except (OSError, ValueError) as error:
            return {"error": str(error), **self.setup()}

        return self.setup()

    def restore_builds(self):
        try:
            dota_builds.restore()
            dota_builds.restore_selection()

        except OSError as error:
            return {"error": str(error), **self.setup()}

        return self.setup()

    def current(self):
        with self.lock:
            # Пакеты от игры идут постоянно, пока Дота запущена. Оверлей
            # использует это как второй признак того, что играют: список
            # процессов может подвести, если игра называется иначе.
            live = (time.time() - self.tracker.updated_at) < GSI_GRACE

            # Идёт ли матч прямо сейчас. Пакеты GSI сами по себе этого не
            # доказывают: они идут и пока игрок сидит в меню Доты, а
            # панель должна вылезать на драфте и прятаться после итогов.
            in_match = live and self.tracker.game_state in MATCH_STAGES

            if not self.result:
                return {
                    "waiting": True,
                    "game_live": live,
                    "in_match": in_match,
                    "match": self.tracker.snapshot(),
                }

            return {**self.result, "game_live": live, "in_match": in_match}


HELPER = Helper()


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)

        # Просьба уйти от нового экземпляра. Заголовок обязателен: без
        # него страница в браузере могла бы выключить Helper обычной
        # формой, а свой заголовок ей поставить не дадут без нашего
        # разрешения на кросс-доменный запрос, которого мы не даём.
        if self.path.startswith("/quit"):
            if self.headers.get(QUIT_HEADER):
                self.send_body(b"{}", "application/json", cache=False)

                # shutdown() ждёт, пока остановится serve_forever, а тот
                # ждёт нас: из своего же обработчика звать нельзя.
                threading.Thread(
                    target=self.server.shutdown, daemon=True
                ).start()

            else:
                self.send_response(403)
                self.end_headers()

            return

        # Конфиг GSI кладём в папку игры только по явной просьбе с экрана:
        # молча писать в чужую установку игры нельзя. Ответ здесь нужен
        # настоящий, с телом, поэтому ветка стоит до пустого ответа ниже —
        # пакетам от игры отвечать нечем, и им хватает голого 200.
        # Обновления — тоже с телом ответа, поэтому здесь же, до
        # пустого ответа пакетам от игры.
        if self.path.startswith("/update-data"):
            self.answer(HELPER.update_data())

            return

        if self.path.startswith("/update-app"):
            self.answer(HELPER.update_app())

            return

        # Сборки в папке игры — тоже только по кнопке, как и конфиг.
        if self.path.startswith("/install-builds"):
            self.answer(HELPER.install_builds())

            return

        if self.path.startswith("/restore-builds"):
            self.answer(HELPER.restore_builds())

            return

        if self.path.startswith("/install-config"):
            self.send_body(
                json.dumps(
                    HELPER.install_setup(), ensure_ascii=False
                ).encode("utf-8"),
                "application/json; charset=utf-8",
                cache=False,
            )

            return

        self.send_response(200)
        self.end_headers()

        try:
            state = json.loads(raw)

        except json.JSONDecodeError:
            return

        # Клик в оверлее — не пакет игры, в дампы его не пишем.
        if self.path.startswith("/lane"):
            HELPER.set_lane(state.get("heroes") or [])

            return

        if self.path.startswith("/position"):
            HELPER.set_position(state.get("position"))

            return

        # Сначала считаем, потом пишем на диск: рекомендация нужна сразу,
        # а дампы — это отладка.
        HELPER.on_state(state)

        stage = (state.get("map") or {}).get("game_state")
        fresh_stage = bool(stage) and stage not in SEEN_STAGES
        now = time.monotonic()

        # Игра шлёт пакет каждые несколько долей секунды. Писать при этом
        # по 40 КБ на диск — верный способ поймать фризы прямо в бою,
        # поэтому latest.json обновляем раз в DUMP_EVERY секунд.
        if not fresh_stage and now - LAST_DUMP[0] < DUMP_EVERY:
            return

        LAST_DUMP[0] = now

        DUMP_DIR.mkdir(parents=True, exist_ok=True)

        dump = json.dumps(state, ensure_ascii=False, indent=2)

        (DUMP_DIR / "latest.json").write_text(dump, encoding="utf-8")

        # По одному снимку на стадию: latest.json затирается пустым пакетом
        # после выхода из матча, и разбирать баги потом не на чем.
        if fresh_stage:
            SEEN_STAGES.add(stage)

            (DUMP_DIR / f"stage_{stage}.json").write_text(
                dump, encoding="utf-8"
            )

    def answer(self, payload):
        """Ответ с телом на POST: в нём всегда JSON."""

        self.send_body(
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
            cache=False,
        )

    def do_GET(self):
        path = self.path.split("?", 1)[0]

        if path in ("/settings", "/settings.html"):
            self.send_file(
                UI_DIR / "settings.html", "text/html; charset=utf-8"
            )

            return

        if path in ("/meta", "/meta.html"):
            self.send_file(UI_DIR / "meta.html", "text/html; charset=utf-8")

            return

        if path in ("/counters", "/counters.html"):
            self.send_file(
                UI_DIR / "counters.html", "text/html; charset=utf-8"
            )

            return

        if path.startswith("/api/meta"):
            self.send_body(
                json.dumps(HELPER.meta(), ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
                cache=False,
            )

            return

        if path.startswith("/api/counters"):
            query = parse_qs(urlparse(self.path).query)

            self.send_body(
                json.dumps(
                    HELPER.counters((query.get("hero") or [""])[0]),
                    ensure_ascii=False,
                ).encode("utf-8"),
                "application/json; charset=utf-8",
                cache=False,
            )

            return

        if path.startswith("/updates"):
            self.send_body(
                json.dumps(
                    HELPER.updates(force="force" in self.path),
                    ensure_ascii=False,
                ).encode("utf-8"),
                "application/json; charset=utf-8",
                cache=False,
            )

            return

        if path.startswith("/setup"):
            self.send_body(
                json.dumps(HELPER.setup(), ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
                cache=False,
            )

            return

        if path.startswith("/state"):
            self.send_body(
                json.dumps(HELPER.current(), ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
                cache=False,
            )

            return

        if path in ("/", "/index.html", "/overlay.html"):
            self.send_file(UI_DIR / "overlay.html", "text/html; charset=utf-8")

            return

        if path in ("/menu", "/menu.html"):
            self.send_file(UI_DIR / "menu.html", "text/html; charset=utf-8")

            return

        if path in ("/browse", "/browse.html"):
            self.send_file(UI_DIR / "browse.html", "text/html; charset=utf-8")

            return

        # Фон шапки главного окна. Лежит рядом с разметкой; если его
        # нет, страница сама возьмёт портрет героя.
        if path == "/steam.png":
            self.send_file(UI_DIR / "steamlogo.png", "image/png")

            return

        if path == "/banner.png":
            self.send_file(UI_DIR / "background.png", "image/png")

            return

        if path in ("/home", "/home.html"):
            self.send_file(UI_DIR / "home.html", "text/html; charset=utf-8")

            return

        if path == "/home.js":
            self.send_file(
                UI_DIR / "home.js", "application/javascript; charset=utf-8",
                cache=False,
            )

            return

        if path == "/app-icon.png":
            self.send_file(
                ICONS_DIR / "app" / "app-256.png", "image/png"
            )

            return

        # Стили разнесены: общее, игровая панель и обычные окна.
        if path in ("/base.css", "/panel.css", "/windows.css"):
            self.send_file(
                UI_DIR / path.lstrip("/"), "text/css; charset=utf-8", cache=False
            )

            return

        if path.startswith("/heroes"):
            self.send_body(
                json.dumps(HELPER.heroes(), ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
                cache=False,
            )

            return

        if path.startswith("/guide"):
            self.send_guide()

            return

        if path.startswith("/items"):
            self.send_body(
                json.dumps(HELPER.items(), ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
                cache=False,
            )

            return

        if path.startswith("/item"):
            self.send_item()

            return

        if path.startswith("/icons/"):
            self.send_icon(path)

            return

        if path.startswith("/fonts/"):
            self.send_font(path)

            return

        self.send_response(404)
        self.end_headers()

    def send_guide(self):
        """Гайд по герою и позиции, без всякого матча.

        Тот же движок, что и в панели, только вместо состояния игры —
        то, что спросили в справочнике.
        """

        from urllib.parse import parse_qs, urlparse

        query = parse_qs(urlparse(self.path).query)
        hero = (query.get("hero") or [""])[0]
        position = (query.get("position") or [""])[0] or None

        answer = HELPER.guide(hero, position)

        self.send_body(
            json.dumps(answer, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
            cache=False,
        )

    def send_item(self):
        """Кто и когда берёт этот предмет — по всем героям и позициям."""

        from urllib.parse import parse_qs, urlparse

        query = parse_qs(urlparse(self.path).query)
        name = (query.get("name") or [""])[0]

        self.send_body(
            json.dumps(HELPER.item(name), ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
            cache=False,
        )

    # --- отдача файлов ---

    def send_body(self, body, kind, cache=True):
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))

        if cache:
            # Иконки и шрифты за матч не меняются.
            self.send_header("Cache-Control", "max-age=86400")
        else:
            self.send_header("Cache-Control", "no-store")

        self.end_headers()

        try:
            self.wfile.write(body)

        except (BrokenPipeError, ConnectionAbortedError):
            # Оверлей закрыли посреди ответа — это не ошибка.
            pass

    def send_file(self, path, kind, cache=True):
        try:
            self.send_body(path.read_bytes(), kind, cache)

        except OSError:
            self.send_response(404)
            self.end_headers()

    def safe_name(self, part):
        """Кусок пути, в котором не может быть выхода наружу.

        Сервер локальный, но принимает он всё, что придёт на порт,
        поэтому «..» и слэши отсекаем, а не надеемся на вежливость.
        """

        return part if re.fullmatch(r"[A-Za-z0-9_.-]+", part or "") else None

    def send_icon(self, path):
        parts = path.strip("/").split("/")

        if len(parts) != 3:
            self.send_response(404)
            self.end_headers()

            return

        kind = self.safe_name(parts[1])
        name = self.safe_name(parts[2])

        if kind not in ("items", "heroes", "abilities", "crops") or not name:
            self.send_response(404)
            self.end_headers()

            return

        # Предметы лежат без приставки item_, а гайды её несут.
        if kind == "items" and name.startswith("item_"):
            name = name[len("item_"):]

        # У рецептов своих иконок нет — показываем то, что из них
        # собирается. Иначе в стартовой закупке пустая рамка.
        if kind == "items" and name.startswith("recipe_"):
            if not (ICONS_DIR / kind / name).exists():
                name = name[len("recipe_"):]

        self.send_file(ICONS_DIR / kind / name, "image/png")

    def send_font(self, path):
        name = self.safe_name(path.rsplit("/", 1)[-1])

        for candidate in font_files():
            if candidate.name == name:
                self.send_file(candidate, "font/otf")

                return

        self.send_response(404)
        self.end_headers()

    def log_message(self, *args):
        pass


class Server(ThreadingHTTPServer):
    """Один приёмник на машину.

    По умолчанию `HTTPServer` включает `SO_REUSEADDR`, а в Windows он
    разрешает двум процессам занять один и тот же порт. Из-за этого
    второй запуск молча поднимался рядом с первым, не получал от игры
    ни одного пакета и показывал панели матч получасовой давности —
    ошибка не проявлялась ничем, кроме застывших данных.
    """

    allow_reuse_address = False


def ask_previous_to_quit(port):
    """Просит прежний Helper уйти.

    Уходит старый, а не новый: перезапуск должен чинить залипший
    приёмник, а не упираться в него.
    """

    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/quit",
            data=b"{}",
            headers={QUIT_HEADER: "quit"},
        )

        urllib.request.urlopen(request, timeout=2)

    except (urllib.error.URLError, OSError):
        # Никого не было либо прежний уже не отвечает — так тоже бывает.
        pass


def take_over(port, timeout=6.0):
    """Занимает порт, дождавшись, пока его освободит прежний Helper."""

    deadline = time.time() + timeout

    while True:
        try:
            return Server(("127.0.0.1", port), Handler)

        except OSError:
            ask_previous_to_quit(port)

            if time.time() >= deadline:
                raise SystemExit(
                    f"Порт {port} занят, и прежний Helper не уходит. "
                    f"Закрой его вручную и запусти снова."
                )

            time.sleep(0.3)


def serve_in_background(port=PORT):
    """Поднимает приёмник в отдельном потоке и сразу возвращает управление.

    Так приложение живёт одним процессом: в главном потоке рисуется
    панель, в фоновом принимаются пакеты от игры. Раньше это были две
    программы, и у одной из них оставалось чёрное окно консоли, которое
    игрок видел и закрывал вместе со всем остальным.
    """

    server = take_over(port)

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    return server


def run(port=PORT):
    if not SNAPSHOT_PATH.exists():
        raise SystemExit(
            f"Нет snapshot'а ({SNAPSHOT_PATH}). "
            f"Сначала собери его: python -c "
            f"\"from services.snapshot import export_snapshot; export_snapshot()\""
        )

    print(f"\nHelper слушает http://localhost:{port}/")
    print("  POST /       — сюда шлёт Dota 2")
    print("  GET  /       — страница оверлея")
    print("  GET  /state  — рекомендация для оверлея")
    print("  GET  /icons/ — картинки предметов, героев и способностей")
    print("\nЗапускай игру. Ctrl+C для выхода.\n")

    server = take_over(port)

    try:
        server.serve_forever()

    finally:
        # Освобождаем порт сразу, а не когда процесс доживёт до конца:
        # его прямо сейчас ждёт тот, кто попросил нас уйти.
        server.server_close()

        print("Helper остановлен.")


if __name__ == "__main__":
    run()
