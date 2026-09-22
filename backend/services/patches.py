"""Патчи Доты и вес матча по его возрасту.

Номера версий у STRATZ врут: их справочник застрял на 7.40b, и все матчи
после декабря 2025 получают один и тот же идентификатор. Поэтому патч
матча мы определяем по времени начала, а список патчей с датами берём
у самой Valve.

Зачем веса. Выбрасывать матчи старого патча — значит остаться почти без
данных в первые недели после выхода нового: у редких связок герой+позиция
их просто не наберётся. Поэтому старые матчи не выбрасываются, а весят
меньше: свежих хватает, чтобы перевесить, но там, где свежего мало,
картина плавно опирается на старое, а не рассыпается.
"""

import json
import time
import urllib.error
import urllib.request

from config import DATA_DIR


PATCHES_URL = (
    "https://www.dota2.com/datafeed/patchnoteslist?language=english"
)

# Список с датами кладём рядом с остальными данными: он нужен и без сети,
# а меняется раз в несколько недель.
PATCHES_PATH = DATA_DIR / "patches.json"

# Сколько держим скачанный список, прежде чем спросить Valve заново.
FRESH_FOR = 6 * 3600

# Вес матча по тому, насколько его патч далёк от нынешнего.
#
# Ступенями по патчам, а не плавным затуханием по дате: предметы и таланты
# меняются скачком в день патча, а не понемногу каждый день.
#
# Внутри одной большой версии (7.41a…7.41f) правки мелкие, поэтому такие
# матчи теряют вдвое за каждый патч назад и не дешевеют бесконечно: даже
# 7.41a ближе к 7.41f, чем что угодно из 7.40. Прошлая большая версия —
# это уже другая игра, ей одна низкая ступень на всех.
SAME_PATCH = 1.0
STEP_BACK = 0.5
SAME_VERSION_FLOOR = 0.25
OLDER = 0.1


def _download():
    request = urllib.request.Request(
        PATCHES_URL, headers={"User-Agent": "Dota2Helper/1.0"}
    )

    with urllib.request.urlopen(request, timeout=20) as answer:
        data = json.loads(answer.read().decode("utf-8"))

    patches = [
        {
            "patch": entry["patch_number"],
            "at": int(entry["patch_timestamp"]),
        }
        for entry in data.get("patches") or []
        if entry.get("patch_number") and entry.get("patch_timestamp")
    ]

    patches.sort(key=lambda entry: entry["at"])

    return patches


def patch_list(refresh=False):
    """Список патчей с датами, от старых к новым."""

    cached = None

    if PATCHES_PATH.exists():
        try:
            cached = json.loads(PATCHES_PATH.read_text(encoding="utf-8"))

        except (OSError, ValueError):
            cached = None

    fresh = (
        cached
        and not refresh
        and time.time() - cached.get("built_at", 0) < FRESH_FOR
    )

    if fresh:
        return cached["patches"]

    try:
        patches = _download()

    except (urllib.error.URLError, OSError, ValueError, KeyError):
        # Без сети работаем по тому, что уже скачано: даты патчей задним
        # числом не меняются, так что вчерашний список ничем не хуже.
        return cached["patches"] if cached else []

    PATCHES_PATH.parent.mkdir(parents=True, exist_ok=True)

    PATCHES_PATH.write_text(
        json.dumps(
            {"built_at": int(time.time()), "patches": patches},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    return patches


def version_of(patch):
    """Большая версия патча: 7.41f -> 7.41."""

    return patch.rstrip("abcdefghijklmnopqrstuvwxyz") if patch else patch


class Weights:
    """Вес матча по времени его начала.

    Держим готовые границы, а не ищем патч перебором на каждый матч:
    проход идёт по миллионам строк.
    """

    def __init__(self, patches=None, current=None):
        self.patches = patches if patches is not None else patch_list()

        # Нынешним считаем последний вышедший, а не последний, по которому
        # есть матчи: сразу после выхода патча матчей по нему ещё нет,
        # но собирать мы будем уже его.
        self.current = current or (
            self.patches[-1]["patch"] if self.patches else None
        )

        self.version = version_of(self.current)

        # Патчи своей версии, от новых к старым: по месту в этом списке
        # считается, на сколько ступеней назад ушёл матч.
        self.ladder = [
            entry["patch"]
            for entry in reversed(self.patches)
            if version_of(entry["patch"]) == self.version
        ]

        # (время начала патча, вес) от новых к старым.
        self.steps = [
            (entry["at"], self._weight_of_patch(entry["patch"]))
            for entry in reversed(self.patches)
        ]

    def _weight_of_patch(self, patch):
        if patch == self.current:
            return SAME_PATCH

        if version_of(patch) != self.version:
            return OLDER

        try:
            steps = self.ladder.index(patch) - self.ladder.index(self.current)

        except ValueError:
            return SAME_VERSION_FLOOR

        # Патч новее нынешнего бывает только при проверке правил на
        # собранных матчах: считаем его своим.
        if steps <= 0:
            return SAME_PATCH

        return max(SAME_VERSION_FLOOR, STEP_BACK ** steps)

    def patch_at(self, start_time):
        """Каким патчем шёл матч, начавшийся в это время."""

        for entry in reversed(self.patches):
            if start_time >= entry["at"]:
                return entry["patch"]

        return None

    def of(self, start_time):
        if not start_time:
            return OLDER

        for at, weight in self.steps:
            if start_time >= at:
                return weight

        return OLDER

    def summary(self, connection):
        """Сколько матчей какого веса лежит в базе — для отчёта о сборе."""

        counts = {}

        for row in connection.execute("SELECT start_time FROM matches"):
            patch = self.patch_at(row["start_time"]) or "до списка"

            counts[patch] = counts.get(patch, 0) + 1

        return {
            "current": self.current,
            "by_patch": dict(
                sorted(counts.items(), key=lambda pair: -pair[1])
            ),
        }
