"""Оверлей на движке браузера.

Окно то же самое, что и раньше: безрамочное, прозрачное, поверх игры.
Изменился только слой отрисовки — внутри окна живёт Chromium и показывает
локальную страницу, которая сама читает состояние с localhost:3000.

Браузер при этом не открывается: это встроенный виджет, без вкладок и
адресной строки.

Запуск:  python overlay_web.py   (при запущенном app.py)
"""

import json
import os
import sys
import threading
import time
from pathlib import Path
import urllib.error
import urllib.request

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QDesktopServices, QIcon
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QLabel,
    QMenu,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from config import USER_DIR
from services import dota, settings as settings_store
from services.single_instance import SingleInstance
from services.winapi import MOD_ALT, MOD_CONTROL, Hotkeys, set_click_through


PAGE_URL = "http://localhost:3000/"
BROWSE_URL = "http://localhost:3000/browse"
MENU_URL = "http://localhost:3000/menu"

# Размер главного окна. Подобран под содержимое: слева разделы, справа
# справочник с четырьмя колонками сборки — им нужно около тысячи точек.
MENU_SIZE = (1180, 720)

ICON_PATH = Path(__file__).resolve().parent / "data" / "icons" / "app" / "app.ico"

# Steam сам знает, где лежит игра, и сам её запустит.
DOTA_STEAM_URL = "steam://rungameid/570"

# Как часто смотрим, запущена ли игра. Реже — и панель заметно опаздывает
# за запуском Доты; чаще — впустую дёргаем список процессов.
DOTA_CHECK_MS = 3000

# Сколько секунд после последнего пакета GSI считаем, что игра всё ещё
# идёт. Пакеты приходят часто, так что запас нужен небольшой.
GSI_GRACE = 90

# Сколько держим панель после того, как страница перестала сообщать о
# матче. Нужно только на случай, когда перезапускают app.py посреди игры:
# панель не должна мигать из-за пары пропущенных опросов.
MATCH_GRACE = 20

ESCAPE_STYLE = """
#escape {
    background-color: rgb(16, 20, 26);
    border: 1px solid #4c9dff;
    border-radius: 7px;
}
#escape_text {
    color: #8cc2ff;
    font-size: 12px;
    font-weight: 600;
    border: none;
    background: transparent;
}
"""


class Watch(QObject):
    """Следит за матчем со стороны Python, а не страницы.

    Панель, пока она скрыта, — это фоновая вкладка Chromium, и её таймеры
    замедляются до пары срабатываний в минуту. Значит, спросить «начался
    ли матч» страница вовремя не может: именно в скрытом виде она этого
    ждёт. Поэтому опрос идёт отсюда, обычным запросом в фоновом потоке.
    """

    told = Signal(bool, bool)

    def __init__(self):
        super().__init__()

        self.busy = False

    def kick(self):
        if self.busy:
            return

        self.busy = True

        threading.Thread(target=self._ask, daemon=True).start()

    def _ask(self):
        alive = False
        in_match = False

        try:
            with urllib.request.urlopen(
                "http://localhost:3000/state", timeout=2
            ) as answer:
                state = json.loads(answer.read().decode("utf-8"))

            alive = True
            in_match = bool(state.get("in_match"))

        except (urllib.error.URLError, OSError, ValueError):
            pass

        self.busy = False
        self.told.emit(alive, in_match)


