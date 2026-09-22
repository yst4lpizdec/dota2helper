"""Оверлей на Qt: иконки предметов и героев вместо текстовых списков.

Читает готовое состояние с localhost:3000/state и ничего не считает сам.
Окно безрамочное, поверх игры, таскается мышью, Esc закрывает.

Запуск:  python overlay_qt.py   (при запущенном app.py)
"""

import json
import sys
import threading
import urllib.error
import urllib.request

from PySide6.QtCore import QObject, Qt, QSize, Signal
from PySide6.QtGui import QCursor, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from services import settings as settings_store
from theme import (
    CAPTION,
    DIM,
    EMBER,
    GOLD,
    GOLD_EDGE,
    INK,
    MUTED,
    SURFACE,
    TEXT,
    WELL,
)
from services.fonts import load_into_qt
from services.icons import ability_icon, hero_icon, item_icon
from services.single_instance import SingleInstance
from settings_window import SettingsWindow
from services.winapi import MOD_ALT, MOD_CONTROL, Hotkeys, set_click_through


STATE_URL = "http://localhost:3000/state"
LANE_URL = "http://localhost:3000/lane"
REFRESH_MS = 1500
MAX_FAILURES = 4

ITEM_SIZE = 40
HERO_SIZE = 34
ABILITY_SIZE = 30

FULL_WIDTH = 430
COMPACT_WIDTH = 250

# Насколько панель можно растянуть и ужать, относительно обычного размера.
MIN_SCALE = 0.7
MAX_SCALE = 2.0

# Масштаб меняем ступеньками: на каждую ступень иконки перерисовываются
# в новом размере, и делать это на каждый пиксель движения мыши незачем.
SCALE_STEP = 0.05

# Ширина полосы у края окна, за которую его тянут.
EDGE = 9


# Единственная секция, которая остаётся в компактном режиме: в бою нужен
# один ответ — что покупать следующим.
COMPACT_BLOCKS = {"next"}

# До этой минуты «что дальше» берём из ранней закупки, после — из кора.
CORE_AFTER = 600

HINT = (
    "клик по врагу — он против тебя на линии"
    "   ·   боковой край — размер"
    "   ·   Ctrl+Alt+D свернуть"
)

def build_style(scale):
    """Стиль панели под текущий масштаб.

    Размеры шрифтов живут здесь, а не константами: когда игрок тянет
    панель за край, вместе с иконками должен расти и текст, иначе
    получается растянутое окно с прежними мелкими буквами.

    Палитра взята от интерфейса самой игры: холодная тёмно-синяя подложка
    с градиентом, тёплое золото в акцентах, фаска по верхней кромке.
    Прошлый вариант был тёмной темой редактора кода и выглядел поверх
    Dota как чужое приложение.
    """

    def px(base):
        return max(8, round(base * scale))

    return f"""
#card {{
    background: {SURFACE};
    border: 1px solid {INK};
    border-top: 1px solid {GOLD_EDGE};
    border-radius: {px(3)}px;
}}
#hero    {{ color: {GOLD}; font-size: {px(17)}px; font-weight: 700; }}
#sub     {{ color: {MUTED}; font-size: {px(11)}px; }}
#caption {{ color: {CAPTION}; font-size: {px(10)}px; font-weight: 700;
           letter-spacing: 1px; }}
#value   {{ color: {TEXT}; font-size: {px(12)}px; }}
#time       {{ color: {MUTED}; font-size: {px(10)}px; }}
#time_boost {{ color: {GOLD}; font-size: {px(10)}px; font-weight: 700; }}
#legend     {{ color: {DIM}; font-size: {px(10)}px; }}
#lane    {{ color: {EMBER}; font-size: {px(11)}px; }}
#hint    {{ color: {DIM}; font-size: {px(10)}px; }}
#mode    {{ color: {EMBER}; font-size: {px(10)}px; font-weight: 700; }}
#sep     {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
           stop:0 {GOLD_EDGE},
           stop:0.65 rgba(125, 135, 148, 30),
           stop:1 rgba(0, 0, 0, 0)); }}
#btn     {{ color: {MUTED}; font-size: {px(14)}px; font-weight: 700;
           padding: 0 {px(4)}px; }}
#btn_text {{ color: {MUTED}; font-size: {px(10)}px; font-weight: 700;
           letter-spacing: 1px; padding: 0 {px(4)}px; }}
#btn_text:hover {{ color: {GOLD}; }}
#btn:hover {{ color: {GOLD}; }}
#slot    {{ border: 1px solid {INK};
           border-radius: {px(2)}px;
           background-color: {WELL}; }}
#slot_boost {{ border: 1px solid {GOLD};
           border-radius: {px(2)}px;
           background-color: rgba(48, 38, 18, 230); }}
#hero_slot {{ border: 1px solid {INK};
           border-radius: {px(2)}px; }}
#portrait {{ border: 1px solid {GOLD_EDGE};
           border-radius: {px(2)}px;
           background-color: {WELL}; }}
#hero_lane {{ border: 1px solid {EMBER};
           border-radius: {px(2)}px;
           background-color: rgba(58, 30, 14, 230); }}
"""

