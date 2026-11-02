"""The HUD: Jarvis's full-screen dashboard, in the style of the films.

A turning arc reactor in the middle shows what Jarvis is doing. Around it: the weather,
the computer's state, today's alarms, the analysed inbox and the news, with a ticker
and a command bar at the bottom. Animations only run while the HUD is on screen.
"""
import math
import random
import sys
import webbrowser
from datetime import datetime, timedelta

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QKeySequence, QPainter, QPen, QRadialGradient, QShortcut
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import ai, analysis, config, human_voice, sysinfo, windows
from .icons import draw_symbol

C = {
    "void": "#02060c", "panel": "#071320", "line": "#0f3348", "cyan": "#3ddcff", "ice": "#d7f7ff",
    "text": "#c9dce8", "muted": "#5a7b90", "amber": "#ffb43d", "red": "#ff4f62", "green": "#4cf0a3",
}
GROUP_COLORS = {"reply": C["amber"], "important": C["cyan"], "update": C["muted"], "low": C["line"]}
STATE_COLORS = {"Listening": C["ice"], "Thinking": C["amber"], "Speaking": C["cyan"]}
STATES = {"": "ONLINE", "Listening": "LISTENING", "Thinking": "THINKING", "Speaking": "SPEAKING"}

STYLE = f"""
QWidget {{ background: transparent; color: {C['text']}; }}
QLabel {{ background: transparent; }}
QToolTip {{ background: {C['panel']}; color: {C['text']}; border: 1px solid {C['line']}; }}
QLineEdit#hudInput {{ background: rgba(7, 19, 32, 215); border: 1px solid {C['line']}; padding: 10px 14px;
                      color: {C['ice']}; selection-background-color: #1d6f8f; }}
QLineEdit#hudInput:focus {{ border-color: {C['cyan']}; }}
QPushButton#hudRound {{ background: transparent; border: 1px solid {C['line']}; border-radius: 20px; padding: 0;
                        min-width: 40px; max-width: 40px; min-height: 40px; max-height: 40px; }}
QPushButton#hudRound:hover {{ border-color: {C['cyan']}; }}
QPushButton#hudMic {{ background: rgba(61, 220, 255, 30); border: 1px solid {C['cyan']}; border-radius: 20px; padding: 0;
                      min-width: 40px; max-width: 40px; min-height: 40px; max-height: 40px; }}
QPushButton#hudMic:hover {{ background: rgba(61, 220, 255, 70); }}
QFrame#hudRow {{ border: none; }}
QFrame#hudRow:hover {{ background: rgba(61, 220, 255, 14); }}
QScrollArea {{ border: none; }}
QScrollBar:vertical {{ background: transparent; width: 6px; }}
QScrollBar::handle:vertical {{ background: {C['line']}; min-height: 24px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical, QScrollBar::add-page:vertical,
QScrollBar::sub-page:vertical {{ height: 0; background: none; }}
"""


MAC = sys.platform == "darwin"
# Bahnschrift (Windows) and DIN Alternate (Mac) are both drawn from DIN, the
# engineering-drawing typeface, which suits a HUD. A Mac point is smaller, hence the scale.
FAMILIES = ["DIN Alternate", "Avenir Next", "Helvetica Neue"] if MAC else ["Bahnschrift", "Segoe UI", "Arial"]
SCALE = 4 / 3 if MAC else 1


def hud_font(size, weight=QFont.Weight.Normal, spacing=0.0):
    font = QFont()
    font.setFamilies(FAMILIES)
    font.setPointSizeF(size * SCALE)
    font.setWeight(weight)
    if spacing:
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    return font


def label(text="", size=10.0, color=C["text"], weight=QFont.Weight.Normal, spacing=0.0, wrap=False):
    widget = QLabel(text)
    widget.setFont(hud_font(size, weight, spacing))
    widget.setStyleSheet(f"color: {color};")
    widget.setWordWrap(wrap)
    return widget