class Escape(QWidget):
    """Кнопка возврата, когда панель перестала ловить мышь.

    Отдельное окно: у панели в сквозном режиме клики проваливаются в игру
    целиком, вместе с её собственными кнопками.
    """

    def __init__(self, on_click):
        super().__init__(
            None,
            Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint,
        )

        self.on_click = on_click

        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setStyleSheet(ESCAPE_STYLE)
        self.setCursor(Qt.PointingHandCursor)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        plate = QFrame()
        plate.setObjectName("escape")

        inner = QVBoxLayout(plate)
        inner.setContentsMargins(11, 6, 11, 6)

        label = QLabel("клики уходят в игру  ·  вернуть мышь панели")
        label.setObjectName("escape_text")

        inner.addWidget(label)
        outer.addWidget(plate)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.on_click()

    def place_over(self, window):
        self.adjustSize()

        top_left = window.mapToGlobal(window.rect().topLeft())
        screen = window.screen().availableGeometry()

        x = top_left.x()
        y = top_left.y() - self.height() - 2

        if y < screen.top():
            y = top_left.y() + window.height() + 2

        x = max(screen.left(), min(x, screen.right() - self.width()))

        self.move(x, y)


class Page(QWidget):
    """Обычное окно с рамкой, внутри — страница приложения.

    `fixed` — окно нельзя растянуть. Главное окно свёрстано под один
    размер: растянутое, оно превращается в поле пустоты с кучкой кнопок
    в углу, а «резиновая» вёрстка под любую ширину здесь ничего не даёт —
    содержимого ровно на один экран.
    """

    def __init__(self, title, url, size, channel=None, fixed=False):
        super().__init__()

        self.setWindowTitle(title)
        self.resize(*size)

        if fixed:
            self.setFixedSize(*size)

        # Значок приложения задан на всё приложение сразу, но окну его
        # лучше поставить и отдельно: так он точно доживает до панели
        # задач, в какой бы момент окно ни открыли.
        if ICON_PATH.exists():
            self.setWindowIcon(QIcon(str(ICON_PATH)))

        self.view = QWebEngineView(self)
        self.view.setContextMenuPolicy(Qt.NoContextMenu)

        if channel is not None:
            self.view.page().setWebChannel(channel)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)

        self.view.load(QUrl(url))

    def reopen(self):
        """Показывает окно, поднимая уже открытое вместо второго такого."""

        self.show()
        self.setWindowState(
            self.windowState() & ~Qt.WindowMinimized | Qt.WindowActive
        )
        self.raise_()
        self.activateWindow()