ESCAPE_STYLE = """
#escape {
    background-color: rgb(22, 27, 34);
    border: 1px solid #f0883e;
    border-radius: 7px;
}
#escape_text {
    color: #f0883e;
    font-size: 12px;
    font-weight: 600;
    border: none;
    background: transparent;
}
"""

TIP_STYLE = """
#plate {
    background-color: rgb(22, 27, 34);
    border: 1px solid #e3b341;
    border-radius: 7px;
}
#tip {
    color: #f0f6fc;
    font-size: 13px;
    font-weight: 600;
    border: none;
    background: transparent;
}
"""


# Хранение настроек живёт в services.settings: тот же файл читает окно
# настроек, и держать два разных способа его разбирать незачем.
load_settings = settings_store.load
save_settings = settings_store.save


# Раскодированные иконки: одна и та же картинка рисуется десятки раз
# за матч, и читать её с диска каждый раз — это фриз на пересборке панели.
_PIXMAPS = {}


def pixmap(path, size, dim=False, by_height=False):
    key = (str(path), size, dim, by_height)
    cached = _PIXMAPS.get(key)

    if cached is not None:
        return cached

    image = QPixmap(str(path))

    if by_height:
        image = image.scaledToHeight(size, Qt.SmoothTransformation)
    else:
        image = image.scaled(
            size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation
        )

    if dim:
        # Гасим картинку один раз здесь, а не QGraphicsOpacityEffect'ом:
        # эффект заставляет Qt перерисовывать виджет через отдельный буфер
        # на каждый кадр, а поверх игры это дорого.
        faded = QPixmap(image.size())
        faded.fill(Qt.transparent)

        painter = QPainter(faded)
        painter.setOpacity(0.35)
        painter.drawPixmap(0, 0, image)
        painter.end()

        image = faded

    _PIXMAPS[key] = image

    return image


def stale_mark(entry, text):
    """Подпись для того, чего уже нет в текущей раскладке героя.

    Такие строки приходят из матчей на старых патчах. Молча их прятать
    нельзя — тогда в прокачке появится дырка, — но и выдавать за
    действующий совет тоже: помечаем прямо в тексте.
    """

    if not entry.get("stale"):
        return text

    return (
        f"<span style='color:#6e7681'>{text}"
        f" <i>(старый патч)</i></span>"
    )


