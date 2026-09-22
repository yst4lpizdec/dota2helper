# Сборка приложения в папку, а не одним файлом.
#
# Одним файлом было бы красивее, но внутри Chromium: при каждом запуске
# он распаковывался бы во временную папку целиком — это сотни мегабайт
# и несколько секунд ожидания перед каждым матчем.
#
# Сборка: pyinstaller dota2helper.spec
# Результат: dist/Dota2Helper/

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

BACKEND = Path("backend").resolve()

# Иконки лежат россыпью — 1297 файлов, перечислять их незачем.
datas = [
    (str(BACKEND / "ui"), "ui"),
    (str(BACKEND / "data" / "icons"), "data/icons"),
    (str(BACKEND / "data" / "snapshot.json.gz"), "data"),
]

# Приёмник подключается по имени уже во время работы, статически его
# никто не импортирует — без подсказки он в сборку не попадёт.
hiddenimports = ["app", "engine"]

# Всё это стоит в окружении, но приложению не нужно: остатки опытов
# с распознаванием экрана. Без явного исключения PyInstaller иногда
# утаскивает их следом за зависимостями.
excludes = [
    "cv2",
    "numpy",
    "onnxruntime",
    "rapidocr_onnxruntime",
    "PIL",
    "tkinter",
    "matplotlib",
    "scipy",
    "pandas",
    "PySide6.QtQuick3D",
    "PySide6.QtMultimedia",
    "PySide6.Qt3DCore",
]


a = Analysis(
    [str(BACKEND / "overlay_web.py")],
    pathex=[str(BACKEND)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

# Chromium тащит с собой полсотни языков и отладочные ресурсы —
# инструменты разработчика, которых в приложении всё равно нет.
# Это 130 мегабайт, которые незачем скачивать игроку.
KEEP_LOCALES = ("ru.pak", "en-US.pak")


def needed(entry):
    name = str(entry[0]).replace("\\", "/")

    if ".debug.pak" in name or ".debug.bin" in name:
        return False

    if "devtools_resources" in name:
        return False

    if "qtwebengine_locales/" in name:
        return name.endswith(KEEP_LOCALES)

    # Переводы самого Qt: наш интерфейс свой, от них нужны только
    # системные надписи вроде кнопок в диалогах.
    if "/translations/" in name and name.endswith(".qm"):
        return "_ru" in name or "_en" in name

    return True


a.datas = [entry for entry in a.datas if needed(entry)]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Dota2Helper",
    debug=False,
    strip=False,
    upx=False,
    # Окна консоли нет: приёмник живёт внутри программы, а вывод идёт
    # в журнал рядом с настройками.
    console=False,
    icon=str(BACKEND / "data" / "icons" / "app" / "app.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Dota2Helper",
)
