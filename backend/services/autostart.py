"""Запуск вместе с Windows.

Обычная запись в разделе Run текущего пользователя — без прав
администратора и без ярлыков в папке автозагрузки. Состояние не хранится
в настройках: правда одна — есть запись в реестре или нет. Иначе галочка
врала бы, стоило человеку выключить автозапуск в диспетчере задач.
"""

import sys
from pathlib import Path


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

VALUE_NAME = "Dota2Helper"

# С этим ключом программа стартует тихо: только значок у часов, без
# главного окна. Человек не просил ничего открывать — он включил компьютер.
TRAY_FLAG = "--tray"


def command():
    """Чем запускаться: собранной программой или pythonw со скриптом."""

    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" {TRAY_FLAG}'

    # Из исходников — через pythonw, иначе при входе в систему выскочит
    # чёрное окно консоли.
    python = Path(sys.executable)
    windowless = python.with_name("pythonw.exe")

    if windowless.exists():
        python = windowless

    script = Path(__file__).resolve().parent.parent / "overlay_web.py"

    return f'"{python}" "{script}" {TRAY_FLAG}'


def _registry():
    if sys.platform != "win32":
        return None

    import winreg

    return winreg


def is_enabled():
    winreg = _registry()

    if winreg is None:
        return False

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, VALUE_NAME)

        return True

    except OSError:
        return False


def set_enabled(enabled):
    """Включает или выключает автозапуск. Возвращает то, что вышло."""

    winreg = _registry()

    if winreg is None:
        return False

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            if enabled:
                winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command())
            else:
                try:
                    winreg.DeleteValue(key, VALUE_NAME)

                except FileNotFoundError:
                    pass

    except OSError as error:
        print(f"автозапуск не переключился: {error}")

    return is_enabled()
