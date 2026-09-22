"""Обновления: данные отдельно, программа отдельно.

Данные стареют быстрее программы. Матчи собираются каждую неделю, а
интерфейс может не меняться месяцами — гонять человека через установщик
на четверть гигабайта ради полутора мегабайт цифр незачем. Поэтому
snapshot обновляется отдельно и на месте: согласился — скачали,
подменили, движок перечитал, без перезапуска.

Ничего не качается само. Программа только проверяет и предлагает:
качать в фоне чужой трафик, тем более посреди матча, — не наше дело.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from config import USER_DIR


# Версия программы. Её же подставляет установщик и сверяет обновление.
VERSION = "0.1.0"

REPO = "yst4lpizdec/dota2helper"

RELEASES_API = f"https://api.github.com/repos/{REPO}/releases"

# Данные живут прямо в репозитории, а не отдельным релизом: релизы —
# это версии программы, и мешать в них файл с цифрами незачем.
SNAPSHOT_IN_REPO = "backend/data/snapshot.json.gz"

SNAPSHOT_RAW = (
    f"https://raw.githubusercontent.com/{REPO}/main/{SNAPSHOT_IN_REPO}"
)

COMMITS_API = f"https://api.github.com/repos/{REPO}/commits"

SNAPSHOT_NAME = "snapshot.json.gz"

# Свежий snapshot кладём рядом с настройками, а не в папку программы:
# в Program Files писать нельзя.
USER_SNAPSHOT = USER_DIR / SNAPSHOT_NAME

# Когда последний раз спрашивали GitHub. Чаще раза в сутки незачем:
# данные пересобираются не быстрее.
STAMP_PATH = USER_DIR / "updates.json"

CHECK_EVERY = 24 * 3600

TIMEOUT = 20


def _get(url, raw=False):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": f"Dota2Helper/{VERSION}",
            "Accept": "application/octet-stream" if raw else "application/json",
        },
    )

    with urllib.request.urlopen(request, timeout=TIMEOUT) as answer:
        data = answer.read()

    return data if raw else json.loads(data.decode("utf-8"))


def _version_tuple(text):
    """«v0.2.10» -> (0, 2, 10). Для сравнения, а не для показа."""

    cleaned = (text or "").lstrip("vV").split("-")[0]

    parts = []

    for chunk in cleaned.split("."):
        parts.append(int(chunk) if chunk.isdigit() else 0)

    while len(parts) < 3:
        parts.append(0)

    return tuple(parts[:3])


def local_snapshot_time():
    """Когда собран тот snapshot, которым программа пользуется сейчас."""

    from services.snapshot import snapshot_path

    path = snapshot_path()

    try:
        return int(path.stat().st_mtime)

    except OSError:
        return 0


def check(force=False):
    """Что нового на GitHub. Ошибки сети — не повод шуметь."""

    now = int(time.time())

    if not force:
        try:
            stamp = json.loads(STAMP_PATH.read_text(encoding="utf-8"))

            if now - stamp.get("checked_at", 0) < CHECK_EVERY:
                return stamp.get("result") or {}

        except (OSError, ValueError):
            pass

    result = {"checked_at": now}

    # Данные: смотрим, когда файл в репозитории последний раз менялся.
    # Это один запрос и никакой закачки — сам файл тянем, только если
    # игрок согласится.
    try:
        commits = _get(
            f"{COMMITS_API}?path={SNAPSHOT_IN_REPO}&per_page=1"
        )

        changed = (
            (commits or [{}])[0].get("commit", {}).get("committer", {}).get("date")
        )

        if changed:
            result["data"] = {
                "url": SNAPSHOT_RAW,
                "published": changed,
                "newer": _is_newer(changed),
            }

    except (urllib.error.URLError, OSError, ValueError, KeyError, IndexError):
        pass

    # Программа.
    try:
        release = _get(f"{RELEASES_API}/latest")

        tag = release.get("tag_name") or ""

        if _version_tuple(tag) > _version_tuple(VERSION):
            setup = None

            for asset in release.get("assets") or []:
                if (asset.get("name") or "").lower().endswith(".exe"):
                    setup = asset

                    break

            if setup:
                result["app"] = {
                    "version": tag.lstrip("vV"),
                    "url": setup.get("url"),
                    "size": setup.get("size"),
                    "notes": (release.get("body") or "")[:500],
                }

    except (urllib.error.URLError, OSError, ValueError, KeyError):
        pass

    try:
        STAMP_PATH.parent.mkdir(parents=True, exist_ok=True)

        STAMP_PATH.write_text(
            json.dumps(
                {"checked_at": now, "result": result}, ensure_ascii=False
            ),
            encoding="utf-8",
        )

    except OSError:
        pass

    return result


def _is_newer(published):
    """Свежее ли то, что лежит на GitHub, того, что у нас на диске.

    Сравниваем по времени, а не по размеру: после пересчёта размер может
    совпасть до байта, а цифры внутри будут другие.
    """

    try:
        stamp = time.strptime(published, "%Y-%m-%dT%H:%M:%SZ")

    except (ValueError, TypeError):
        return False

    return int(time.mktime(stamp)) - time.timezone > local_snapshot_time()


def download_snapshot(url):
    """Качает свежие данные и подменяет ими текущие.

    Пишем во временный файл рядом с целью и переименовываем: если связь
    оборвётся на середине, программа останется со старыми данными, а не
    с обрезанным файлом, который уже не прочитать.
    """

    data = _get(url, raw=True)

    USER_SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)

    temporary = USER_SNAPSHOT.with_suffix(".part")

    temporary.write_bytes(data)

    # Проверяем, что скачалось именно то: битый архив не должен заменить
    # рабочие данные.
    import gzip

    with gzip.open(temporary, "rt", encoding="utf-8") as handle:
        parsed = json.load(handle)

    if not parsed.get("guides"):
        temporary.unlink(missing_ok=True)

        raise ValueError("в скачанных данных нет гайдов")

    os.replace(temporary, USER_SNAPSHOT)

    return {
        "path": str(USER_SNAPSHOT),
        "mb": round(len(data) / 1048576, 2),
        "patch": parsed.get("patch"),
        "matches": parsed.get("matches"),
        "guides": len(parsed["guides"]),
    }


def download_installer(url):
    """Качает установщик во временную папку и возвращает путь к нему."""

    data = _get(url, raw=True)

    folder = Path(tempfile.gettempdir()) / "Dota2Helper"
    folder.mkdir(parents=True, exist_ok=True)

    path = folder / "Dota2Helper-setup.exe"
    path.write_bytes(data)

    return path


def run_installer(path):
    """Запускает установщик и уходит с дороги.

    Он сам закроет запущенную программу и поставит новую поверх, поэтому
    после запуска нам остаётся только выйти.
    """

    if sys.platform != "win32":
        return False

    subprocess.Popen([str(path)], close_fds=True)

    return True
