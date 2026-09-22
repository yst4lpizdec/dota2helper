"""Запущена ли сейчас Dota 2.

Нужно затем, чтобы панель не висела на рабочем столе, когда играть никто
не собирается: игрок просит показываться вместе с игрой, а не вместе с
Windows.

Смотрим список процессов напрямую через Windows API: psutil ради одной
проверки в зависимости тащить незачем.
"""

import ctypes
import sys
from ctypes import wintypes


PROCESS_NAME = "dota2.exe"

TH32CS_SNAPPROCESS = 0x00000002

INVALID_HANDLE = ctypes.c_void_p(-1).value


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


def is_running(name=PROCESS_NAME):
    """Есть ли среди процессов dota2.exe.

    Ошибку не поднимаем никогда: не смогли посмотреть — считаем, что игра
    запущена. Спрятать панель по ошибке хуже, чем лишний раз показать.
    """

    if sys.platform != "win32":
        return True

    try:
        kernel32 = ctypes.windll.kernel32

        snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)

        if snapshot == INVALID_HANDLE:
            return True

        try:
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)

            found = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))

            while found:
                if entry.szExeFile.lower() == name:
                    return True

                found = kernel32.Process32NextW(snapshot, ctypes.byref(entry))

        finally:
            kernel32.CloseHandle(snapshot)

    except OSError:
        return True

    return False
