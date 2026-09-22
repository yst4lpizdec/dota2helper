"""Окно настроек оверлея.

Обычное окно с рамкой и заголовком, а не ещё один слой поверх игры:
настройки крутят между матчами, и вести себя оно должно как любое другое
окно Windows — его можно свернуть, подвинуть и закрыть крестиком.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from services.settings import BLOCKS
from theme import (
    CAPTION,
    DIM,
    FILL,
    GOLD,
    GOLD_BRIGHT,
    GOLD_EDGE,
    INK,
    MUTED,
    RAISED,
    SURFACE,
    TEXT,
    WELL,
)


STYLE = f"""
QWidget {{
    background: {SURFACE};
    color: {TEXT};
    font-size: 12px;
}}
QLabel#head {{
    color: {CAPTION};
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1px;
}}
QLabel#note  {{ color: {DIM}; font-size: 10px; }}
QLabel#value {{ color: {GOLD}; font-size: 11px; font-weight: 700; }}

QSlider::groove:horizontal {{
    height: 4px;
    background: {WELL};
    border: 1px solid {INK};
    border-radius: 2px;
}}
QSlider::sub-page:horizontal {{
    background: {FILL};
    border: 1px solid {INK};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    width: 11px;
    height: 11px;
    margin: -5px 0;
    background: {GOLD};
    border: 1px solid #000;
    border-radius: 2px;
}}
QSlider::handle:horizontal:hover {{ background: {GOLD_BRIGHT}; }}

QCheckBox {{ color: {TEXT}; spacing: 7px; padding: 3px 0; }}
QCheckBox::indicator {{
    width: 12px;
    height: 12px;
    background: {WELL};
    border: 1px solid {INK};
    border-radius: 2px;
}}
QCheckBox::indicator:hover {{ border-color: {GOLD}; }}
QCheckBox::indicator:checked {{
    background: {FILL};
    border: 1px solid {GOLD};
}}
QCheckBox:!checked {{ color: {DIM}; }}

QPushButton {{
    background: {RAISED};
    border: 1px solid {INK};
    border-top: 1px solid {GOLD_EDGE};
    border-radius: 3px;
    padding: 7px 13px;
    color: {MUTED};
    font-weight: 700;
}}
QPushButton:hover {{ color: {GOLD}; border-top-color: {GOLD}; }}
QPushButton:pressed {{ background: #12161c; }}

QFrame#sep {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {GOLD_EDGE},
        stop:0.65 rgba(125, 135, 148, 30),
        stop:1 rgba(0, 0, 0, 0));
}}
"""


class SettingsWindow(QWidget):
    """Сама ничего не сохраняет: только сообщает, что игрок передвинул.

    Применяет и записывает панель — она одна знает, что с этими числами
    делать, и остаётся единственным владельцем файла настроек.
    """

    opacity_changed = Signal(int)
    scale_changed = Signal(int)
    blocks_changed = Signal(list)
    position_reset = Signal()

    def __init__(self, settings):
        super().__init__()

        self.setWindowTitle("Dota2Helper — настройки")
        self.setStyleSheet(STYLE)
        self.setMinimumWidth(320)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(10)

        self.opacity = self._slider(
            outer,
            "ПРОЗРАЧНОСТЬ",
            40,
            100,
            int(settings.get("opacity") or 100),
            "%",
        )
        self.opacity.valueChanged.connect(self.opacity_changed.emit)

        self.scale = self._slider(
            outer,
            "РАЗМЕР",
            70,
            200,
            round(float(settings.get("scale") or 1.0) * 100),
            "%",
        )

        # Размер применяем по отпусканию: каждая ступень пересобирает
        # панель целиком, и делать это на каждый пиксель ползунка незачем.
        self.scale.sliderReleased.connect(
            lambda: self.scale_changed.emit(self.scale.value())
        )

        note = QLabel("панель можно тянуть и за боковой край")
        note.setObjectName("note")
        outer.addWidget(note)

        outer.addWidget(self._separator())

        caption = QLabel("ЧТО ПОКАЗЫВАТЬ")
        caption.setObjectName("head")
        outer.addWidget(caption)

        hidden = set(settings.get("hidden") or [])

        self.boxes = {}

        # В две колонки: одиннадцать галочек столбиком превращали окно
        # настроек в простыню выше самой панели.
        grid = QGridLayout()
        grid.setContentsMargins(0, 2, 0, 2)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(0)

        rows = (len(BLOCKS) + 1) // 2

        for index, (key, title) in enumerate(BLOCKS):
            box = QCheckBox(title)
            box.setChecked(key not in hidden)
            box.stateChanged.connect(self._emit_blocks)

            grid.addWidget(box, index % rows, index // rows)

            self.boxes[key] = box

        outer.addLayout(grid)

        outer.addWidget(self._separator())

        buttons = QHBoxLayout()

        reset = QPushButton("Вернуть панель на экран")
        reset.clicked.connect(self.position_reset.emit)

        close = QPushButton("Закрыть")
        close.clicked.connect(self.close)

        buttons.addWidget(reset)
        buttons.addStretch()
        buttons.addWidget(close)

        outer.addLayout(buttons)

    def _slider(self, outer, title, low, high, value, suffix):
        row = QHBoxLayout()

        caption = QLabel(title)
        caption.setObjectName("head")

        amount = QLabel(f"{value}{suffix}")
        amount.setObjectName("value")

        row.addWidget(caption)
        row.addStretch()
        row.addWidget(amount)

        outer.addLayout(row)

        slider = QSlider(Qt.Horizontal)
        slider.setRange(low, high)
        slider.setValue(value)
        slider.valueChanged.connect(
            lambda current: amount.setText(f"{current}{suffix}")
        )

        outer.addWidget(slider)

        return slider

    def _separator(self):
        line = QFrame()
        line.setObjectName("sep")
        line.setFixedHeight(1)

        return line

    def _emit_blocks(self):
        self.blocks_changed.emit(
            [key for key, box in self.boxes.items() if not box.isChecked()]
        )
