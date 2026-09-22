"""Шрифт Radiance — родной шрифт интерфейса Dota 2.

Берём его из установленной игры, а не тащим в проект: файл принадлежит
Valve, и у пользователя он и так есть. Если игру не нашли — интерфейс
просто останется на системном шрифте.
"""

from pathlib import Path


FONT_SUBPATH = Path("game") / "dota" / "panorama" / "fonts"

# Где обычно стоит Dota 2. Список пополняется поиском по библиотекам Steam.
COMMON_ROOTS = [
    Path(r"C:/Program Files (x86)/Steam/steamapps/common/dota 2 beta"),
    Path(r"C:/Steam/steamapps/common/dota 2 beta"),
]

WANTED = [
    "radiance-regular.otf",
    "radiance-semibold.otf",
    "radiance-bold.otf",
    "radiance-light.otf",
]


def _library_roots():
    """Ищет Dota 2 по всем библиотекам Steam, а не только в стандартной."""

    roots = list(COMMON_ROOTS)

    for drive in "CDEFGH":
        for candidate in (
            Path(f"{drive}:/SteamLibrary/steamapps/common/dota 2 beta"),
            Path(f"{drive}:/Games/Steam/steamapps/common/dota 2 beta"),
        ):
            roots.append(candidate)

    return roots


def font_files():
    """Пути к файлам Radiance, если игра установлена."""

    for root in _library_roots():
        folder = root / FONT_SUBPATH

        if not folder.exists():
            continue

        found = [folder / name for name in WANTED if (folder / name).exists()]

        if found:
            return found

    return []


def load_into_qt():
    """Регистрирует Radiance в Qt. Возвращает имя семейства или None."""

    from PySide6.QtGui import QFontDatabase

    family = None

    for path in font_files():
        font_id = QFontDatabase.addApplicationFont(str(path))

        if font_id == -1:
            continue

        families = QFontDatabase.applicationFontFamilies(font_id)

        if families and family is None:
            family = families[0]

    return family