class ArcReactor(QWidget):
    """The turning rings in the middle. They speed up while Jarvis thinks and pulse while it talks."""

    SPEED = {"": 1.0, "Listening": 1.5, "Thinking": 4.5, "Speaking": 2.0}

    def __init__(self):
        super().__init__()
        self.setMinimumSize(240, 240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.state = ""
        self.angle = 0.0
        self.clock = 0.0
        self.levels = [0.0] * 48
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)

    def set_state(self, state):
        self.state = state if state in self.SPEED else ""
        self.update()

    def showEvent(self, event):
        self.timer.start(33)
        super().showEvent(event)

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def _tick(self):
        self.clock += 0.033
        self.angle = (self.angle + 0.033 * 14 * self.SPEED[self.state]) % 360
        if self.state == "Speaking":  # a voice-like ripple around the core
            self.levels = [0.7 * old + 0.3 * random.random() for old in self.levels]
        else:
            self.levels = [0.85 * old for old in self.levels]
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        size = min(self.width(), self.height())
        r = size / 2 - 6
        center = QPointF(self.width() / 2, self.height() / 2)
        color = QColor(STATE_COLORS.get(self.state, C["cyan"]))

        def tinted(alpha):
            c = QColor(color)
            c.setAlphaF(max(0.0, min(1.0, alpha)))
            return c

        def ring(radius, width, alpha, start, span, cap=Qt.PenCapStyle.FlatCap):
            pen = QPen(tinted(alpha), width)
            pen.setCapStyle(cap)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            box = QRectF(center.x() - radius, center.y() - radius, 2 * radius, 2 * radius)
            painter.drawArc(box, int(start * 16), int(span * 16))

        glow = QRadialGradient(center, r)
        glow.setColorAt(0.0, tinted(0.30))
        glow.setColorAt(0.45, tinted(0.08))
        glow.setColorAt(1.0, tinted(0.0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(center, r, r)

        # the scale around the edge
        for i in range(120):
            major = i % 10 == 0
            a = math.radians(i * 3)
            inner = r * (0.92 if major else 0.955)
            painter.setPen(QPen(tinted(0.9 if major else 0.32), 1.6 if major else 1))
            painter.drawLine(QPointF(center.x() + inner * math.cos(a), center.y() + inner * math.sin(a)),
                             QPointF(center.x() + r * math.cos(a), center.y() + r * math.sin(a)))

        # turning segments, an opposite-turning ring, a fixed dotted ring
        for k in range(6):
            ring(r * 0.84, 3, 0.85, self.angle + k * 60, 38)
        for k in range(3):
            ring(r * 0.74, 1.4, 0.55, -self.angle * 1.6 + k * 120, 80)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(tinted(0.45))
        for i in range(60):
            a = math.radians(i * 6)
            painter.drawEllipse(QPointF(center.x() + r * 0.64 * math.cos(a), center.y() + r * 0.64 * math.sin(a)), 1.1, 1.1)

        if self.state == "Listening":  # waves going out
            for k in range(3):
                t = (self.clock * 0.8 + k / 3) % 1
                ring(r * (0.4 + 0.55 * t), 2, 0.7 * (1 - t), 0, 360)
        if any(level > 0.02 for level in self.levels):
            for i, level in enumerate(self.levels):
                a = math.radians(i * 360 / len(self.levels))
                inner, outer = r * 0.47, r * (0.47 + 0.12 * level)
                painter.setPen(QPen(tinted(0.85), 2.2))
                painter.drawLine(QPointF(center.x() + inner * math.cos(a), center.y() + inner * math.sin(a)),
                                 QPointF(center.x() + outer * math.cos(a), center.y() + outer * math.sin(a)))

        # the core
        breathe = 1 + 0.04 * math.sin(self.clock * 2.2)
        core = QRadialGradient(center, r * 0.42)
        core.setColorAt(0.0, QColor(C["ice"]))
        core.setColorAt(0.35, tinted(0.95))
        core.setColorAt(0.7, tinted(0.25))
        core.setColorAt(1.0, tinted(0.0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(core)
        painter.drawEllipse(center, r * 0.42 * breathe, r * 0.42 * breathe)
        ring(r * 0.3, 2, 0.9, 0, 360)

        painter.setPen(QPen(tinted(0.55), 1.4))
        n, half = r * 0.12, r + 4
        for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
            x, y = center.x() + dx * half, center.y() + dy * half
            painter.drawLine(QPointF(x, y), QPointF(x - dx * n, y))
            painter.drawLine(QPointF(x, y), QPointF(x, y - dy * n))
        painter.end()


class Panel(QFrame):
    """A HUD panel: a thin frame with corner marks and a small title."""

    def __init__(self, title):
        super().__init__()
        self.title = label(title.upper(), 8, C["cyan"], QFont.Weight.DemiBold, 2.2)
        self.body = QVBoxLayout()
        self.body.setSpacing(8)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 14)
        layout.setSpacing(10)
        layout.addWidget(self.title)
        layout.addLayout(self.body, 1)

    def paintEvent(self, event):
        painter = QPainter(self)
        box = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(QPen(QColor(C["line"]), 1))
        fill = QColor(C["panel"])
        fill.setAlpha(170)
        painter.setBrush(fill)
        painter.drawRect(box)
        painter.setPen(QPen(QColor(C["cyan"]), 1.6))
        n = 12
        for x, y, dx, dy in ((box.left(), box.top(), 1, 1), (box.right(), box.top(), -1, 1),
                             (box.left(), box.bottom(), 1, -1), (box.right(), box.bottom(), -1, -1)):
            painter.drawLine(QPointF(x, y), QPointF(x + dx * n, y))
            painter.drawLine(QPointF(x, y), QPointF(x, y + dy * n))
        painter.end()


class Row(QFrame):
    """A line in a list that does something when clicked."""

    def __init__(self, on_click=None, tooltip=""):
        super().__init__()
        self.setObjectName("hudRow")
        self.on_click = on_click
        if on_click:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        if tooltip:
            self.setToolTip(tooltip)

    def mousePressEvent(self, event):
        if self.on_click and event.button() == Qt.MouseButton.LeftButton:
            self.on_click()
        super().mousePressEvent(event)


class Meter(QWidget):
    """A reading with a thin bar under it, for the system panel."""

    def __init__(self, name):
        super().__init__()
        self.name = name
        self.value = None
        self.note = ""
        self.setFixedHeight(30)

    def set_value(self, value, note=""):
        self.value, self.note = value, note
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width = self.width()
        painter.setFont(hud_font(8, QFont.Weight.DemiBold, 1.6))
        painter.setPen(QColor(C["muted"]))
        painter.drawText(QRectF(0, 0, width, 16), Qt.AlignmentFlag.AlignLeft, self.name.upper())
        painter.setFont(hud_font(10))
        painter.setPen(QColor(C["ice"]))
        text = "--" if self.value is None else f"{self.value}%" + (f"  {self.note}" if self.note else "")
        painter.drawText(QRectF(0, 0, width, 16), Qt.AlignmentFlag.AlignRight, text)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(C["line"]))
        painter.drawRect(QRectF(0, 22, width, 3))
        if self.value is not None:
            high = self.value >= 85 and self.name != "Battery" or self.name == "Battery" and self.value <= 15
            painter.setBrush(QColor(C["amber"] if high else C["cyan"]))
            painter.drawRect(QRectF(0, 22, width * max(0, min(100, self.value)) / 100, 3))
        painter.end()


class Ticker(QWidget):
    """Headlines running along the bottom. Hover to stop it, click a headline to read it."""

    GAP = 48

    def __init__(self):
        super().__init__()
        self.setFixedHeight(30)
        self.setMouseTracking(True)
        self.items = []  # (section, title, link)
        self.offset = 0.0
        self.spans = []  # (left, right, link) of what is on screen
        self.hovered = False
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)

    def set_stories(self, stories):
        self.items = [(s.section.upper(), s.title, s.link) for s in stories]
        self.offset = 0.0
        self.update()

    def showEvent(self, event):
        self.timer.start(30)
        super().showEvent(event)

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def enterEvent(self, event):
        self.hovered = True
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.hovered = False
        super().leaveEvent(event)

    def _widths(self):
        tag, text = QFontMetrics(hud_font(8, QFont.Weight.DemiBold, 1.6)), QFontMetrics(hud_font(10))
        return [(tag.horizontalAdvance(section), text.horizontalAdvance(title)) for section, title, _ in self.items]

    def _tick(self):
        if self.items and not self.hovered:
            self.offset += 1.5
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(C["line"]), 1))
        painter.drawLine(0, 0, self.width(), 0)
        self.spans = []
        if not self.items:
            painter.setFont(hud_font(10))
            painter.setPen(QColor(C["muted"]))
            painter.drawText(QRectF(12, 0, self.width(), self.height()), Qt.AlignmentFlag.AlignVCenter,
                             "Headlines appear here once the news has loaded.")
            painter.end()
            return
        widths = self._widths()
        total = sum(a + b + 12 + self.GAP for a, b in widths)
        x = -(self.offset % total)
        while x < self.width():
            for (section, title, link), (tag_w, text_w) in zip(self.items, widths):
                if x + tag_w + text_w + 12 > 0 and x < self.width():
                    painter.setFont(hud_font(8, QFont.Weight.DemiBold, 1.6))
                    painter.setPen(QColor(C["cyan"]))
                    painter.drawText(QRectF(x, 0, tag_w + 2, self.height()), Qt.AlignmentFlag.AlignVCenter, section)
                    painter.setFont(hud_font(10))
                    painter.setPen(QColor(C["text"]))
                    painter.drawText(QRectF(x + tag_w + 12, 0, text_w + 2, self.height()),
                                     Qt.AlignmentFlag.AlignVCenter, title)
                    self.spans.append((x, x + tag_w + 12 + text_w, link))
                x += tag_w + 12 + text_w + self.GAP
        painter.end()

    def link_at(self, x):
        return next((link for left, right, link in self.spans if left <= x <= right), None)

    def mousePressEvent(self, event):
        link = self.link_at(event.position().x())
        if link:
            webbrowser.open(link)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        over = self.link_at(event.position().x())
        self.setCursor(Qt.CursorShape.PointingHandCursor if over else Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(event)


def _list_area():
    """A scrolling list for a panel, so long lists never squash the layout."""
    holder = QWidget()
    rows = QVBoxLayout(holder)
    rows.setContentsMargins(0, 0, 4, 0)
    rows.setSpacing(4)
    rows.addStretch()
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    area.setWidget(holder)
    return area, rows


def _clear(rows):
    while rows.count() > 1:  # keep the stretch at the end
        item = rows.takeAt(0)
        if item.widget():
            item.widget().hide()  # gone at once, not only when Qt gets round to deleting it
            item.widget().deleteLater()


class HudWindow(QWidget):
    """The full-screen dashboard."""

    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.messages = []
        self.mail = None
        self.news = None
        self.weather_time = None  # when the weather on screen was fetched
        self.setWindowTitle("Jarvis")
        self.setMinimumSize(1100, 660)
        self.resize(1366, 800)
        self.setStyleSheet(STYLE)

        # top bar
        brand = label("J.A.R.V.I.S.", 15, C["ice"], QFont.Weight.Light, 6)
        self.status = label("ONLINE", 8, C["green"], QFont.Weight.DemiBold, 2.2)
        self.brain = label("", 8.5, C["muted"])
        self.clock = label("", 22, C["ice"], QFont.Weight.Light, 1)
        self.date = label("", 9, C["muted"])
        top = QHBoxLayout()
        top.setSpacing(18)
        top.addWidget(brand)
        top.addWidget(self.status)
        top.addWidget(self.brain)
        top.addStretch()
        when = QVBoxLayout()
        when.setSpacing(0)
        when.addWidget(self.clock, 0, Qt.AlignmentFlag.AlignRight)
        when.addWidget(self.date, 0, Qt.AlignmentFlag.AlignRight)
        top.addLayout(when)

        # left: weather, system, today
        self.weather_panel = Panel("Weather")
        self.temperature = label("--", 34, C["ice"], QFont.Weight.Light)
        self.sky = label("", 10.5, C["text"], wrap=True)
        self.weather_more = label("", 9, C["muted"], wrap=True)
        for widget in (self.temperature, self.sky, self.weather_more):
            self.weather_panel.body.addWidget(widget)
        self.system_panel = Panel("System")
        self.meters = {name: Meter(name) for name in ("Processor", "Memory", "Battery", "Disk")}
        for meter in self.meters.values():
            self.system_panel.body.addWidget(meter)
        self.today_panel = Panel("Today")
        today_area, self.today_rows = _list_area()
        self.today_panel.body.addWidget(today_area)
        left = QVBoxLayout()
        left.setSpacing(14)
        left.addWidget(self.weather_panel)
        left.addWidget(self.system_panel)
        left.addWidget(self.today_panel, 1)

        # middle: the reactor and what is being said
        self.reactor = ArcReactor()
        self.line = label("", 16, C["ice"], QFont.Weight.Light, wrap=True)
        self.line.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.line_area = QScrollArea()
        self.line_area.setWidgetResizable(True)
        self.line_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.line_area.setWidget(self.line)
        self.line_area.setMaximumWidth(760)
        self.line_area.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.line_area.setFixedHeight(QFontMetrics(self.line.font()).lineSpacing() * 4 + 6)  # 4 lines, then it scrolls
        self.you = label("", 10, C["muted"], wrap=True)
        self.you.setAlignment(Qt.AlignmentFlag.AlignCenter)
        middle = QVBoxLayout()
        middle.setSpacing(10)
        middle.addWidget(self.reactor, 1)
        middle.addWidget(self.you)
        centred = QHBoxLayout()
        centred.addStretch()
        centred.addWidget(self.line_area, 100)
        centred.addStretch()
        middle.addLayout(centred)

        # right: inbox and news
        self.mail_panel = Panel("Inbox")
        self.mail_counts = label("Loading your inbox...", 9.5, C["muted"], wrap=True)
        mail_area, self.mail_rows = _list_area()
        self.mail_panel.body.addWidget(self.mail_counts)
        self.mail_panel.body.addWidget(mail_area, 1)
        self.news_panel = Panel("News")
        self.news_topics = label("Loading the news...", 9.5, C["muted"], wrap=True)
        news_area, self.news_rows = _list_area()
        self.news_panel.body.addWidget(self.news_topics)
        self.news_panel.body.addWidget(news_area, 1)
        right = QVBoxLayout()
        right.setSpacing(14)
        right.addWidget(self.mail_panel, 1)
        right.addWidget(self.news_panel, 1)

        columns = QHBoxLayout()
        columns.setSpacing(22)
        left_box, right_box = QWidget(), QWidget()
        left_box.setLayout(left)
        right_box.setLayout(right)
        left_box.setFixedWidth(280)
        right_box.setFixedWidth(390)
        columns.addWidget(left_box)
        columns.addLayout(middle, 1)
        columns.addWidget(right_box)

        # bottom: ticker and command bar
        self.ticker = Ticker()
        self.input = QLineEdit()
        self.input.setObjectName("hudInput")
        self.input.setFont(hud_font(11))
        self.input.setPlaceholderText("Ask Jarvis anything")
        self.input.returnPressed.connect(self._typed)
        self.send_button = QPushButton()
        self.send_button.setObjectName("hudRound")
        self.send_button.setIcon(draw_symbol("send", color=C["text"]))
        self.send_button.setToolTip("Send")
        self.send_button.clicked.connect(self._typed)
        self.talk_button = QPushButton()
        self.talk_button.setObjectName("hudMic")
        self.talk_button.setIcon(draw_symbol("mic", color=C["ice"]))
        self.talk_button.setToolTip(f"Talk ({config.HOTKEY_LABEL})")
        self.talk_button.clicked.connect(controller.talk)
        bar = QHBoxLayout()
        bar.setSpacing(10)
        bar.addWidget(self.input, 1)
        bar.addWidget(self.send_button)
        bar.addWidget(self.talk_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 18, 28, 18)
        layout.setSpacing(16)
        layout.addLayout(top)
        layout.addLayout(columns, 1)
        layout.addWidget(self.ticker)
        layout.addLayout(bar)

        if hasattr(controller, "quit"):  # Cmd+Q on a Mac (the menu bar icon can hide behind the notch)
            QShortcut(QKeySequence(QKeySequence.StandardKey.Quit), self, activated=controller.quit)

        self.timer = QTimer(self)  # clock, system readings and alarms, while on screen
        self.timer.timeout.connect(self._every_second)
        self.flash_timer = QTimer(self)
        self.flash_timer.setSingleShot(True)
        self.flash_timer.timeout.connect(self._show_idle)
        self._readings = 0
        self._show_idle()
        self.update_brain()
        self._every_second()

    # --- things the rest of Jarvis calls --------------------------------------------

    def add(self, who, text):
        self.messages.append((who, text))
        del self.messages[:-80]
        if who == "You":
            self.you.setText(f"You: {text}")
        else:
            self.flash_timer.stop()
            self.line.setText(text)
            self.line_area.verticalScrollBar().setValue(0)

    def set_state(self, busy):
        state = busy if busy in STATES else ""
        self.reactor.set_state(state)
        self.status.setText(STATES[state])
        self.status.setStyleSheet(f"color: {C['green'] if not state else STATE_COLORS[state]};")

    def flash(self, text):
        self.reactor.set_state("")
        self.line.setText(text)
        self.flash_timer.start(4000)

    def update_data(self, data):
        """New weather, inbox or news (any of them) for the panels."""
        if "weather" in data:
            self.set_weather(data["weather"])
        if "mail" in data:
            self.set_mail(data["mail"])
        if "news" in data:
            self.set_news(data["news"])
        for key in ("weather_error", "mail_error", "news_error"):
            if key not in data:
                continue
            if key == "weather_error" and self.weather_time:  # keep showing the last weather it got
                self.weather_more.setText(f"Last updated at {self.weather_time:%H:%M}. {data[key]}")
                continue
            {"weather_error": self.sky, "mail_error": self.mail_counts, "news_error": self.news_topics}[key] \
                .setText(data[key])

    def set_weather(self, weather):
        self.weather_time = datetime.now() if weather else None
        if not weather:
            self.temperature.setText("--")
            self.sky.setText(f"No weather found for {config.HOME_CITY}. Check the city in Settings.")
            self.weather_more.setText("")
            return
        self.temperature.setText(f"{weather['temperature']}\u00b0")
        self.sky.setText(f"{weather['sky'].capitalize()} in {weather['place']}")
        details = []
        if weather.get("high") is not None and weather.get("low") is not None:
            details.append(f"High {weather['high']}\u00b0  Low {weather['low']}\u00b0")
        if weather.get("rain") is not None:
            details.append(f"Rain {weather['rain']}%")
        if weather.get("source") not in (None, "Open-Meteo"):  # the usual service didn't answer
            details.append(f"from {weather['source']}")
        self.weather_more.setText("    ".join(details))

    def set_mail(self, brief):
        self.mail = brief
        _clear(self.mail_rows)
        if not brief.notes:
            self.mail_counts.setText("Inbox clear. No unread email.")
            return
        other = brief.count("update") + brief.count("low")
        counts = [f"{n} {name}" for n, name in ((brief.count("reply"), "to reply"),
                                                (brief.count("important"), "important"), (other, "other")) if n]
        self.mail_counts.setText("    ".join(counts) + ("      analysed by Claude" if brief.by_claude else ""))
        for note in brief.notes[:12]:
            self.mail_rows.insertWidget(self.mail_rows.count() - 1, self._mail_row(note))

    def _mail_row(self, note):
        row = Row(self.controller.open_inbox, f"{analysis.GROUP_NAMES[note.group]}: open the inbox")
        mark = QFrame()
        mark.setFixedWidth(3)
        mark.setStyleSheet(f"background: {GROUP_COLORS[note.group]};")
        sender = label(note.mail.sender, 10, C["ice"], QFont.Weight.DemiBold)
        head = QHBoxLayout()
        head.addWidget(sender, 1)
        if note.due:
            head.addWidget(label(f"due {note.due}", 8.5, C["amber"]))
        text = QVBoxLayout()
        text.setSpacing(1)
        text.addLayout(head)
        text.addWidget(label(note.mail.subject or "(no subject)", 9.5, C["text"], wrap=True))
        if note.summary and note.summary != note.mail.subject:
            text.addWidget(label(note.summary, 9, C["muted"], wrap=True))
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 5, 6, 5)
        layout.setSpacing(10)
        layout.addWidget(mark)
        layout.addLayout(text, 1)
        return row

    def set_news(self, brief):
        self.news = brief
        _clear(self.news_rows)
        self.ticker.set_stories(brief.stories)
        if not brief.stories:
            self.news_topics.setText("No news just now.")
            return
        self.news_topics.setText(("Trending: " + ", ".join(brief.topics)) if brief.topics else "Today's top stories")
        for story in brief.stories[:8]:
            self.news_rows.insertWidget(self.news_rows.count() - 1, self._story_row(story))

    def _story_row(self, story):
        row = Row(lambda: webbrowser.open(story.link) if story.link else None, "Open the article")
        outlets = len(story.sources)
        meta = QHBoxLayout()
        meta.addWidget(label(story.section.upper(), 7.5, C["cyan"], QFont.Weight.DemiBold, 1.6))
        meta.addStretch()
        meta.addWidget(label(f"{outlets} outlet{'' if outlets == 1 else 's'}", 8.5,
                             C["amber"] if outlets > 1 else C["muted"]))
        layout = QVBoxLayout(row)
        layout.setContentsMargins(0, 5, 6, 5)
        layout.setSpacing(2)
        layout.addLayout(meta)
        layout.addWidget(label(story.title, 10, C["ice"], wrap=True))
        return row

    def set_alarms(self, upcoming):
        now = datetime.now()
        soon = [a for a in upcoming if a.due - now <= timedelta(hours=24)][:6]
        shown = tuple((a.id, a.due, a.text) for a in soon)
        if shown == getattr(self, "_alarms_shown", None):
            return  # nothing changed: don't rebuild the list every two seconds
        self._alarms_shown = shown
        _clear(self.today_rows)
        if not soon:
            self.today_rows.insertWidget(0, label('Nothing in the next 24 hours. Say "set an alarm for 7 am".',
                                                  9, C["muted"], wrap=True))
            return
        for alarm in soon:
            row = QHBoxLayout()
            row.addWidget(label(alarm.due.strftime("%H:%M"), 11, C["cyan"], QFont.Weight.Light))
            what = {"alarm": "Alarm", "timer": f"Timer, {alarm.text}"}.get(alarm.kind, alarm.text or "Reminder")
            row.addWidget(label(what, 9.5, C["text"], wrap=True), 1)
            holder = QWidget()
            holder.setLayout(row)
            self.today_rows.insertWidget(self.today_rows.count() - 1, holder)

    def transcript(self):
        return "\n".join(f"{who}: {text}" for who, text in self.messages)

    # --- inside ----------------------------------------------------------------------

    def _typed(self):
        text = self.input.text().strip()
        if text:
            self.input.clear()
            self.controller.run_command(text)

    def _show_idle(self):
        hour = datetime.now().hour
        part = "morning" if hour < 12 else "afternoon" if hour < 18 else "evening"
        sir = f", {config.ADDRESS}" if config.ADDRESS else ""
        self.line.setText(f"Good {part}{sir}. All systems online.\n"
                          f"Say \u201cHey Jarvis\u201d, press {config.HOTKEY_LABEL}, or type below.")

    def update_brain(self):
        brain = f"Brain: {ai.model_name()}" if ai.enabled() else "Brain: offline rules"
        voice = human_voice.label()
        self.brain.setText(f"{brain}    {voice}" if voice else brain)

    def _every_second(self):
        now = datetime.now()
        self.clock.setText(now.strftime("%H:%M"))
        self.date.setText(f"{now:%A} {now.day} {now:%B}")
        self._readings += 1
        if self._readings % 2 == 1:  # every two seconds is plenty
            self.update_brain()  # the human voice may have finished loading
            info = sysinfo.snapshot()
            self.meters["Processor"].set_value(info["cpu"])
            self.meters["Memory"].set_value(info["memory"])
            plugged = {True: "charging", False: "on battery"}.get(info["plugged_in"], "")
            self.meters["Battery"].set_value(info["battery"], plugged)
            self.meters["Disk"].set_value(info["disk"])
            self.set_alarms(self.controller.alarms.all())

    def showEvent(self, event):
        self.timer.start(1000)
        self.update_brain()
        windows.dark_title_bar(int(self.winId()))
        super().showEvent(event)

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(C["void"]))
        grid = QColor(C["cyan"])
        grid.setAlpha(9)
        painter.setPen(QPen(grid, 1))
        for x in range(0, self.width(), 48):
            painter.drawLine(x, 0, x, self.height())
        for y in range(0, self.height(), 48):
            painter.drawLine(0, y, self.width(), y)
        painter.end()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_F11:
            self.showNormal() if self.isFullScreen() else self.showFullScreen()
        elif event.key() == Qt.Key.Key_Escape and self.isFullScreen():
            self.showMaximized()
        else:
            super().keyPressEvent(event)