class Bridge(QObject):
    """То, что страница может попросить у приложения.

    Всё, что умеет окно — подвинуться, свернуться, закрыться, — умеет
    только Python, поэтому страница шлёт сюда намерения, а не делает сама.
    """

    def __init__(self, window):
        super().__init__()

        self.window = window

    @Slot()
    def ready(self):
        self.window.on_page_ready()

    @Slot(str)
    def fit(self, size):
        """Страница сообщает, сколько места ей нужно."""

        try:
            width, height = json.loads(size)

        except (ValueError, TypeError):
            return

        self.window.fit_to(int(width), int(height))

    @Slot(str)
    def drag(self, delta):
        try:
            dx, dy = json.loads(delta)

        except (ValueError, TypeError):
            return

        self.window.move(self.window.x() + int(dx), self.window.y() + int(dy))

    @Slot()
    def dragged(self):
        self.window.save_position()

    @Slot(bool)
    def setCompact(self, compact):
        self.window.set_compact(compact)

    @Slot()
    def toggleClickThrough(self):
        self.window.set_click_through(not self.window.click_through)

    @Slot(result=str)
    def settings(self):
        """Текущие настройки — страница рисует ими своё окно настроек."""

        return json.dumps(settings_store.load(), ensure_ascii=False)

    @Slot(int)
    def setOpacity(self, percent):
        self.window.set_opacity(percent)
        settings_store.update(opacity=int(percent))

    @Slot(int)
    def setScale(self, percent):
        self.window.set_scale(percent)
        settings_store.update(scale=round(percent / 100, 2))

    @Slot(str)
    def setHidden(self, keys):
        try:
            hidden = json.loads(keys)

        except (ValueError, TypeError):
            return

        settings_store.update(hidden=sorted(hidden))

    @Slot(str)
    def setFolded(self, keys):
        try:
            folded = json.loads(keys)

        except (ValueError, TypeError):
            return

        settings_store.update(folded=sorted(folded))

    @Slot(bool)
    def setHideWithoutDota(self, hide):
        self.window.set_hide_without_dota(hide)

    @Slot(bool)
    def serverAlive(self, alive):
        self.window.server_alive = bool(alive)

    @Slot(bool)
    def matchLive(self, live):
        """Страница видит, что матч идёт: драфт, загрузка или сама игра.

        Это не то же самое, что «Дота запущена»: в меню игры пакеты GSI
        тоже идут, а панели там делать нечего.
        """

        self.window.note_match_live(live)

    @Slot(bool)
    def gameLive(self, live):
        """Страница видела свежий пакет от игры.

        Второй признак вдобавок к списку процессов: если Дота почему-то
        называется иначе, пакеты GSI всё равно доказывают, что она идёт.
        """

        self.window.note_game_live(live)

    @Slot(int)
    def fitMenu(self, height):
        """Меню просит высоту под своё содержимое.

        Карточка «Дота ещё не отдаёт данные» то появляется, то исчезает, и
        при постоянной высоте окно либо режет её полосой прокрутки, либо
        стоит с пустым полем внизу.
        """

        self.window.fit_menu(int(height))

    @Slot()
    def openBrowser(self):
        self.window.open_browser()

    @Slot()
    def openMenu(self):
        self.window.open_menu()

    @Slot()
    def openSettings(self):
        self.window.open_settings()

    @Slot()
    def closeSettings(self):
        self.window.close_settings()

    @Slot()
    def showOverlay(self):
        self.window.show_overlay()

    @Slot()
    def launchDota(self):
        self.window.launch_dota()

    @Slot(result=str)
    def status(self):
        """Что происходит прямо сейчас — для главного меню."""

        return json.dumps(
            {
                "dota": self.window.dota_here(),
                "match": self.window.match_in_progress(),
                "server": self.window.server_alive,
                "overlay": self.window.isVisible(),
            }
        )

    @Slot()
    def resetPosition(self):
        self.window.reset_position()

    @Slot(str)
    def setLane(self, heroes):
        try:
            chosen = json.loads(heroes)

        except (ValueError, TypeError):
            return

        self.window.tell_app("/lane", {"heroes": chosen})

    @Slot(str)
    def setPosition(self, position):
        # Пустая строка — «снова угадывай сам».
        self.window.tell_app("/position", {"position": position or None})

    @Slot()
    def hidePanel(self):
        """Убрать панель с экрана. Программа при этом остаётся."""

        self.window.close()

    @Slot()
    def quit(self):
        self.window.quit_all()


