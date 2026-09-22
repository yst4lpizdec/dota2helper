"""Один оверлей на машину.

Каждый запуск start.bat поднимал ещё одну панель поверх прежних: они
копились, перекрывали друг друга и делили одни горячие клавиши — нажатие
доставалось самой старой, а мешала самая новая.

Место занимается сокетом, а не файлом с PID: сокет освобождается сам,
когда процесс умирает, даже если его сняли из диспетчера задач.
"""

import socket
import threading
import time

from PySide6.QtCore import QObject, Signal


PORT = 3001

QUIT = b"quit"


class SingleInstance(QObject):
    """Занимает место, предварительно попросив прежний оверлей уйти.

    Уходит именно старый, а не новый: перезапуск должен чинить залипшую
    панель, а не оставлять игрока с ней один на один.
    """

    asked_to_quit = Signal()

    def __init__(self, port=PORT):
        super().__init__()

        self.port = port
        self.server = None

    def take_over(self, timeout=3.0):
        self._ask_previous_to_quit()

        deadline = time.time() + timeout

        while True:
            server = socket.socket()

            try:
                # Без SO_REUSEADDR намеренно: в Windows он разрешает двум
                # сокетам занять один порт, и проверка перестала бы работать.
                server.bind(("127.0.0.1", self.port))
                server.listen(1)

            except OSError:
                server.close()

                if time.time() >= deadline:
                    return False

                time.sleep(0.2)

                continue

            self.server = server

            threading.Thread(target=self._serve, daemon=True).start()

            return True

    def _ask_previous_to_quit(self):
        try:
            with socket.create_connection(
                ("127.0.0.1", self.port), timeout=0.5
            ) as link:
                link.sendall(QUIT)

        except OSError:
            # Никого не было — обычный первый запуск.
            pass

    def _serve(self):
        while True:
            try:
                link, _ = self.server.accept()

            except OSError:
                return

            with link:
                try:
                    if link.recv(16).strip() == QUIT:
                        self.asked_to_quit.emit()

                        return

                except OSError:
                    continue