class Poller(QObject):
    """Сеть в отдельном потоке.

    Раньше оверлей дёргал urlopen прямо в GUI-потоке: пока сервер считал
    рекомендацию, окно не обрабатывало ни движение мыши, ни клики — отсюда
    и «минуту выбирал героя».
    """

    arrived = Signal(object)

    def __init__(self):
        super().__init__()

        self.tick = threading.Event()
        self.stopped = False
        self.pending_lane = None
        self.lock = threading.Lock()

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self.stopped = True
        self.tick.set()

    def send_lane(self, heroes):
        """Ставит выбор линии в очередь и будит поток: клик возвращается
        мгновенно, отправка происходит за кадром."""

        with self.lock:
            self.pending_lane = list(heroes)

        self.tick.set()

    def _loop(self):
        while not self.stopped:
            with self.lock:
                lane, self.pending_lane = self.pending_lane, None

            if lane is not None:
                self._post_lane(lane)

            self.arrived.emit(self._fetch())

            self.tick.wait(REFRESH_MS / 1000)
            self.tick.clear()

    def _fetch(self):
        try:
            with urllib.request.urlopen(STATE_URL, timeout=2) as response:
                return json.load(response)

        except (urllib.error.URLError, OSError, json.JSONDecodeError):
            return None

    def _post_lane(self, heroes):
        try:
            request = urllib.request.Request(
                LANE_URL,
                data=json.dumps({"heroes": heroes}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )

            urllib.request.urlopen(request, timeout=2)

        except (urllib.error.URLError, OSError):
            pass


class IconRow(QWidget):
    """Ряд иконок: предмет, под ним тайминг и прибавка против пика."""

    def __init__(self, on_hover, size=ITEM_SIZE):
        super().__init__()

        self.on_hover = on_hover
        self.size = size
        self.row = QHBoxLayout(self)
        self.row.setContentsMargins(0, 0, 0, 0)
        self.row.setSpacing(6)
        self.row.addStretch()

    def clear(self):
        while self.row.count() > 1:
            item = self.row.takeAt(0)

            if item.widget():
                item.widget().deleteLater()

    def show_items(self, entries, with_time=True):
        self.clear()

        for entry in entries:
            path = item_icon(entry["item"])

            cell = QWidget()
            column = QVBoxLayout(cell)
            column.setContentsMargins(0, 0, 0, 0)
            # Зазор между иконкой и таймингом под ней: без него подпись
            # налезала на картинку, когда панели не хватало высоты.
            column.setSpacing(3)

            title = entry.get("display", entry["item"])

            if entry.get("count", 1) > 1:
                title += f" ×{entry['count']}"

            if entry.get("cost"):
                title += f"  ·  {entry['cost'] * entry.get('count', 1)} золота"

            if entry.get("owned"):
                title += "  ·  уже куплено"

            if entry.get("share"):
                title += f"  ·  {entry['share']}% игр"

            if entry.get("boost", 0) >= 3:
                title += f"  ·  +{entry['boost']} против этого пика"

            icon = Hoverable(title, self.on_hover)
            icon.setAlignment(Qt.AlignCenter)

            # Рамка рисуется вокруг картинки, поэтому ячейка на два
            # пикселя больше самой иконки — иначе рамка съедала бы её край.
            icon.setFixedSize(QSize(self.size + 2, self.size + 2))

            # Подсвечиваем то, что важнее обычного против этого пика.
            icon.setObjectName(
                "slot_boost" if entry.get("boost", 0) >= 3 else "slot"
            )

            if path:
                # Уже в инвентаре — не прячем, чтобы набор читался целиком,
                # но рисуем бледным: покупать это больше не нужно.
                icon.setPixmap(
                    pixmap(path, self.size, dim=bool(entry.get("owned")))
                )
            else:
                icon.setText(entry.get("display", "?")[:6])
                icon.setWordWrap(True)

            column.addWidget(icon, alignment=Qt.AlignHCenter)

            if not with_time and entry.get("count", 1) > 1:
                amount = QLabel(f"×{entry['count']}")
                amount.setObjectName("time")
                amount.setAlignment(Qt.AlignHCenter)

                column.addWidget(amount)

            if with_time:
                # Под иконкой — когда покупают. Если предмет важен против
                # этого пика, время подсвечено, чтобы было видно не только
                # ЧТО брать, но и КОГДА.
                caption = QLabel(
                    f"{entry['median_time'] // 60}м"
                    if entry.get("median_time")
                    else ""
                )
                caption.setObjectName(
                    "time_boost" if entry.get("boost", 0) >= 3 else "time"
                )
                caption.setAlignment(Qt.AlignHCenter)

                column.addWidget(caption)

            self.row.insertWidget(self.row.count() - 1, cell)


class AbilityRow(QWidget):
    """Порядок прокачки иконками, а не текстом.

    Текстом это выглядело как «1-я Wraithfire Blast › 3-я Mortal Strike ›
    …» в три строки с переносами посреди названий — читать невозможно.
    Иконки узнаются мгновенно, а под каждой стоит номер клавиши.
    """

    def __init__(self, on_hover, size=ABILITY_SIZE):
        super().__init__()

        self.on_hover = on_hover
        self.size = size

        self.row = QHBoxLayout(self)
        self.row.setContentsMargins(0, 0, 0, 0)
        self.row.setSpacing(4)
        self.row.addStretch()

    def show_abilities(self, entries):
        while self.row.count() > 1:
            item = self.row.takeAt(0)

            if item.widget():
                item.widget().deleteLater()

        for order, entry in enumerate(entries, start=1):
            path = ability_icon(entry.get("icon"))
            title = entry.get("display") or entry["ability"]

            cell = QWidget()
            column = QVBoxLayout(cell)
            column.setContentsMargins(0, 0, 0, 0)
            column.setSpacing(2)

            icon = Hoverable(
                f"{order}-м уровнем: {title}", self.on_hover
            )
            icon.setAlignment(Qt.AlignCenter)
            icon.setFixedSize(QSize(self.size + 2, self.size + 2))
            icon.setObjectName("slot")

            if path:
                icon.setPixmap(pixmap(path, self.size))
            else:
                icon.setObjectName("slot")
                icon.setText(title[:4])
                icon.setWordWrap(True)

            column.addWidget(icon, alignment=Qt.AlignHCenter)

            mark = QLabel(entry.get("mark") or "")
            mark.setObjectName("time")
            mark.setAlignment(Qt.AlignHCenter)

            column.addWidget(mark)

            self.row.insertWidget(self.row.count() - 1, cell)


class Tip(QWidget):
    """Всплывающая подпись над иконкой.

    Своё окно, а не системный тултип: тот появляется с задержкой,
    выглядит чужеродно и в игре почти не читается.
    """

    def __init__(self):
        super().__init__(
            None,
            Qt.ToolTip | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint,
        )

        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setStyleSheet(TIP_STYLE)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        # Подложку рисует именно QFrame: у прозрачного QWidget фон из стилей
        # не отрисовывается, и подпись висела голым текстом поверх панели.
        plate = QFrame()
        plate.setObjectName("plate")

        inner = QVBoxLayout(plate)
        inner.setContentsMargins(10, 6, 10, 6)

        self.label = QLabel("")
        self.label.setObjectName("tip")

        inner.addWidget(self.label)
        outer.addWidget(plate)

    def show_above(self, widget, text):
        """Ставит подпись по центру над иконкой, а если сверху нет места —
        под ней."""

        if not text:
            self.hide()

            return

        self.label.setText(text)
        self.adjustSize()

        top_left = widget.mapToGlobal(widget.rect().topLeft())
        screen = widget.screen().availableGeometry()

        x = top_left.x() + widget.width() // 2 - self.width() // 2
        y = top_left.y() - self.height() - 4

        if y < screen.top():
            y = top_left.y() + widget.height() + 4

        x = max(screen.left(), min(x, screen.right() - self.width()))

        self.move(x, y)
        self.show()
        self.raise_()


class Escape(QWidget):
    """Кнопка возврата, когда панель перестала ловить мышь.

    Живёт отдельным окном намеренно: у панели в сквозном режиме клики
    проваливаются в игру целиком, вместе с её собственными кнопками.
    Первый вариант ровно на этом и попался — выключатель оказался внутри
    того, что он выключает.
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
        """Ставит кнопку над панелью, а если сверху нет места — под ней."""

        self.adjustSize()

        top_left = window.mapToGlobal(window.rect().topLeft())
        screen = window.screen().availableGeometry()

        x = top_left.x()
        y = top_left.y() - self.height() - 2

        if y < screen.top():
            y = top_left.y() + window.height() + 2

        x = max(screen.left(), min(x, screen.right() - self.width()))

        self.move(x, y)


class Hoverable(QLabel):
    """Иконка, которая подписывается при наведении и умеет отвечать на клик."""

    def __init__(self, caption, on_hover, on_click=None):
        super().__init__()

        self.caption = caption
        self.on_hover = on_hover
        self.on_click = on_click

        if on_click:
            self.setCursor(Qt.PointingHandCursor)

    def enterEvent(self, event):
        self.on_hover(self.caption, self)

    def leaveEvent(self, event):
        self.on_hover("", self)

    def mousePressEvent(self, event):
        if self.on_click and event.button() == Qt.LeftButton:
            self.on_click()

            # Клик по иконке не должен таскать окно.
            event.accept()


class HeroRow(QWidget):
    """Ряд вражеских героев: подсветка лайн-оппонента, клик — выбрать вручную."""

    def __init__(self, on_hover, on_pick, size=HERO_SIZE):
        super().__init__()

        self.on_hover = on_hover
        self.on_pick = on_pick
        self.size = size

        self.row = QHBoxLayout(self)
        self.row.setContentsMargins(0, 0, 0, 0)
        self.row.setSpacing(5)
        self.row.addStretch()

    def show_heroes(self, heroes, highlight=()):
        while self.row.count() > 1:
            item = self.row.takeAt(0)

            if item.widget():
                item.widget().deleteLater()

        for hero in heroes:
            path = hero_icon(hero)
            title = hero.replace("_", " ").title()

            icon = Hoverable(
                title,
                self.on_hover,
                on_click=lambda name=hero: self.on_pick(name),
            )
            icon.setAlignment(Qt.AlignCenter)
            icon.setFixedHeight(self.size + 2)
            icon.setObjectName(
                "hero_lane" if hero in highlight else "hero_slot"
            )

            if path:
                icon.setPixmap(pixmap(path, self.size, by_height=True))
            else:
                icon.setText(hero[:4])

            self.row.insertWidget(self.row.count() - 1, icon)


class Overlay(QWidget):
    def __init__(self):
        super().__init__()

        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet(build_style(1.0))

        # Без этого движения мыши приходят только с зажатой кнопкой,
        # и курсор у края окна не успевает превратиться в стрелки.
        self.setMouseTracking(True)

        self.failures = 0
        self.last_signature = None
        self.last_state = {}
        self.drag_offset = None
        self.compact = False
        self.click_through = False
        self.settings_window = None
        self.hidden = set()
        self.buttons = {}
        self.scale = 1.0
        # Пока тянем за край окна: с какой стороны и от чего считаем.
        self.resize_edge = None
        self.resize_from = None
        # Кого игрок отметил кликом как противника на линии.
        self.lane_manual = []

        self.tip = Tip()
        self.escape = Escape(lambda: self.set_click_through(False))

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)

        card = QFrame()
        card.setObjectName("card")

        # Тени у карточки нет намеренно: QGraphicsDropShadowEffect размывает
        # всё окно заново на каждой перерисовке, а окно у нас прозрачное и
        # поверх игры — это самые дорогие кадры из возможных.
        outer.addWidget(card)

        body = QVBoxLayout(card)
        body.setContentsMargins(14, 12, 14, 14)
        body.setSpacing(6)

        # Шапка: иконка героя, имя, позиция, винрейт.
        head = QHBoxLayout()
        head.setSpacing(8)

        self.hero_icon = QLabel()
        self.hero_icon.setObjectName("portrait")
        self.hero_icon.setAlignment(Qt.AlignCenter)
        self.hero_icon.setFixedHeight(40)

        head.addWidget(self.hero_icon)

        titles = QVBoxLayout()
        titles.setSpacing(0)

        self.hero_name = QLabel("ожидаю игру…")
        self.hero_name.setObjectName("hero")

        self.subtitle = QLabel("")
        self.subtitle.setObjectName("sub")

        titles.addWidget(self.hero_name)
        titles.addWidget(self.subtitle)

        head.addLayout(titles)
        head.addStretch()

        # Переключатель сквозной мыши, он же признак режима: отдельная
        # кнопка и отдельная лампочка про одно и то же — лишнее, а без
        # признака сквозной режим выглядит как зависший оверлей.
        self.mode = Hoverable(
            "кому достаются клики: панели или игре · Ctrl+Alt+F",
            self.set_hint,
            on_click=lambda: self.set_click_through(not self.click_through),
        )
        self.mode.setObjectName("mode")
        self.mode.setText("мышь")
        self.mode.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        head.addWidget(self.mode)

        # Глифы намеренно простые: экзотические символы есть не в каждом
        # шрифте, а панель рисуется шрифтом Dota 2, если он нашёлся.
        # Шестерёнки тут нет намеренно: панель рисуется шрифтом Dota 2,
        # а в нём нет ни U+2699, ни даже крестика — символ либо подхватит
        # чужой запасной шрифт, либо не отрисуется вовсе. Слово надёжнее.
        for key, glyph, caption, action in (
            ("settings", "опции", "настройки оверлея", self.open_settings),
            ("compact", "–", "свернуть · Ctrl+Alt+D",
             lambda: self.set_compact(not self.compact)),
            ("close", "×", "закрыть · Esc", self.close),
        ):
            button = Hoverable(caption, self.set_hint, on_click=action)
            button.setObjectName("btn_text" if len(glyph) > 1 else "btn")
            button.setText(glyph)

            head.addWidget(button)

            self.buttons[key] = button

        body.addLayout(head)

        self.enemies = HeroRow(self.set_hint, self.pick_lane)
        body.addWidget(self.enemies)

        self.lane = QLabel("")
        self.lane.setObjectName("lane")
        body.addWidget(self.lane)

        self.legend = QLabel(
            f"<span style='color:{GOLD}'>золотая рамка</span>"
            " — сильнее против этого пика"
            f"&nbsp; <span style='color:{DIM}'>·</span> &nbsp;"
            "цифра — минута покупки"
        )
        self.legend.setObjectName("legend")
        self.legend.setTextFormat(Qt.RichText)
        self.legend.setWordWrap(True)
        body.addWidget(self.legend)

        self.blocks = {}
        self.sections = {}
        self.captions = {}

        for key, title in (
            ("next", "ЧТО БРАТЬ ДАЛЬШЕ"),
            ("starting", "СТАРТ"),
            ("early", "РАННЯЯ"),
            ("consumables", "РАСХОДНИКИ И ВАРДЫ"),
            ("core", "КОР"),
            ("situational", "СИТУАТИВНО ПРОТИВ ПИКА"),
        ):
            row = IconRow(self.set_hint)

            self.sections[key] = row

            body.addWidget(self.block(key, title, row))

        self.skills = AbilityRow(self.set_hint)

        body.addWidget(self.block("skills", "ПОРЯДОК ПРОКАЧКИ", self.skills))

        self.talents = QLabel("-")
        self.talents.setObjectName("value")
        self.talents.setTextFormat(Qt.RichText)
        self.talents.setWordWrap(True)

        body.addWidget(self.block("talents", "ТАЛАНТЫ", self.talents))

        # Строка подсказки: сюда пишется название того, на что навели мышь.
        self.hint = QLabel(HINT)
        self.hint.setObjectName("hint")
        self.hint.setWordWrap(True)
        body.addWidget(self.hint)

        self.restore_position()

        self.hotkeys = Hotkeys(
            {
                "compact": (MOD_CONTROL | MOD_ALT, ord("D")),
                "click_through": (MOD_CONTROL | MOD_ALT, ord("F")),
            }
        )
        self.hotkeys.pressed.connect(self.on_hotkey)
        self.hotkeys.start()

        settings = load_settings()

        self.hidden = set(settings.get("hidden") or [])

        self.setWindowOpacity(max(0.2, (settings.get("opacity") or 100) / 100))
        self.set_scale(float(settings.get("scale") or 1.0))
        self.set_compact(bool(settings.get("compact")))

        self.poller = Poller()
        self.poller.arrived.connect(self.apply_state)
        self.poller.start()

    def block(self, key, title, content):
        """Секция панели одним куском: разделитель, заголовок, содержимое.

        Куском — чтобы компактный режим прятал секцию одним вызовом,
        а не гонялся за тремя виджетами по отдельности.
        """

        container = QWidget()

        column = QVBoxLayout(container)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(6)

        separator = QFrame()
        separator.setObjectName("sep")
        separator.setFixedHeight(1)
        column.addWidget(separator)

        caption = QLabel(title)
        caption.setObjectName("caption")
        column.addWidget(caption)

        column.addWidget(content)

        self.blocks[key] = container
        self.captions[key] = caption

        return container

    # --- перетаскивание мышью ---

    def edge_at(self, point):
        """С какого края окна находится точка: слева, справа или нигде.

        Верх и низ не трогаем: высота у панели своя, по содержимому,
        и тянуть её вниз было бы обманом — там нечего показывать.
        """

        if point.x() <= EDGE:
            return "left"

        if point.x() >= self.width() - EDGE:
            return "right"

        return None

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return

        point = event.globalPosition().toPoint()
        edge = self.edge_at(event.position().toPoint())

        if edge:
            self.resize_edge = edge
            self.resize_from = (point.x(), self.width(), self.x())

            return

        self.drag_offset = point - self.pos()

    def mouseMoveEvent(self, event):
        point = event.globalPosition().toPoint()

        if self.resize_edge:
            start_x, start_width, window_x = self.resize_from
            shift = point.x() - start_x

            if self.resize_edge == "left":
                width = start_width - shift
            else:
                width = start_width + shift

            self.set_scale(width / self.base_width())

            # Тянем за левый край — правый должен остаться на месте.
            if self.resize_edge == "left":
                self.move(window_x + start_width - self.width(), self.y())

            return

        if self.drag_offset is not None:
            self.move(point - self.drag_offset)

            return

        self.setCursor(
            Qt.SizeHorCursor
            if self.edge_at(event.position().toPoint())
            else Qt.ArrowCursor
        )

    def mouseReleaseEvent(self, event):
        if self.drag_offset is not None:
            self.save_position()

        if self.resize_edge:
            self.save_scale()

        self.drag_offset = None
        self.resize_edge = None

    def leaveEvent(self, event):
        self.setCursor(Qt.ArrowCursor)

    # --- размер ---

    def base_width(self):
        return COMPACT_WIDTH if self.compact else FULL_WIDTH

    def set_scale(self, scale):
        """Меняет размер панели целиком: и иконки, и шрифты.

        Ступеньками по SCALE_STEP: на каждой ступени иконки
        перерисовываются в новом размере, и делать это на каждый пиксель
        движения мыши незачем.
        """

        scale = min(MAX_SCALE, max(MIN_SCALE, scale))
        scale = round(scale / SCALE_STEP) * SCALE_STEP

        if abs(scale - self.scale) < SCALE_STEP / 2:
            return

        self.scale = scale

        self.setStyleSheet(build_style(scale))

        self.enemies.size = round(HERO_SIZE * scale)
        self.hero_icon.setFixedHeight(round(38 * scale))

        for row in self.sections.values():
            row.size = round(ITEM_SIZE * scale)

        self.setFixedWidth(round(self.base_width() * scale))
        self.rebuild()

    def save_scale(self):
        settings = load_settings()
        settings["scale"] = round(self.scale, 2)
        save_settings(settings)

    def fit(self):
        """Подгоняет высоту панели под содержимое.

        Одного adjustSize() мало: у подписей с переносом высота известна
        только после того, как разметка пересчитает ширину. Из-за этого
        нижние блоки — таланты и строка подсказки — обрезались.
        """

        layout = self.layout()

        layout.invalidate()
        layout.activate()

        # Именно heightForWidth: обычный sizeHint не знает, во сколько
        # строк развернётся подпись с переносом, и отдаёт высоту меньше
        # настоящей — отсюда и обрезанные таланты внизу.
        height = (
            layout.heightForWidth(self.width())
            if layout.hasHeightForWidth()
            else 0
        )

        self.resize(self.width(), max(height, self.sizeHint().height()))

    def rebuild(self):
        """Пересобирает панель из последнего состояния."""

        if not self.last_state:
            self.fit()

            return

        self.last_signature = None

        self.apply_state(self.last_state)
        self.fit()

    # --- позиция окна ---

    def restore_position(self):
        """Возвращает окно туда, куда его перетащили в прошлый раз.

        Если сохранённая точка не попадает ни на один монитор (монитор
        отключили), ставим в угол основного экрана, а не куда попало.
        """

        from PySide6.QtCore import QPoint

        saved = load_settings().get("position")

        if saved:
            point = QPoint(saved[0], saved[1])

            if any(
                screen.availableGeometry().contains(point)
                for screen in QApplication.screens()
            ):
                self.move(point)

                return

        corner = QApplication.primaryScreen().availableGeometry().topLeft()
        self.move(corner.x() + 40, corner.y() + 40)

    def save_position(self):
        settings = load_settings()
        settings["position"] = [self.pos().x(), self.pos().y()]
        save_settings(settings)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.tip.hide()
            self.close()

    def closeEvent(self, event):
        self.tip.hide()
        self.escape.hide()
        self.poller.stop()

        super().closeEvent(event)

    # --- данные ---

    def set_hint(self, text, widget=None):
        if widget is not None:
            self.tip.show_above(widget, text)

    def pick_lane(self, hero):
        """Клик по вражескому герою: отметить или снять отметку линии.

        Отметку показываем сразу, не дожидаясь ответа сервера: иначе
        каждый клик стоил бы кругового похода в сеть.
        """

        chosen = list(self.lane_manual)

        if hero in chosen:
            chosen.remove(hero)
        else:
            chosen.append(hero)

        self.lane_manual = chosen

        self.enemies.show_heroes(
            (self.last_state.get("match") or {}).get("enemies", []), chosen
        )

        self.poller.send_lane(chosen)

    # --- режимы ---

    def on_hotkey(self, action):
        if action == "compact":
            self.set_compact(not self.compact)
        elif action == "click_through":
            self.set_click_through(not self.click_through)

    def open_settings(self):
        """Окно настроек. Одно на всё приложение: второе такое же окно
        показывало бы те же значения и спорило само с собой."""

        if self.settings_window is None:
            window = SettingsWindow(load_settings())

            window.opacity_changed.connect(self.set_opacity)
            window.scale_changed.connect(
                lambda percent: self.set_scale(percent / 100)
            )
            window.blocks_changed.connect(self.set_hidden)
            window.position_reset.connect(self.reset_position)

            self.settings_window = window

        self.settings_window.show()
        self.settings_window.raise_()
        self.settings_window.activateWindow()

    def set_opacity(self, percent):
        self.setWindowOpacity(max(0.2, percent / 100))

        settings = load_settings()
        settings["opacity"] = percent
        save_settings(settings)

    def set_hidden(self, hidden):
        """Какие блоки игрок выключил в настройках."""

        self.hidden = set(hidden)

        settings = load_settings()
        settings["hidden"] = sorted(self.hidden)
        save_settings(settings)

        self.show_blocks()
        self.fit()

    def show_blocks(self):
        """Что видно прямо сейчас — из режима и из настроек сразу.

        Блок показывается, только если его разрешают оба: компактный
        режим прячет всё лишнее, а настройки — то, что игрок выключил
        насовсем.
        """

        for key, block in self.blocks.items():
            allowed = self.compact == (key in COMPACT_BLOCKS)

            block.setVisible(allowed and key not in self.hidden)

        for key, widget in (
            ("enemies", self.enemies),
            ("lane", self.lane),
            ("legend", self.legend),
            ("hint", self.hint),
        ):
            widget.setVisible(not self.compact and key not in self.hidden)

        # В компактном виде шапка ужимается до портрета и кнопок: на
        # 250 пикселях имя героя всё равно обрезалось до «Ske».
        self.subtitle.setVisible(not self.compact)
        self.hero_name.setVisible(not self.compact)
        self.mode.setVisible(not self.compact)
        self.buttons["settings"].setVisible(not self.compact)

    def reset_position(self):
        """Возвращает панель в угол основного экрана.

        Нужна, когда её утащили за край или отключили монитор, на котором
        она стояла: иначе окно не достать мышью.
        """

        corner = QApplication.primaryScreen().availableGeometry().topLeft()

        self.move(corner.x() + 40, corner.y() + 40)
        self.save_position()

    def set_compact(self, compact):
        """Компактный режим: только «что брать дальше».

        В бою полная панель занимает четверть экрана и читать её некогда,
        а один ряд иконок в углу — можно.
        """

        self.compact = compact
        self.tip.hide()

        self.show_blocks()

        self.setFixedWidth(round(self.base_width() * self.scale))
        self.show_mode()

        settings = load_settings()
        settings["compact"] = compact
        save_settings(settings)

        # Состав рядов зависит от режима, поэтому подпись прошлого
        # состояния больше не годится — пересобираем панель заново.
        self.rebuild()

    def set_click_through(self, enabled):
        """Сквозная мышь: клики уходят в игру, а не в панель."""

        self.click_through = enabled
        self.tip.hide()

        set_click_through(self, enabled)

        # Выход из режима всегда снаружи панели: изнутри его не нажать.
        if enabled:
            self.escape.place_over(self)
            self.escape.show()
            self.escape.raise_()
        else:
            self.escape.hide()

        self.show_mode()

    def show_mode(self):
        """Куда сейчас уходят клики — это видно в шапке всегда."""

        # Состояние несёт цвет, а не длина подписи: в шапке рядом имя
        # героя и три кнопки, и «мышь: панели» съедало имя целиком.
        # Что это такое, объясняет подсказка при наведении.
        self.mode.setStyleSheet(
            f"color: {EMBER};" if self.click_through else f"color: {DIM};"
        )

        self.buttons["compact"].setText("+" if self.compact else "–")

    def next_phase(self, match):
        """Из какой фазы брать совет «что дальше» — по часам матча."""

        return "early" if (match.get("clock") or 0) < CORE_AFTER else "core"

    def show_next(self, state, match):
        """Компактный ряд: ближайшие покупки и больше ничего."""

        phase = self.next_phase(match)

        self.sections["next"].show_items((state.get(phase) or [])[:4])

        self.captions["next"].setText(
            "ЧТО БРАТЬ ДАЛЬШЕ · "
            + ("ранняя" if phase == "early" else "кор")
        )

    def busy(self):
        """Курсор над панелью — значит игрок целится в иконку.

        Пересобирать ряды в этот момент нельзя: виджет под курсором
        удаляется, и клик уходит в пустоту.
        """

        if self.drag_offset is not None:
            return True

        # Сквозная мышь — значит по иконкам сейчас не целятся, и ждать
        # нечего: иначе панель замерла бы, стоит курсору оказаться над ней.
        if self.click_through:
            return False

        return self.rect().contains(self.mapFromGlobal(QCursor.pos()))

    def apply_state(self, state):
        # Перерисовываем только когда данные реально поменялись.
        # Раньше иконки пересоздавались каждые полторы секунды: виджет
        # под курсором исчезал, наведение сбрасывалось, клик терялся.
        match = (state or {}).get("match") or {}

        signature = json.dumps(
            {key: value for key, value in (state or {}).items()
             if key != "match"}
            | {
                "enemies": match.get("enemies"),
                # Не сами часы, а только фаза: часы идут всегда, и панель
                # пересобиралась бы на каждом опросе.
                "phase": self.next_phase(match),
            },
            sort_keys=True,
            ensure_ascii=False,
        ) if state is not None else None

        if state is not None and signature == self.last_signature:
            self.failures = 0

            return

        # Данные новые, но игрок сейчас водит мышью по панели: подождём
        # следующего опроса, состояние никуда не денется.
        if state is not None and self.busy():
            return

        self.last_signature = signature

        if state is not None:
            self.last_state = state

        # Иконки сейчас пересоздадутся — подсказка над старыми не нужна.
        self.tip.hide()

        if state is None:
            self.failures += 1

            if self.failures >= MAX_FAILURES:
                # Движок закрыли — уходим следом.
                self.close()

                return

            self.hero_name.setText("нет связи с Helper")

            return

        self.failures = 0

        if state.get("waiting") or state.get("error"):
            self.hero_name.setText("ожидаю игру…")
            self.subtitle.setText("")

            return

        hero = state["hero"]
        path = hero_icon(hero)

        if path:
            self.hero_icon.setPixmap(
                pixmap(path, round(38 * self.scale), by_height=True)
            )

        self.hero_name.setText(hero.replace("_", " ").title())

        # Разделители узкие: в шапке рядом кнопки, и подпись с широкими
        # отступами не влезала — «по 211 матчам» обрезалось до «по 211».
        self.subtitle.setText(
            f"{state['position'].replace('POSITION_', 'поз. ')}"
            f" · {state['winrate']}%"
            f" · {state['matches']} игр"
        )

        lane_against = state.get("lane_against", [])

        if state.get("lane_manual"):
            self.lane_manual = list(lane_against)

        if self.compact:
            # Остальные ряды сейчас скрыты — собирать их незачем.
            self.show_next(state, match)
            self.fit()

            return

        self.enemies.show_heroes(match.get("enemies", []), lane_against)

        if lane_against:
            mark = " (выбрано вручную)" if state.get("lane_manual") else ""

            self.lane.setText(
                "на линии против: "
                + ", ".join(n.replace("_", " ").title() for n in lane_against)
                + mark
            )
        else:
            self.lane.setText(
                f"врагов известно {state.get('enemies_known', 0)}/5"
            )

        purchase = state.get("starting_purchase") or {}
        owned = set(state.get("owned", []))

        if purchase.get("items"):
            self.sections["starting"].show_items(
                [
                    {**entry, "owned": entry["item"] in owned}
                    for entry in purchase["items"]
                ],
                with_time=False,
            )

            share = (
                f" · {purchase['share']}% игр" if purchase.get("share") else ""
            )

            self.captions["starting"].setText(
                f"СТАРТ · {purchase['total']}/{purchase['budget']} золота{share}"
            )
        else:
            self.sections["starting"].show_items(
                state.get("starting", [])[:8], with_time=False
            )
            self.captions["starting"].setText("СТАРТ")

        for phase in ("early", "core"):
            self.sections[phase].show_items(state.get(phase, [])[:7])

        # Расходники — отдельным рядом: по доле они выбивали из ранней
        # собственно предметы, а без них непонятно, что варды вообще нужны.
        self.sections["consumables"].show_items(
            state.get("consumables", [])[:8], with_time=False
        )

        self.sections["situational"].show_items(
            [
                {**entry, "boost": entry["score"], "median_time": 0}
                for entry in state.get("situational", [])[:7]
            ],
            with_time=False,
        )

        self.skills.show_abilities(state.get("skills", [])[:8])

        self.talents.setText(
            "<br>".join(
                f"<b>{entry['level']}</b>"
                + (
                    f"&nbsp;<span style='color:#f0883e'>{entry['side']}</span>"
                    if entry.get("side")
                    else ""
                )
                + "&nbsp;·&nbsp;"
                + stale_mark(entry, entry.get("display") or entry["talent"])
                for entry in state.get("talents", [])
            )
        )

        self.fit()


if __name__ == "__main__":
    app = QApplication(sys.argv)

    # Родной шрифт Dota 2, если игра установлена: с ним оверлей
    # смотрится частью интерфейса, а не чужим окном.
    family = load_into_qt()

    if family:
        app.setStyleSheet(f"* {{ font-family: '{family}'; }}")

    # Прежний оверлей уходит, место занимает этот. Так перезапуск чинит
    # залипшую панель, а не добавляет к ней вторую.
    guard = SingleInstance()
    guard.asked_to_quit.connect(app.quit)

    if not guard.take_over():
        print("прежний оверлей не отвечает — запускаюсь рядом с ним")

    window = Overlay()
    window.show()

    sys.exit(app.exec())