class Overlay(QWidget):
    def __init__(self):
        super().__init__()

        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)

        self.click_through = False
        self.compact = False
        # Панель закрыли вручную: до конца матча не показываем её снова,
        # иначе она вернётся сама через пару секунд, по таймеру.
        self.dismissed = False
        # Наоборот: показать вне матча, потому что попросили. Держится
        # до ближайшей смены обстановки, а не вечно.
        self.forced = False
        self.escape = Escape(lambda: self.set_click_through(False))
        self.browser = None
        self.menu = None
        self.server_alive = False
        # Открыты ли настройки сами по себе, без панели за ними.
        self.settings_solo = False

        settings = settings_store.load()

        # Прятать панель, пока игра не запущена. Игрок просил показываться
        # вместе с Дотой, а не висеть на рабочем столе весь день.
        self.hide_without_dota = settings.get("hide_without_dota") is not False
        self.game_live = False
        self.game_seen_at = 0.0
        self.match_live = False
        self.match_seen_at = 0.0
        self.wanted_visible = True

        self.view = QWebEngineView(self)

        # Без этого Chromium подкладывает под страницу белый лист, и от
        # прозрачного окна ничего не остаётся.
        self.view.page().setBackgroundColor(QColor(Qt.transparent))

        self.view.settings().setAttribute(
            QWebEngineSettings.ShowScrollBars, False
        )
        self.view.setContextMenuPolicy(Qt.NoContextMenu)

        self.channel = QWebChannel(self)
        self.bridge = Bridge(self)
        self.channel.registerObject("app", self.bridge)
        self.view.page().setWebChannel(self.channel)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)

        self.resize(460, 300)
        self.restore_position(settings)

        self.set_opacity(settings.get("opacity") or 100)
        self.set_scale(round((settings.get("scale") or 1) * 100))

        self.tray = self.build_tray()

        self.hotkeys = Hotkeys(
            {
                "compact": (MOD_CONTROL | MOD_ALT, ord("D")),
                "click_through": (MOD_CONTROL | MOD_ALT, ord("F")),
            }
        )
        self.hotkeys.pressed.connect(self.on_hotkey)
        self.hotkeys.start()

        self.watch = Watch()
        self.watch.told.connect(self.on_watch)

        self.watcher = QTimer(self)
        self.watcher.timeout.connect(self.watch.kick)
        self.watcher.start(DOTA_CHECK_MS)
        self.watch.kick()

        self.view.load(QUrl(PAGE_URL))

        # Если движок ещё не поднялся, страница приедет пустой — пробуем
        # ещё раз, вместо того чтобы висеть белым окном.
        self.retries = 0
        self.view.loadFinished.connect(self.on_load)

    # --- страница ---

    def on_load(self, ok):
        if ok or self.retries >= 5:
            return

        self.retries += 1

        QTimer.singleShot(700, lambda: self.view.load(QUrl(PAGE_URL)))

    def on_page_ready(self):
        """Страница подключилась к мосту и готова слушать."""

        if self.compact:
            self.run("window.__setCompact(true)")

    def run(self, script):
        self.view.page().runJavaScript(script)

    def fit_to(self, width, height):
        """Подгоняет окно под то, сколько места попросила страница.

        Страница меряет себя в своих пикселях и про зум не знает, поэтому
        домножаем здесь: иначе при увеличении размера панель вылезала бы
        за окно, а при уменьшении окно висело бы пустым полем.
        """

        zoom = self.view.zoomFactor()

        self.resize(round(width * zoom), round(height * zoom))

        if self.click_through:
            self.escape.place_over(self)

    # --- режимы ---

    def on_hotkey(self, action):
        if action == "compact":
            self.set_compact(not self.compact)

            # Хоткей меняет режим мимо страницы — её надо догнать.
            self.run(f"window.__setCompact({str(self.compact).lower()})")
        elif action == "click_through":
            self.set_click_through(not self.click_through)

    def set_compact(self, compact):
        self.compact = bool(compact)

        settings_store.update(compact=self.compact)

    def set_click_through(self, enabled):
        self.click_through = enabled

        set_click_through(self, enabled)

        if enabled:
            self.escape.place_over(self)
            self.escape.show()
            self.escape.raise_()
        else:
            self.escape.hide()

        self.run(
            "document.getElementById('mouse').classList."
            f"toggle('on', {str(bool(enabled)).lower()})"
        )

    # --- окна приложения ---

    def open_browser(self):
        """Справочник переехал внутрь главного окна, отдельного больше нет."""

        self.open_menu("browse")

    def build_tray(self):
        """Значок у часов — единственный вход обратно.

        Панель прячется вместе с матчем, меню можно закрыть, и без значка
        программа осталась бы работать совсем без видимых окон: она есть,
        а показать её нечем.
        """

        if not QSystemTrayIcon.isSystemTrayAvailable():
            print("трей недоступен — значка не будет")

            return None

        tray = QSystemTrayIcon(QIcon(str(ICON_PATH)) if ICON_PATH.exists() else QIcon(), self)
        tray.setToolTip("Dota2Helper")

        menu = QMenu()

        menu.addAction("Главное окно", self.open_menu)
        menu.addAction("Показать панель", self.show_overlay)
        menu.addAction("Справочник", self.open_browser)
        menu.addSeparator()
        menu.addAction("Выход", self.quit_all)

        tray.setContextMenu(menu)

        tray.activated.connect(self.on_tray)
        tray.show()

        # Меню живёт ровно столько, сколько значок: без ссылки его
        # соберёт сборщик мусора, и по правой кнопке ничего не выпадет.
        self.tray_menu = menu

        print("значок в трее поставлен")

        return tray

    def announce_update(self, found):
        """Сообщает, что есть что обновить.

        Приходит из фонового потока, поэтому в главный возвращаемся
        таймером: трогать окна не из своего потока Qt не разрешает.
        """

        QTimer.singleShot(0, lambda: self._show_update_note(found))

    def _show_update_note(self, found):
        if self.tray is None:
            return

        if found.get("app"):
            text = f"Новая версия {found['app']['version']} — нажми, чтобы обновить"
        else:
            text = "Есть свежие данные — нажми, чтобы обновить"

        self.tray.showMessage("Dota2Helper", text, QSystemTrayIcon.Information, 10000)

        # По нажатию на само сообщение открываем меню: кнопки обновления
        # там, а значок у часов Windows ещё и прячет под стрелку.
        try:
            self.tray.messageClicked.connect(self.open_menu)

        except (TypeError, RuntimeError):
            pass

    def on_tray(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            self.open_menu()

    def quit_all(self):
        """Настоящий выход: по просьбе из меню или из значка."""

        for window in (self.browser, self.menu):
            if window is not None:
                window.close()

        self.escape.hide()

        if self.tray is not None:
            self.tray.hide()

        QApplication.quit()

    def open_menu(self, section=None):
        if self.menu is None:
            # Мост тот же самый: меню умеет всё то же, что и панель.
            self.menu = Page(
                "Dota2Helper", MENU_URL, MENU_SIZE, self.channel, fixed=True
            )

        self.menu.reopen()

        if section:
            self.menu.view.page().runJavaScript(
                f"window.__open && window.__open('{section}')"
            )

    def fit_menu(self, height):
        """Осталось от прежнего окна, которое подгоняло высоту под себя.

        Теперь размер фиксирован, и подгонять нечего — но страница об
        этом не знает и по-прежнему шлёт свою высоту.
        """

    def open_settings(self):
        """Показывает настройки — и только их, если панели показывать нечего.

        Окно настроек стоит рядом с панелью, чтобы было видно, что делают
        ползунки. Но когда матча нет, рядом стоит пустое «ожидаю игру»,
        и выглядит это странно: просили настройки, а показали панель.
        """

        solo = not self.isVisible() or not self.match_in_progress()

        self.settings_solo = solo
        self.set_panel_visible(True)
        self.raise_()

        self.run(
            "window.__openSettings && window.__openSettings("
            f"{str(solo).lower()})"
        )

    def close_settings(self):
        """Настройки закрыли — возвращаемся туда, откуда пришли."""

        self.settings_solo = False

        self.check_dota()

        if self.menu is not None:
            self.menu.reopen()

    def show_overlay(self):
        """Показывает панель по просьбе из меню или из значка в трее.

        Настройку «только в матче» при этом не трогаем. Раньше трогали —
        и «показать сейчас» молча отменяло правило навсегда: человек один
        раз посмотрел панель, а она потом висела на рабочем столе всегда.
        Просьба показать сейчас — про сейчас, а не про правило.
        """

        self.forced = True
        self.dismissed = False

        self.set_panel_visible(True)
        self.raise_()

    def launch_dota(self):
        QDesktopServices.openUrl(QUrl(DOTA_STEAM_URL))

    # --- ждём игру ---

    def note_game_live(self, live):
        if live:
            self.game_seen_at = time.monotonic()

        self.game_live = live

    def on_watch(self, alive, in_match):
        self.server_alive = alive

        self.note_match_live(in_match)

        # Даже если про матч ничего не изменилось, состояние могло
        # устареть по времени — проверяем видимость на каждом опросе.
        self.check_dota()

    def note_match_live(self, live):
        if live:
            self.match_seen_at = time.monotonic()

        was = self.match_in_progress()

        self.match_live = live

        if self.match_in_progress() != was:
            self.check_dota()

    def match_in_progress(self):
        if self.match_live:
            return True

        return time.monotonic() - self.match_seen_at < MATCH_GRACE

    def set_hide_without_dota(self, hide):
        self.hide_without_dota = bool(hide)

        settings_store.update(hide_without_dota=self.hide_without_dota)

        self.check_dota()

    def dota_here(self):
        """Идёт ли игра прямо сейчас.

        Два признака, и хватает любого: процесс в списке или свежий пакет
        GSI. Ошибиться в сторону «показать» безопаснее, чем спрятать
        панель у человека, который сел играть.
        """

        if dota.is_running():
            return True

        return time.monotonic() - self.game_seen_at < GSI_GRACE

    def check_dota(self):
        # Пока открыты настройки, панель не прячем: игрок сейчас в ней.
        if self.settings_solo:
            return

        live = self.match_in_progress()

        # Матч кончился — прошлый отказ от панели забываем: в следующем
        # она должна появиться сама.
        if not live:
            self.dismissed = False

        # А матч начался — забываем и ручной показ: дальше решает правило.
        if live:
            self.forced = False

        if self.dismissed:
            return

        if not self.hide_without_dota or self.forced:
            self.set_panel_visible(True)

            return

        # Панель живёт по матчу, а не по процессу Доты: в меню игры она
        # не нужна, а на драфте нужна.
        self.set_panel_visible(live)

    def set_panel_visible(self, visible):
        """Показывает или прячет панель.

        Сверяемся с тем, что на экране, а не только с собственной
        отметкой: при запуске отметка говорила «показана», а окно ни разу
        не показывали — и панель не появлялась вовсе.
        """

        if visible == self.wanted_visible and visible == self.isVisible():
            return

        self.wanted_visible = visible

        if visible:
            self.show()
        else:
            self.hide()
            self.escape.hide()

    def set_opacity(self, percent):
        self.setWindowOpacity(max(0.2, (percent or 100) / 100))

    def set_scale(self, percent):
        """Размер панели — это зум страницы.

        Chromium масштабирует всё сам и без потери резкости, поэтому
        пересобирать разметку под каждый размер, как раньше, не нужно.
        """

        self.view.setZoomFactor(max(0.5, min(2.5, (percent or 100) / 100)))

        # Страница меряет себя в своих пикселях и смены зума не видит:
        # без этого окно остаётся прежнего размера и режет панель.
        self.run("window.__refit && window.__refit()")

    # --- положение ---

    def restore_position(self, settings):
        from PySide6.QtCore import QPoint

        saved = settings.get("position")

        if saved:
            point = QPoint(saved[0], saved[1])

            if any(
                screen.availableGeometry().contains(point)
                for screen in QApplication.screens()
            ):
                self.move(point)

                return

        self.reset_position()

    def reset_position(self):
        corner = QApplication.primaryScreen().availableGeometry().topLeft()

        self.move(corner.x() + 40, corner.y() + 40)
        self.save_position()

    def save_position(self):
        settings_store.update(position=[self.x(), self.y()])

    # --- уточнения от игрока ---

    def tell_app(self, path, payload):
        """Отметки игрока шлём в фоне: клик не должен ждать сеть."""

        def post():
            try:
                request = urllib.request.Request(
                    "http://localhost:3000" + path,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                )

                urllib.request.urlopen(request, timeout=2)

            except (urllib.error.URLError, OSError):
                pass

        threading.Thread(target=post, daemon=True).start()

    # --- закрытие ---

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()

    def closeEvent(self, event):
        """Закрыли панель — приложение остаётся.

        Раньше крестик на панели закрывал всё, вместе с меню и
        справочником. Но панель — это одно из окон приложения, а не само
        приложение: закрыть её и остаться в меню должно быть можно.
        Выход целиком — только кнопкой «Выход» в меню.
        """

        event.ignore()

        print("панель убрана, приложение продолжает работать")

        self.escape.hide()
        self.dismissed = True
        self.forced = False

        self.set_panel_visible(False)

        # Иначе видимых окон не остаётся вовсе: программа работает, а
        # увидеть её можно только через значок в трее, который Windows
        # по умолчанию прячет под стрелку.
        #
        # Через таймер, а не сразу: создавать окно прямо в обработчике
        # закрытия другого окна Qt не даёт — меню молча не появлялось.
        QTimer.singleShot(0, self.open_menu)


def open_log():
    """Куда писать, когда консоли нет.

    Под pythonw (и в собранном приложении) `sys.stdout` равен None, и
    любой print роняет программу на ровном месте. Поэтому вывод уходит
    в файл рядом с данными: он же пригодится, когда что-то сломается у
    игрока, а консоли, чтобы посмотреть, у него нет.
    """

    if sys.stdout is not None:
        return

    path = USER_DIR / "helper.log"

    try:
        path.parent.mkdir(parents=True, exist_ok=True)

        handle = open(path, "a", encoding="utf-8", buffering=1)

    except OSError:
        # Даже если писать некуда, работать программа должна.
        handle = open(os.devnull, "w", encoding="utf-8")

    sys.stdout = handle
    sys.stderr = handle

    print("")
    print("--- запуск " + time.strftime("%d.%m.%Y %H:%M:%S") + " ---")


def main():
    # Без своего идентификатора Windows показывает в панели задач значок
    # pythonw.exe: она группирует окна по нему, а не по значку окна.
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "yst4l.dota2helper"
            )

        except (OSError, AttributeError):
            pass

    open_log()

    app = QApplication(sys.argv)

    if ICON_PATH.exists():
        app.setWindowIcon(QIcon(str(ICON_PATH)))

    # Панель умеет прятаться, а меню и справочник — закрываться. Ни то ни
    # другое не должно ронять приложение: выход только по явной просьбе.
    app.setQuitOnLastWindowClosed(False)

    guard = SingleInstance()
    guard.asked_to_quit.connect(app.quit)

    if not guard.take_over():
        print("прежний оверлей не отвечает — запускаюсь рядом с ним")

    # Приёмник поднимаем сами, в фоновом потоке: отдельной программы с
    # чёрным окном консоли больше нет. Порядок важен — сначала уходит
    # прежний экземпляр, потом мы занимаем его порт.
    try:
        import app as helper

        helper.serve_in_background()

    except SystemExit as error:
        # Нет snapshot'а или порт не освободился: показать это игроку
        # некому, поэтому пишем в журнал и продолжаем — страница сама
        # скажет, что приёмник не отвечает.
        print(f"приёмник не поднялся: {error}")

    window = Overlay()

    # Показываем панель, только если решено её показывать: иначе она
    # мигает на рабочем столе на первой секунде запуска.
    window.check_dota()

    # Проверить — и сказать. Само ничего не качается, а меню может быть
    # закрыто, поэтому говорим значком у часов. Здесь, а не рядом с
    # приёмником: окна к тому моменту ещё нет.
    try:
        import app as helper

        helper.HELPER.look_for_updates(on_found=window.announce_update)

    except Exception as error:
        print(f"проверка обновлений не запустилась: {error}")

    # Запустили приложение, а матча нет — человек пришёл не за панелью,
    # а в само приложение. Показываем ему главное меню.
    if not window.isVisible():
        window.open_menu()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
