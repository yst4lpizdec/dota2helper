"""То, чего Qt не умеет сам, и приходится просить у Windows напрямую.

Две вещи, без которых оверлей не живёт поверх игры: глобальные горячие
клавиши (Qt-шорткаты работают только когда окно в фокусе, а оверлей
фокус не получает никогда) и сквозная мышь (чтобы клик уходил в игру,
а не в панель).
"""

import ctypes
import sys
import threading

from PySide6.QtCore import QObject, Qt, Signal


MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004

# Без этого Windows шлёт хоткей повторами, пока клавиша зажата.
MOD_NOREPEAT = 0x4000

WM_HOTKEY = 0x0312

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000


def _user32():
    if sys.platform != "win32":
        return None

    return ctypes.windll.user32


def set_click_through(window, enabled):
    """Пропускает клики сквозь окно в игру.

    Qt-атрибута тут мало: у окна верхнего уровня попадание мыши решает
    система ещё до того, как событие дойдёт до Qt, поэтому правим стиль
    окна напрямую.
    """

    window.setAttribute(Qt.WA_TransparentForMouseEvents, enabled)

    user32 = _user32()

    if user32 is None:
        return

    from ctypes import wintypes

    read = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
    write = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)

    read.argtypes = [wintypes.HWND, ctypes.c_int]
    read.restype = ctypes.c_ssize_t
    write.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    write.restype = ctypes.c_ssize_t

    handle = int(window.winId())
    style = read(handle, GWL_EXSTYLE)

    if enabled:
        style |= WS_EX_TRANSPARENT | WS_EX_LAYERED
    else:
        style &= ~WS_EX_TRANSPARENT

    write(handle, GWL_EXSTYLE, style)


class Hotkeys(QObject):
    """Горячие клавиши, которые работают, пока игрок в игре.

    Комбинации регистрируются в самой системе, а их сообщения приходят
    в отдельный поток со своим циклом — GUI-поток при этом свободен.
    """

    pressed = Signal(str)

    def __init__(self, bindings):
        super().__init__()

        # имя действия -> (модификаторы, код клавиши)
        self.bindings = dict(bindings)
        self.failed = []

    def start(self):
        if _user32() is None:
            return

        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        from ctypes import wintypes

        user32 = _user32()

        user32.RegisterHotKey.argtypes = [
            wintypes.HWND,
            ctypes.c_int,
            wintypes.UINT,
            wintypes.UINT,
        ]
        user32.RegisterHotKey.restype = wintypes.BOOL

        user32.GetMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG),
            wintypes.HWND,
            wintypes.UINT,
            wintypes.UINT,
        ]
        user32.GetMessageW.restype = ctypes.c_int

        names = {}

        for index, (name, (mods, key)) in enumerate(
            self.bindings.items(), start=1
        ):
            if user32.RegisterHotKey(None, index, mods | MOD_NOREPEAT, key):
                names[index] = name
            else:
                # Комбинацию уже держит другая программа. Это не повод
                # падать: остальные горячие клавиши продолжают работать.
                self.failed.append(name)

        if self.failed:
            print(
                "не удалось занять горячие клавиши: "
                + ", ".join(self.failed)
                + " — их держит другая программа"
            )

        message = wintypes.MSG()

        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            if message.message != WM_HOTKEY:
                continue

            name = names.get(message.wParam)

            if name:
                self.pressed.emit(name)
