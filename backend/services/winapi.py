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
WM_QUIT = 0x0012

MODIFIERS = {"ctrl": MOD_CONTROL, "alt": MOD_ALT, "shift": MOD_SHIFT}


def parse_combo(text):
    """«Ctrl+Alt+D» → (модификаторы, код клавиши) или None.

    Клавиша — буква, цифра или F1–F12. Букве и цифре нужен хотя бы один
    модификатор: голая «D» отняла бы у игры клавишу целиком.
    """

    parts = [part.strip() for part in (text or "").split("+") if part.strip()]

    if not parts:
        return None

    mods = 0

    for part in parts[:-1]:
        flag = MODIFIERS.get(part.lower())

        if flag is None:
            return None

        mods |= flag

    key = parts[-1].upper()

    if len(key) == 1 and (key.isalpha() or key.isdigit()) and key.isascii():
        if not mods:
            return None

        return mods, ord(key)

    if key.startswith("F") and key[1:].isdigit() and 1 <= int(key[1:]) <= 12:
        return mods, 0x70 + int(key[1:]) - 1

    return None

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
        self.thread_id = None
        self.thread = None

    def start(self):
        if _user32() is None:
            return

        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def rebind(self, bindings):
        """Новые сочетания без перезапуска программы.

        Горячие клавиши принадлежат потоку, который их занял, поэтому
        старый поток останавливаем (он сам их отпустит) и поднимаем новый.
        """

        user32 = _user32()

        if user32 is not None and self.thread_id:
            user32.PostThreadMessageW(self.thread_id, WM_QUIT, 0, 0)

            if self.thread is not None:
                self.thread.join(timeout=1.0)

        self.bindings = dict(bindings)
        self.failed = []
        self.thread_id = None

        self.start()

    def _loop(self):
        from ctypes import wintypes

        user32 = _user32()

        self.thread_id = ctypes.windll.kernel32.GetCurrentThreadId()

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

        # Попросили остановиться — отпускаем сочетания, иначе новый поток
        # не сможет занять те же самые.
        for index in names:
            user32.UnregisterHotKey(None, index)
