"""Jarvis in the system tray (the menu bar on a Mac).

Say "Hey Jarvis" or press the listening key and give a command: mail, music, alarms,
reminders, apps, the web and more. It also checks Gmail in the background.
Everything slow (Gmail, speech, the microphone, the web) runs on one worker
thread, one job at a time, so the windows never freeze.
"""
import math
import os
import re
import signal
import sqlite3
import sys
import threading
import traceback
import webbrowser
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

from google.auth.exceptions import RefreshError, TransportError
from googleapiclient.errors import HttpError
from httplib2 import HttpLib2Error
from PyQt6.QtCore import QLockFile, QObject, QPointF, QRectF, QStandardPaths, Qt, QThread, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import (QColor, QFont, QGuiApplication, QIcon, QKeySequence, QPainter, QPen, QPixmap,
                         QRadialGradient, QShortcut)
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QSystemTrayIcon,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from . import (ai, alarms, analysis, commands, compose, config, dictation, hotkey, hud, human_voice, importance, macos,
               settings, sound, speech, startup, wakeword, web, windows)
from .auth import AuthError
from .gmail_client import GmailClient
from .icons import draw_symbol
from .models import Draft
from .notifier import notify
from .store import Store
from .watcher import check_once

APP_NAME = "Jarvis"
GMAIL_URL = "https://mail.google.com/"
VOICE_SAMPLE = "This is how I will sound."
IDLE_TEXT = f'Say "Hey Jarvis" or press {config.HOTKEY_LABEL}'


def draw_icon(size=64, alert=False):
    """A glowing ring on a dark tile, with a red dot when important mail came in."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(size / 64, size / 64)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#0b3d91"))
    painter.drawRoundedRect(2, 2, 60, 60, 14, 14)
    painter.setPen(QPen(QColor("#4fc3f7"), 5))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(13, 13, 38, 38)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#4fc3f7"))
    painter.drawEllipse(25, 25, 14, 14)
    if alert:
        painter.setBrush(QColor("#ea4335"))
        painter.drawEllipse(40, 0, 24, 24)
    painter.end()
    return pixmap


def draw_menu_bar_icon(alert=False):
    """The Mac menu bar's version: black on clear, so macOS can colour it for light and dark menu bars."""
    pixmap = QPixmap(44, 44)  # 22 points at 2x
    pixmap.setDevicePixelRatio(2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor("black"), 2))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(QPointF(11, 11), 7, 7)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("black"))
    painter.drawEllipse(QPointF(11, 11), 2.6, 2.6)
    if alert:
        painter.drawEllipse(QPointF(18.5, 3.5), 3.5, 3.5)
    painter.end()
    icon = QIcon(pixmap)
    icon.setIsMask(True)  # a template image
    return icon


def tray_icon(alert=False):
    return draw_menu_bar_icon(alert) if macos.available() else QIcon(draw_icon(alert=alert))


def save_icon(path):
    """Write the icon as an .ico file (used for the desktop shortcut). Returns the path, or None."""
    qt_app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])  # needed to draw
    path.parent.mkdir(parents=True, exist_ok=True)
    saved = draw_icon(256).save(str(path), "ICO")
    del qt_app
    return path if saved else None


# --- look and feel -------------------------------------------------------------------

COLORS = {
    "bg": "#0d1117", "panel": "#161b22", "raised": "#21262d", "border": "#30363d", "text": "#e6edf3",
    "muted": "#8b949e", "accent": "#4fc3f7", "blue": "#1f6feb", "blue_hover": "#388bfd", "red": "#da3633",
    "thinking": "#b388ff", "speaking": "#69f0ae",
}

STYLE = """
QWidget { background: %(bg)s; color: %(text)s; }
QToolTip { background: %(panel)s; color: %(text)s; border: 1px solid %(border)s; padding: 4px; }
QLabel { background: transparent; }
QLabel#title { font-size: 16pt; font-weight: 600; }
QLabel#subtitle, QLabel#muted { color: %(muted)s; }
QLabel#bigtime { font-size: 40pt; font-weight: 300; }
QLabel#kind { color: %(accent)s; font-weight: 700; letter-spacing: 2px; }
QLabel#message { font-size: 14pt; }
QPushButton { background: %(raised)s; border: 1px solid %(border)s; border-radius: 8px; padding: 7px 16px; }
QPushButton:hover { background: %(border)s; }
QPushButton:pressed { background: %(panel)s; }
QPushButton:disabled { color: #484f58; border-color: %(raised)s; }
QPushButton#primary { background: %(blue)s; border-color: %(blue)s; color: white; font-weight: 600; }
QPushButton#primary:hover { background: %(blue_hover)s; border-color: %(blue_hover)s; }
QPushButton#danger { background: transparent; border-color: %(red)s; color: #ff7b72; }
QPushButton#danger:hover { background: %(red)s; color: white; }
QPushButton#chip { background: %(panel)s; border-radius: 14px; padding: 5px 12px; color: %(muted)s; text-align: left; }
QPushButton#chip:hover { color: %(text)s; border-color: %(accent)s; }
QPushButton#round { border-radius: 19px; padding: 0; min-width: 38px; max-width: 38px; min-height: 38px; max-height: 38px; }
QPushButton#round[accent="true"] { background: %(blue)s; border-color: %(blue)s; }
QPushButton#round[accent="true"]:hover { background: %(blue_hover)s; }
QPushButton#flat { background: transparent; border: none; color: %(muted)s; font-size: 14pt; padding: 0 6px; }
QPushButton#flat:hover { color: %(text)s; }
QLineEdit, QPlainTextEdit, QSpinBox, QComboBox {
    background: %(panel)s; border: 1px solid %(border)s; border-radius: 8px; padding: 6px 9px;
    selection-background-color: %(blue)s;
}
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QComboBox:focus { border-color: %(accent)s; }
QLineEdit#command { border-radius: 18px; padding: 0 14px; min-height: 36px; }
QComboBox QAbstractItemView { background: %(panel)s; border: 1px solid %(border)s; selection-background-color: %(blue)s; }
QTableWidget { outline: 0; background: %(panel)s; alternate-background-color: #1a2029; border: 1px solid %(border)s;
               border-radius: 8px; gridline-color: transparent; selection-background-color: %(blue)s; }
QTableWidget::item { padding: 4px; }
QHeaderView::section { background: %(panel)s; color: %(muted)s; border: none; border-bottom: 1px solid %(border)s;
                       padding: 6px; font-weight: 600; }
QTabWidget::pane { border: 1px solid %(border)s; border-radius: 8px; top: -1px; }
QTabBar::tab { background: transparent; color: %(muted)s; padding: 8px 18px; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: %(text)s; border-bottom: 2px solid %(accent)s; }
QMenu { background: %(panel)s; border: 1px solid %(border)s; border-radius: 8px; padding: 6px; }
QMenu::item { padding: 7px 26px 7px 14px; border-radius: 6px; }
QMenu::item:selected { background: %(blue)s; color: white; }
QMenu::item:disabled { color: %(muted)s; }
QMenu::separator { height: 1px; background: %(border)s; margin: 5px 8px; }
QScrollArea { border: none; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 2px; }
QScrollBar::handle:vertical { background: %(border)s; border-radius: 4px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical, QScrollBar::add-page:vertical,
QScrollBar::sub-page:vertical { height: 0; background: none; }
QSlider::groove:horizontal { height: 4px; background: %(border)s; border-radius: 2px; }
QSlider::sub-page:horizontal { background: %(accent)s; border-radius: 2px; }
QSlider::handle:horizontal { background: white; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }
QFrame#panel { background: %(bg)s; border: 1px solid %(border)s; border-radius: 16px; }
QWidget#chat, QWidget#chips { background: transparent; }
QLabel#you { background: %(blue)s; color: white; border-radius: 12px; }
QLabel#jarvis { background: %(raised)s; border-radius: 12px; }
""" % COLORS


FONT_SCALE = 4 / 3 if sys.platform == "darwin" else 1  # a Mac point is 1 pixel, a Windows one 1.33


def scaled_style(style, factor=FONT_SCALE):
    """The style sheet with its point sizes scaled, so text is the same size on a Mac as on Windows."""
    if factor == 1:
        return style
    return re.sub(r"(\d+(?:\.\d+)?)pt\b", lambda m: f"{float(m.group(1)) * factor:g}pt", style)


def apply_theme(qt_app):
    """The dark look used by every window."""
    qt_app.setStyle("Fusion")
    font = QFont()  # set here, not in the style sheet, so windows like the HUD can use their own sizes
    if sys.platform == "darwin":
        font = QFont(qt_app.font())  # the Mac's own system font
        font.setPointSizeF(10 * FONT_SCALE)
        qt_app.styleHints().setColorScheme(Qt.ColorScheme.Dark)  # dark title bars and dialogs
    else:
        font.setFamilies(["Segoe UI", "Inter", "Helvetica Neue", "Arial"])
        font.setPointSize(10)
    qt_app.setFont(font)
    qt_app.setStyleSheet(scaled_style(STYLE))


class Orb(QWidget):
    """The circle in the Jarvis window. It moves while Jarvis listens, thinks or talks."""

    COLORS = {"Listening": COLORS["accent"], "Thinking": COLORS["thinking"], "Speaking": COLORS["speaking"]}

    def __init__(self, size=52):
        super().__init__()
        self.setFixedSize(size, size)
        self.state = ""
        self.phase = 0.0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)

    def set_state(self, state):
        self.state = state if state in self.COLORS else ""
        if self.state and not self.timer.isActive():
            self.timer.start(33)
        elif not self.state:
            self.timer.stop()
            self.phase = 0.0
        self.update()

    def _tick(self):
        self.phase += 0.033
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        size = self.width()
        center = QPointF(size / 2, size / 2)
        radius = size * 0.26
        color = QColor(self.COLORS.get(self.state, COLORS["accent"]))

        if self.state == "Listening":  # rings going outwards
            for k in range(3):
                t = (self.phase * 0.9 + k / 3) % 1
                ring = QColor(color)
                ring.setAlphaF(0.6 * (1 - t))
                painter.setPen(QPen(ring, 2))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                r = radius + t * (size / 2 - radius - 2)
                painter.drawEllipse(center, r, r)

        glow = QRadialGradient(center, size / 2)
        soft = QColor(color)
        soft.setAlphaF(0.35 if self.state else 0.18)
        clear = QColor(color)
        clear.setAlphaF(0.0)
        glow.setColorAt(0.0, soft)
        glow.setColorAt(1.0, clear)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(center, size / 2, size / 2)

        core = radius
        if self.state == "Speaking":  # the circle breathes with the voice
            core = radius * (1 + 0.14 * abs(math.sin(self.phase * 7)))
        painter.setPen(QPen(color, 3))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(center, core, core)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(center, core * 0.38, core * 0.38)

        if self.state == "Thinking":  # an arc going round
            pen = QPen(color, 3)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            r = radius + 7
            painter.drawArc(QRectF(center.x() - r, center.y() - r, 2 * r, 2 * r),
                            int(-self.phase * 400 * 16) % (360 * 16), 100 * 16)
        painter.end()


class Requests(QObject):
    """Jobs the windows ask the worker to do. Signals carry them safely across threads."""

    check = pyqtSignal()
    inbox = pyqtSignal()
    read_important = pyqtSignal()
    read_mail = pyqtSignal(object)
    reply = pyqtSignal(object)
    send = pyqtSignal(object)
    dictate = pyqtSignal()
    voices = pyqtSignal()
    test_voice = pyqtSignal(str, int)
    test_human_voice = pyqtSignal(str, str)  # the voice sample ("" for Chatterbox's own voice), the effect
    record_voice = pyqtSignal()  # record the user's voice for the human voice to sound like
    command = pyqtSignal(str)  # a typed command for Jarvis
    listen_command = pyqtSignal()  # record a spoken one (when "Hey Jarvis" isn't running)
    recognize = pyqtSignal(object)  # a command recorded after "Hey Jarvis", as audio bytes
    say = pyqtSignal(str)
    dashboard = pyqtSignal(bool)  # fresh weather and news for the HUD (and the inbox, if True)


class Worker(QObject):
    """Does the slow work on its own thread, one job at a time."""

    busy = pyqtSignal(str)  # what it is doing; "" when it's done
    failed = pyqtSignal(str)
    checked = pyqtSignal(int, list)  # number of new mails, the important ones among them
    inbox_ready = pyqtSignal(list)
    reply_ready = pyqtSignal(object)  # a Draft
    sent = pyqtSignal(object)  # the Draft that went out
    dictated = pyqtSignal(str)
    voices_ready = pyqtSignal(list, str)
    heard = pyqtSignal(str)  # what was said to Jarvis
    replied = pyqtSignal(str, object)  # the command and Jarvis's Reply
    audio_busy = pyqtSignal(bool)  # Jarvis is talking or using the microphone
    dashboard_ready = pyqtSignal(object)  # {"weather", "news", "mail", or "..._error"} for the HUD
    voice_recorded = pyqtSignal(str, str)  # the recording's path, or "" and what went wrong

    def __init__(self, make_client=GmailClient, notify_fn=notify, speak_fn=None, listen_fn=None,
                 alarm_store=None, assistant=None, recognize_fn=None):
        super().__init__()
        self._make_client = make_client
        self._notify = notify_fn
        self._speak = speak_fn or speech.speak
        self._listen = listen_fn or dictation.listen
        self._recognize = recognize_fn or dictation.recognize
        self.ringing = lambda: False  # set by the tray app: is an alarm going off?
        self.hud_open = lambda: False  # set by the tray app: is the HUD on screen (so it wants the inbox)?
        self._alarm_store = alarm_store or alarms.AlarmStore(config.ALARMS_FILE)
        self._assistant_instance = assistant
        self._client = None
        self._store = None
        self._baseline = None
        self.cancelled = threading.Event()  # set from the window to cut the current job short

    def _gmail(self):
        if self._client is None:
            self._client = self._make_client()  # the first time this may open the browser to log in
        return self._client

    def _seen(self):
        if self._store is None:
            self._store = Store(config.DB_FILE)  # made here so it belongs to this thread
        return self._store

    def _assistant(self):
        if self._assistant_instance is None:
            self._assistant_instance = commands.Assistant(
                gmail=self._gmail, speak=self._say, alarm_store=self._alarm_store,
                listen=lambda **kwargs: self._hear(on_ready=sound.listening, **kwargs),
                ringing=lambda: self.ringing(),
            )
        return self._assistant_instance

    def _say(self, text, **kwargs):
        if self.cancelled.is_set():
            return False
        self.audio_busy.emit(True)
        try:
            return self._speak(text, **kwargs)
        finally:
            self.audio_busy.emit(False)

    def _hear(self, **kwargs):
        """Use the microphone (the "Hey Jarvis" listener lets go of it meanwhile)."""
        self.audio_busy.emit(True)
        try:
            return self._listen(**kwargs)
        finally:
            self.audio_busy.emit(False)

    def _run(self, label, job):
        self.cancelled.clear()
        self.busy.emit(label)
        try:
            job()
        except RefreshError:
            self._client = None
            self.failed.emit("The Gmail login expired. The next action opens the browser to log in again.")
        except AuthError as exc:
            self._client = None
            self.failed.emit(f"Login problem: {exc}")
        except HttpError as exc:
            self.failed.emit(f"Gmail API error: {exc}")
        except (OSError, HttpLib2Error, TransportError) as exc:
            self.failed.emit(f"Connection problem: {exc}")
        except (dictation.DictationUnavailable, dictation.DictationError) as exc:
            self.failed.emit(f"Dictation: {exc}")
        except speech.SpeechError as exc:
            self.failed.emit(f"Speech: {exc}")
        except sqlite3.Error as exc:
            self.failed.emit(f"Database problem: {exc}")
        except Exception as exc:  # keep the app running, and leave the details in the log
            traceback.print_exc()
            self.failed.emit(f"Something went wrong: {exc}")
        finally:
            self.busy.emit("")

    @pyqtSlot()
    def check(self):
        def job():
            store = self._seen()
            if self._baseline is None:
                self._baseline = store.is_empty()  # first run: don't announce old mail
            speak_fn = self._say if config.READ_NEW_ALOUD else None
            mails, important = check_once(
                self._gmail(), store, announce=not self._baseline,
                notify_fn=self._notify, speak_fn=speak_fn,
            )
            self._baseline = False
            self.checked.emit(len(mails), important)
            if self.hud_open():
                self.dashboard_ready.emit({"mail": analysis.brief_mail(self._gmail().unread())})

        self._run("Checking for new mail", job)

    @pyqtSlot(bool)
    def refresh_dashboard(self, with_mail):
        def job():
            data = {}
            try:
                data["weather"] = web.weather_data(config.HOME_CITY)
            except web.WebError:
                data["weather_error"] = "The weather service didn't answer. Jarvis will try again soon."
            try:
                data["news"] = analysis.brief_news(web.news_items())
            except web.WebError:
                data["news_error"] = "The news feeds didn't answer. Jarvis will try again soon."
            if with_mail:
                try:
                    data["mail"] = analysis.brief_mail(self._gmail().unread())
                except Exception as exc:  # not signed in, no internet, ...
                    data["mail_error"] = f"Couldn't load the inbox: {exc}"
            self.dashboard_ready.emit(data)

        self._run("Updating the HUD", job)

    @pyqtSlot()
    def load_inbox(self):
        self._run("Loading the inbox", lambda: self.inbox_ready.emit(self._gmail().unread()))

    @pyqtSlot()
    def read_important(self):
        def job():
            client = self._gmail()
            important = [m for m in client.unread() if importance.is_important(m)]
            self._say(speech.summary(len(important)))
            for m in important:
                self._say(speech.mail_text(m, client.body(m.id) or m.snippet))

        self._run("Reading important mail aloud", job)

    @pyqtSlot(object)
    def read_mail(self, mail):
        def job():
            self._say(speech.mail_text(mail, self._gmail().body(mail.id) or mail.snippet))

        self._run("Reading aloud", job)

    @pyqtSlot(object)
    def prepare_reply(self, mail):
        self._run("Preparing the reply", lambda: self.reply_ready.emit(self._gmail().reply_details(mail.id)))

    @pyqtSlot(object)
    def send(self, draft):
        def job():
            self._gmail().send(draft)
            self.sent.emit(draft)

        self._run(f"Sending to {draft.to}", job)

    @pyqtSlot()
    def dictate(self):
        self._run("Listening... speak now", lambda: self.dictated.emit(self._hear(on_ready=sound.listening)))

    @pyqtSlot()
    def load_voices(self):
        def job():
            installed, selected = speech.voices()
            self.voices_ready.emit(installed, selected)

        self._run("Looking up the voices", job)

    @pyqtSlot(str, int)
    def test_voice(self, voice, rate):
        self._run("Testing the voice", lambda: self._say(VOICE_SAMPLE, voice=voice, rate=rate, engine="mac"))

    @pyqtSlot(str, str)
    def test_human_voice(self, sample, effect=""):
        def job():
            human_voice.ENGINE.start()
            self.busy.emit("Loading the human voice (the first time takes a little while)")
            if not human_voice.ENGINE.wait_ready(120):
                self.failed.emit(f"The human voice didn't start: {human_voice.ENGINE.error or 'it took too long'}.")
                return
            self.busy.emit("Testing the voice")
            self._say(VOICE_SAMPLE, engine="chatterbox", sample=sample, effect=effect)

        self._run("Loading the human voice", job)

    @pyqtSlot()
    def record_voice(self):
        def job():
            self.audio_busy.emit(True)  # "Hey Jarvis" lets go of the microphone
            try:
                sound.listening()
                path = human_voice.record_sample()
            except human_voice.VoiceError as exc:
                self.voice_recorded.emit("", str(exc))
                return
            finally:
                self.audio_busy.emit(False)
            self.voice_recorded.emit(str(path), "")

        self._run("Recording your voice: read the text aloud", job)

    @pyqtSlot(str)
    def command(self, text):
        self._run("Thinking", lambda: self._answer(text, voice=False))

    @pyqtSlot()
    def listen_command(self):
        def job():
            try:
                guesses = self._hear(timeout=6, phrase_limit=15, alternatives=True, on_ready=sound.listening, pause=0.8)
            except dictation.DictationError as exc:  # silence, mumbling, or no internet
                self._answer_with("", commands.Reply(f"Sorry, {exc}."))
                return
            self.busy.emit("Thinking")
            self._answer_spoken([guesses] if isinstance(guesses, str) else list(guesses))

        self._run("Listening", job)

    @pyqtSlot(object)
    def recognize(self, raw):
        def job():
            try:
                guesses = self._recognize(raw)
            except dictation.DictationError as exc:
                self._answer_with("", commands.Reply(f"Sorry, {exc}."))
                return
            self._answer_spoken(list(guesses))

        self._run("Thinking", job)

    def _answer_spoken(self, guesses):
        text = commands.best(guesses)  # the guess that is a command Jarvis knows
        self.heard.emit(text)
        self._answer(text, voice=True)

    @pyqtSlot(str)
    def say(self, text):
        self._run("Speaking", lambda: self._say(text))

    def _answer(self, text, voice):
        self._answer_with(text, self._assistant().handle(text, voice=voice))

    def _answer_with(self, text, reply):
        self.replied.emit(text, reply)
        if reply.say:
            self.busy.emit("Speaking")
            self._say(reply.say)
        for more in reply.more:
            self._say(more)


class InboxWindow(QWidget):
    """Unread mail with its rating, and buttons for what to do with it."""

    COLUMNS = ["", "From", "Subject", "Received", "Why"]

    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.mails = []
        self.summary = ""
        self.setWindowTitle(f"{APP_NAME} - unread mail")
        self.setWindowIcon(QIcon(draw_icon()))
        self.resize(900, 460)

        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        for column, mode in enumerate([
            QHeaderView.ResizeMode.ResizeToContents,
            QHeaderView.ResizeMode.ResizeToContents,
            QHeaderView.ResizeMode.Stretch,
            QHeaderView.ResizeMode.ResizeToContents,
            QHeaderView.ResizeMode.ResizeToContents,
        ]):
            header.setSectionResizeMode(column, mode)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self.table.itemDoubleClicked.connect(lambda _item: self.read_selected())

        self.refresh_button = QPushButton("Refresh")
        self.read_button = QPushButton("Read aloud")
        self.reply_button = QPushButton("Reply...")
        new_button = QPushButton("New mail...")
        gmail_button = QPushButton("Open Gmail")
        self.refresh_button.clicked.connect(lambda: controller.requests.inbox.emit())
        self.read_button.clicked.connect(self.read_selected)
        self.reply_button.clicked.connect(self.reply_selected)
        new_button.clicked.connect(lambda: controller.open_compose(Draft(to="", subject="")))
        gmail_button.clicked.connect(lambda: webbrowser.open(GMAIL_URL))
        self.status = QLabel("")
        self.status.setObjectName("muted")
        new_button.setObjectName("primary")
        title = QLabel("Unread mail")
        title.setObjectName("title")

        buttons = QHBoxLayout()
        for button in (self.refresh_button, self.read_button, self.reply_button, new_button):
            buttons.addWidget(button)
        buttons.addStretch()
        buttons.addWidget(gmail_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)
        layout.addWidget(title)
        layout.addWidget(self.status)
        layout.addWidget(self.table)
        layout.addLayout(buttons)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self._update_buttons()

    def show_mails(self, mails):
        self.mails = list(mails)
        self.table.clearSelection()
        self.table.setRowCount(len(self.mails))
        for row, m in enumerate(self.mails):
            points, reasons = importance.score(m)
            when = m.received.strftime("%d %b %H:%M") if m.received else ""
            mark = "!" if points >= config.IMPORTANT_THRESHOLD else ""
            for column, text in enumerate([mark, m.sender, m.subject, when, ", ".join(reasons)]):
                self.table.setItem(row, column, QTableWidgetItem(text))
        self.summary = f"{len(self.mails)} unread" if self.mails else "No unread mail in the inbox."
        self.status.setText(self.summary)
        self._update_buttons()

    def set_busy(self, text):
        self.status.setText(text or self.summary)

    def selected(self):
        rows = self.table.selectionModel().selectedRows()
        return self.mails[rows[0].row()] if rows else None

    def _update_buttons(self):
        chosen = self.selected() is not None
        self.read_button.setEnabled(chosen)
        self.reply_button.setEnabled(chosen)

    def read_selected(self):
        mail = self.selected()
        if mail:
            self.controller.requests.read_mail.emit(mail)

    def reply_selected(self):
        mail = self.selected()
        if mail:
            self.controller.requests.reply.emit(mail)


class ComposeDialog(QDialog):
    """Write a mail by typing or dictating. It is only sent after you confirm."""

    def __init__(self, draft, requests, parent=None):
        super().__init__(parent)
        self.draft = draft
        self.requests = requests
        self.waiting = False
        self.setWindowTitle(f"{APP_NAME} - {'reply' if draft.thread_id else 'new mail'}")
        self.setWindowIcon(QIcon(draw_icon()))
        self.resize(560, 420)

        self.to = QLineEdit(draft.to)
        self.subject = QLineEdit(draft.subject)
        self.body = QPlainTextEdit(draft.body)
        self.body.setPlaceholderText("Type the message here, or press Dictate and speak.")
        self.dictate_button = QPushButton("Dictate")
        self.send_button = QPushButton("Send...")
        self.send_button.setObjectName("primary")
        cancel_button = QPushButton("Cancel")
        self.status = QLabel("")
        self.dictate_button.clicked.connect(self.start_dictation)
        self.send_button.clicked.connect(self.try_send)
        cancel_button.clicked.connect(self.reject)

        form = QFormLayout()
        form.addRow("To:", self.to)
        form.addRow("Subject:", self.subject)
        buttons = QHBoxLayout()
        buttons.addWidget(self.dictate_button)
        buttons.addStretch()
        buttons.addWidget(cancel_button)
        buttons.addWidget(self.send_button)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.body)
        layout.addWidget(self.status)
        layout.addLayout(buttons)
        (self.body if draft.to else self.to).setFocus()

    def _wait(self, text):
        self.waiting = True
        self.dictate_button.setEnabled(False)
        self.send_button.setEnabled(False)
        self.status.setText(text)

    def _done(self, text=""):
        self.waiting = False
        self.dictate_button.setEnabled(True)
        self.send_button.setEnabled(True)
        self.status.setText(text)

    def start_dictation(self):
        self._wait("Listening... speak now.")
        self.requests.dictate.emit()

    def add_dictated(self, text):
        current = self.body.toPlainText().rstrip()
        self.body.setPlainText(f"{current} {text}" if current else text)
        self._done("Check the text, then send it or dictate more.")

    def job_failed(self, message):
        if self.waiting:
            self._done(message)

    def current_draft(self):
        return replace(
            self.draft,
            to=self.to.text().strip(),
            subject=" ".join(self.subject.text().split()) or "(no subject)",
            body=self.body.toPlainText().strip(),
        )

    def try_send(self):
        draft = self.current_draft()
        if not compose.valid_address(draft.to):
            QMessageBox.warning(self, APP_NAME, f"'{draft.to}' does not look like an email address.")
            return
        if not draft.body:
            QMessageBox.warning(self, APP_NAME, "The message is empty.")
            return
        answer = QMessageBox.question(self, APP_NAME, f"Send this mail to {draft.to}?\n\nSubject: {draft.subject}")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._wait("Sending...")
        self.requests.send.emit(draft)

    def was_sent(self):
        self.waiting = False
        self.accept()


class JarvisWindow(QWidget):
    """The Jarvis panel: what you said, what Jarvis answered, and a box to type in."""

    STATES = {"Listening": "Listening...", "Thinking": "Thinking...", "Speaking": "Speaking..."}
    SUGGESTIONS = ["What's the weather?", "Play some music", "Set a timer for 10 minutes",
                   "What's in the news?", "Check my email", "What can you do?"]
    MAX_MESSAGES = 80

    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.messages = []
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(QIcon(draw_icon()))
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)  # don't steal focus from a game or a document
        if macos.available():
            # A Mac hides tool windows while their app isn't the active one, and Jarvis, in the
            # menu bar, almost never is: keep this one showing, like Siri's.
            self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow)
        self.resize(430, 580)

        panel = QFrame()
        panel.setObjectName("panel")
        shadow = QGraphicsDropShadowEffect(panel)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 170))
        panel.setGraphicsEffect(shadow)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 14)
        outer.addWidget(panel)

        self.orb = Orb()
        title = QLabel(APP_NAME)
        title.setObjectName("title")
        self.state = QLabel(IDLE_TEXT)
        self.state.setObjectName("subtitle")
        hide_button = QPushButton("\u00d7")
        hide_button.setObjectName("flat")
        hide_button.setToolTip("Hide (Jarvis keeps running in the tray)")
        hide_button.clicked.connect(self.hide)
        names = QVBoxLayout()
        names.setSpacing(0)
        names.addWidget(title)
        names.addWidget(self.state)
        header = QHBoxLayout()
        header.setSpacing(12)
        header.addWidget(self.orb)
        header.addLayout(names, 1)
        header.addWidget(hide_button, 0, Qt.AlignmentFlag.AlignTop)

        self.hint = QLabel("Try saying or typing:")
        self.hint.setObjectName("muted")
        self.chips = QWidget()
        self.chips.setObjectName("chips")
        grid = QGridLayout(self.chips)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(8)
        for i, text in enumerate(self.SUGGESTIONS):
            chip = QPushButton(text)
            chip.setObjectName("chip")
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.clicked.connect(lambda _checked=False, command=text: controller.run_command(command))
            grid.addWidget(chip, i // 2, i % 2)

        self.chat = QWidget()
        self.chat.setObjectName("chat")
        self.chat_layout = QVBoxLayout(self.chat)
        self.chat_layout.setContentsMargins(0, 0, 4, 0)
        self.chat_layout.setSpacing(8)
        self.chat_layout.addStretch()  # messages sit at the bottom, like a chat app
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setWidget(self.chat)

        self.input = QLineEdit()
        self.input.setObjectName("command")
        self.input.setPlaceholderText("Type a command...")
        self.input.returnPressed.connect(self._typed)
        self.send_button = QPushButton()
        self.send_button.setObjectName("round")
        self.send_button.setIcon(draw_symbol("send", color=COLORS["muted"]))
        self.send_button.setToolTip("Send")
        self.send_button.clicked.connect(self._typed)
        self.talk_button = QPushButton()
        self.talk_button.setObjectName("round")
        self.talk_button.setProperty("accent", True)
        self.talk_button.setIcon(draw_symbol("mic"))
        self.talk_button.setToolTip(f"Talk ({config.HOTKEY_LABEL})")
        self.talk_button.clicked.connect(controller.talk)
        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.addWidget(self.input, 1)
        bar.addWidget(self.send_button)
        bar.addWidget(self.talk_button)

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(18, 16, 18, 18)
        layout.setSpacing(12)
        layout.addLayout(header)
        layout.addWidget(self.hint)
        layout.addWidget(self.chips)
        layout.addWidget(self.scroll, 1)
        layout.addLayout(bar)

        self.flash_timer = QTimer(self)
        self.flash_timer.setSingleShot(True)
        self.flash_timer.timeout.connect(lambda: self.set_state(""))
        QShortcut(QKeySequence(QKeySequence.StandardKey.Quit), self, activated=controller.quit)  # Cmd+Q on a Mac

    def _typed(self):
        text = self.input.text().strip()
        if text:
            self.input.clear()
            self.controller.run_command(text)

    def add(self, who, text):
        self.messages.append((who, text))
        self.hint.hide()
        self.chips.hide()
        bubble = QLabel(text)
        bubble.setObjectName("you" if who == "You" else "jarvis")
        bubble.setWordWrap(True)
        bubble.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        bubble.setContentsMargins(12, 8, 12, 8)
        bubble.ensurePolished()
        # as wide as the text needs, up to a limit; a fixed width lets Qt work out the height of wrapped text
        natural = max(bubble.fontMetrics().horizontalAdvance(line) for line in text.splitlines() or [""])
        bubble.setFixedWidth(min(natural + 30, 300))
        row = QHBoxLayout()
        if who == "You":
            row.addStretch()
        row.addWidget(bubble)
        if who != "You":
            row.addStretch()
        self.chat_layout.addLayout(row)
        while len(self.messages) > self.MAX_MESSAGES:  # keep the window light
            self.messages.pop(0)
            old = self.chat_layout.takeAt(1)
            while old.layout().count():
                widget = old.layout().takeAt(0).widget()
                if widget:
                    widget.deleteLater()
        QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(self.scroll.verticalScrollBar().maximum()))

    def transcript(self):
        return "\n".join(f"{who}: {text}" for who, text in self.messages)

    def set_state(self, busy):
        state = busy if busy in self.STATES else ""
        self.orb.set_state(state)
        self.state.setText(self.STATES[state] if state else IDLE_TEXT)

    def flash(self, text):
        """Show a short note under the name for a few seconds."""
        self.orb.set_state("")
        self.state.setText(text)
        self.flash_timer.start(3500)

    def pop_up(self):
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is not None and not self.isVisible():
            area = screen.availableGeometry()
            top = area.top() + 8 if macos.available() else area.bottom() - self.height() - 8  # near the menu bar
            self.move(area.right() - self.width() - 8, top)
        self.show()

    def mousePressEvent(self, event):
        # drag the window by its top part (it has no title bar)
        if event.button() == Qt.MouseButton.LeftButton and event.position().y() < 96 and self.windowHandle():
            self.windowHandle().startSystemMove()
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(event)


class AlarmDialog(QDialog):
    """A ringing alarm, timer or reminder."""

    KINDS = {"alarm": "ALARM", "timer": "TIMER", "reminder": "REMINDER"}

    def __init__(self, alarm, text, controller):
        super().__init__()
        self.alarm = alarm
        self.controller = controller
        self.setWindowTitle(f"{APP_NAME} - {alarm.kind}")
        self.setWindowIcon(QIcon(draw_icon()))
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
        kind = QLabel(self.KINDS.get(alarm.kind, alarm.kind.upper()))
        kind.setObjectName("kind")
        clock_label = QLabel(alarms.clock(datetime.now()))
        clock_label.setObjectName("bigtime")
        message = QLabel(text)
        message.setObjectName("message")
        message.setWordWrap(True)
        self.snooze_button = QPushButton("Snooze 5 min")
        self.stop_button = QPushButton("Stop")
        self.stop_button.setObjectName("primary")
        self.stop_button.setDefault(True)
        for button in (self.snooze_button, self.stop_button):
            button.setMinimumHeight(40)
        self.stop_button.clicked.connect(self.accept)
        self.snooze_button.clicked.connect(self.snooze)
        buttons = QHBoxLayout()
        buttons.addWidget(self.snooze_button, 1)
        buttons.addWidget(self.stop_button, 2)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(8)
        layout.addWidget(kind)
        layout.addWidget(clock_label)
        layout.addWidget(message)
        layout.addSpacing(12)
        layout.addLayout(buttons)
        self.resize(440, 280)

    def snooze(self):
        self.controller.alarms.add(self.alarm.kind, datetime.now() + timedelta(minutes=5), self.alarm.text)
        self.accept()

    def done(self, result):
        sound.stop()
        if self in self.controller.alarm_dialogs:
            self.controller.alarm_dialogs.remove(self)
        super().done(result)


class AlarmsWindow(QWidget):
    """Every alarm, timer and reminder still to come."""

    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.items = []
        self.setWindowTitle(f"{APP_NAME} - alarms and reminders")
        self.setWindowIcon(QIcon(draw_icon()))
        self.resize(560, 320)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["When", "What"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.delete_button = QPushButton("Delete")
        self.delete_all_button = QPushButton("Delete all")
        self.delete_all_button.setObjectName("danger")
        title = QLabel("Alarms and reminders")
        title.setObjectName("title")
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.delete_button.clicked.connect(self.delete_selected)
        self.delete_all_button.clicked.connect(self.delete_all)
        hint = QLabel('Add them by voice, e.g. "set a timer for 10 minutes" or "remind me to call mum at 6 pm".')
        hint.setWordWrap(True)
        hint.setObjectName("muted")
        buttons = QHBoxLayout()
        buttons.addWidget(self.delete_button)
        buttons.addWidget(self.delete_all_button)
        buttons.addStretch()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)
        layout.addWidget(title)
        layout.addWidget(hint)
        layout.addWidget(self.table)
        layout.addLayout(buttons)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(2000)
        self.refresh()

    def refresh(self):
        selected = self._selected()
        self.items = self.controller.alarms.all()
        now = datetime.now()
        self.table.setRowCount(len(self.items))
        for row, alarm in enumerate(self.items):
            what = {"alarm": "Alarm", "timer": f"Timer ({alarm.text})"}.get(alarm.kind, alarm.text)
            self.table.setItem(row, 0, QTableWidgetItem(alarms.when_text(alarm.due, now)))
            self.table.setItem(row, 1, QTableWidgetItem(what))
            if selected and alarm.id == selected.id:
                self.table.selectRow(row)
        self.delete_button.setEnabled(bool(self.items))
        self.delete_all_button.setEnabled(bool(self.items))

    def _selected(self):
        rows = self.table.selectionModel().selectedRows()
        return self.items[rows[0].row()] if rows and rows[0].row() < len(self.items) else None

    def delete_selected(self):
        alarm = self._selected()
        if alarm:
            self.controller.alarms.remove(alarm.id)
            self.refresh()

    def delete_all(self):
        answer = QMessageBox.question(self, APP_NAME, "Delete every alarm, timer and reminder?")
        if answer == QMessageBox.StandardButton.Yes:
            self.controller.alarms.clear()
            self.refresh()


def _pairs_text(items):
    return "\n".join(f"{name} = {value}" for name, value in items.items())


def _parse_pairs(text):
    """'name = value' lines -> dict (lines without '=' are ignored)."""
    pairs = {}
    for line in text.splitlines():
        name, sep, value = line.partition("=")
        if sep and name.strip() and value.strip():
            pairs[name.strip()] = value.strip()
    return pairs


class SettingsDialog(QDialog):
    """The settings from config.py, saved to settings.json."""

    LISTS = [
        ("vip_domains", "VIP domains"),
        ("vip_senders", "VIP senders"),
        ("muted_domains", "Muted domains"),
        ("keywords", "Keywords"),
    ]

    def __init__(self, requests, parent=None):
        super().__init__(parent)
        self.requests = requests
        self.setWindowTitle(f"{APP_NAME} - settings")
        self.setWindowIcon(QIcon(draw_icon()))
        values = settings.current()

        # General
        self.poll = QSpinBox()
        self.poll.setRange(1, 60)
        self.poll.setSuffix(" min")
        self.poll.setValue(values["poll_minutes"])
        self.read_aloud = QCheckBox("Read new important mail aloud")
        self.read_aloud.setChecked(values["read_new_aloud"])
        self.voice = QComboBox()
        self.voice.addItem("Automatic (British English if installed)", "")
        if values["voice"]:
            self.voice.addItem(values["voice"], values["voice"])
            self.voice.setCurrentIndex(1)
        self.test_button = QPushButton("Test voice")
        self.test_button.clicked.connect(self.test_voice)
        self.rate = QSlider(Qt.Orientation.Horizontal)
        self.rate.setRange(-10, 10)
        self.rate.setValue(values["speech_rate"])
        self.language = QLineEdit(values["dictation_language"])
        self.autostart = QCheckBox(startup.label())
        self.autostart.setEnabled(startup.available())
        self.autostart.setChecked(startup.enabled())
        voice_row = QHBoxLayout()
        voice_row.addWidget(self.voice, 1)
        voice_row.addWidget(self.test_button)
        general = QFormLayout()
        general.addRow("Check for mail every:", self.poll)
        general.addRow("", self.read_aloud)
        general.addRow("Voice:", voice_row)
        general.addRow("Speed:", self.rate)
        general.addRow("Language you speak:", self.language)
        general.addRow("", self.autostart)

        # Jarvis
        self.wake = QCheckBox('Listen for "Hey Jarvis"')
        self.wake.setChecked(values["wake_word"])
        self.wake.setEnabled(wakeword.available())
        self.sensitivity = QComboBox()
        for label, value in (("Low (fewer false wake-ups)", "low"), ("Normal", "normal"), ("High (wakes up more easily)", "high")):
            self.sensitivity.addItem(label, value)
        self.sensitivity.setCurrentIndex(max(0, self.sensitivity.findData(values["wake_sensitivity"])))
        self.hotkey = QCheckBox(f"{config.HOTKEY_LABEL} starts listening")
        self.hotkey.setChecked(values["hotkey"])
        self.hotkey.setEnabled(hotkey.available())
        self.city = QLineEdit(values["home_city"])
        self.music = QLineEdit(values["music_folder"])
        self.music.setPlaceholderText("Your Music folder")
        self.contacts = QPlainTextEdit(_pairs_text(values["contacts"]))
        self.contacts.setPlaceholderText("Mum = mum@example.com")
        self.websites = QPlainTextEdit(_pairs_text(values["websites"]))
        jarvis_form = QFormLayout()
        jarvis_form.addRow("", self.wake)
        jarvis_form.addRow('"Hey Jarvis" sensitivity:', self.sensitivity)
        jarvis_form.addRow("", self.hotkey)
        self.address = QLineEdit(values["address"])
        self.address.setPlaceholderText("e.g. sir, or your name; empty for neither")
        self.hud_at_start = QCheckBox("Open the HUD when Jarvis starts")
        self.hud_at_start.setChecked(values["hud_at_start"])
        jarvis_form.addRow("Jarvis calls you:", self.address)
        jarvis_form.addRow("", self.hud_at_start)
        jarvis_form.addRow("Your city (weather):", self.city)
        jarvis_form.addRow("Music folder:", self.music)
        jarvis_form.addRow("Contacts\n(name = address):", self.contacts)
        jarvis_form.addRow('Websites for "open"\n(name = address):', self.websites)

        # Mail rules
        self.lists = {}
        mail_form = QFormLayout()
        for key, label in self.LISTS:
            box = QPlainTextEdit("\n".join(values[key]))
            box.setFixedHeight(70)
            self.lists[key] = box
            mail_form.addRow(f"{label}\n(one per line):", box)

        # AI
        self.ai_key = QLineEdit(values["ai_key"])
        self.ai_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.ai_key.setPlaceholderText("sk-ant-...  (empty: Jarvis uses its offline rules)")
        self.ai_model = QComboBox()
        for model, name in ai.MODELS.items():
            self.ai_model.addItem(name, model)
        if self.ai_model.findData(values["ai_model"]) < 0:
            self.ai_model.addItem(values["ai_model"], values["ai_model"])
        self.ai_model.setCurrentIndex(self.ai_model.findData(values["ai_model"]))
        self.ai_mail = QCheckBox("Let Claude analyse my unread email (it sees the sender, subject and first lines)")
        self.ai_mail.setChecked(values["ai_for_mail"])
        about = QLabel(
            "With a Claude API key, Claude writes the news and inbox briefings and answers anything Jarvis "
            "has no command for. Get a key at console.anthropic.com. The API is paid per use (see the prices "
            "there); Haiku costs least. The key is kept in your settings file on this computer.")
        about.setWordWrap(True)
        about.setObjectName("muted")
        ai_form = QFormLayout()
        ai_form.addRow("Claude API key:", self.ai_key)
        ai_form.addRow("Model:", self.ai_model)
        ai_form.addRow("", self.ai_mail)
        ai_form.addRow("", about)

        tabs = QTabWidget()
        pages = [("General", general), ("Jarvis", jarvis_form), ("AI", ai_form), ("Mail rules", mail_form)]
        self.human_form = self._human_voice_form(values) if macos.available() else None
        if self.human_form:
            pages.insert(1, ("Human voice", self.human_form))
        for title, form in pages:
            page = QWidget()
            page.setLayout(form)
            tabs.addTab(page, title)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Save).setObjectName("primary")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.addWidget(tabs)
        layout.addWidget(buttons)
        self.resize(600, 560)
        requests.voices.emit()

    def _human_voice_form(self, values):
        ready = human_voice.installed()
        self.engine = QComboBox()
        self.engine.addItem("The Mac's own voice (instant)", "mac")
        self.engine.addItem("Human voice: Chatterbox", "chatterbox")
        self.engine.setCurrentIndex(max(0, self.engine.findData(values["voice_engine"])))
        self.engine.setEnabled(ready)
        installed_models = human_voice.models_installed() if ready else []
        self.voice_model = QComboBox()
        self.voice_model.addItem("Automatic: the best one this Mac can say without waiting", "auto")
        for name, label in (("turbo", "Best quality: Chatterbox Turbo"), ("nano", "Fastest: Chatterbox Nano")):
            if name in installed_models:
                info = human_voice.install_info(name)
                speed = f" ({info['speed']:.1f}x as fast as speech here)" if info.get("speed") else ""
                self.voice_model.addItem(label + speed, name)
        self.voice_model.setCurrentIndex(max(0, self.voice_model.findData(values["voice_model"])))
        self.voice_model.setEnabled(self.voice_model.count() > 2)
        if not ready:
            project = config.SOURCE_PROJECT or config.ROOT
            status = ("Not installed yet. It's free and runs on this Mac only (about 4 GB). To install it, "
                      f'run this in Terminal:\n    bash "{project}/macos/install_voice.sh"')
        else:
            status = ("Installed. The usual replies, and anything said before, come out at once; new sentences "
                      "take a moment to make.")
        self.human_status = QLabel(status)
        self.human_status.setWordWrap(True)
        self.human_status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.sample = QComboBox()
        self.sample.addItem("Chatterbox's own voice", "")
        for path, name in ((human_voice.samples_dir() / "my-voice.wav", "My recording"),
                           (human_voice.samples_dir() / "voice-sample.wav", "Imported recording")):
            if path.is_file():
                self.sample.addItem(name, str(path))
        if values["voice_sample"] and self.sample.findData(values["voice_sample"]) < 0 \
                and Path(values["voice_sample"]).is_file():
            self.sample.addItem(Path(values["voice_sample"]).name, values["voice_sample"])
        self.sample.setCurrentIndex(max(0, self.sample.findData(values["voice_sample"])))
        self.record_button = QPushButton("Record my voice...")
        self.record_button.clicked.connect(self.record_voice)
        self.file_button = QPushButton("Use a recording...")
        self.file_button.clicked.connect(self.use_recording)
        self.file_button.setEnabled(False)
        self.consent = QCheckBox("It's my own voice, or the person speaking agreed to Jarvis using it")
        self.consent.toggled.connect(lambda checked: self.file_button.setEnabled(checked and ready))
        self.voice_effect = QComboBox()
        self.voice_effect.addItem("None", "")
        self.voice_effect.addItem("Deep helmet voice: lower, metallic, as if through a mask", "helmet")
        self.voice_effect.setCurrentIndex(max(0, self.voice_effect.findData(values["voice_effect"])))
        self.voice_effect.setEnabled(ready)
        self.human_test = QPushButton("Test the human voice")
        self.human_test.clicked.connect(self.test_human_voice)
        for widget in (self.sample, self.record_button, self.human_test, self.consent):
            widget.setEnabled(ready)
        buttons = QHBoxLayout()
        buttons.addWidget(self.record_button)
        buttons.addWidget(self.file_button)
        buttons.addStretch(1)
        about = QLabel(
            "Chatterbox is an open speech model by Resemble AI (MIT licence). It runs on this Mac, so nothing you "
            "say goes anywhere. It can sound like a recording of 10 to 15 seconds of clear speech: record your "
            "own, or use one of someone who agreed. Everything it says carries Resemble AI's inaudible watermark, "
            "which shows it was made by AI. While it loads, and if it ever fails, Jarvis uses the Mac's voice.")
        about.setWordWrap(True)
        about.setObjectName("muted")
        form = QFormLayout()
        form.addRow("Jarvis speaks with:", self.engine)
        form.addRow("Model:", self.voice_model)
        form.addRow("", self.human_status)
        form.addRow("Sounds like:", self.sample)
        form.addRow("", buttons)
        form.addRow("", self.consent)
        form.addRow("Effect:", self.voice_effect)
        form.addRow("", self.human_test)
        form.addRow("", about)
        return form

    def record_voice(self):
        answer = QMessageBox.information(
            self, APP_NAME, f"After you press OK and hear the chime, read this aloud in your normal voice. "
            f"Jarvis listens for {human_voice.SAMPLE_SECONDS} seconds.\n\n\u201c{human_voice.SAMPLE_TEXT}\u201d",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        if answer == QMessageBox.StandardButton.Ok:
            self.record_button.setEnabled(False)
            self.requests.record_voice.emit()

    def voice_recorded(self, path, error):
        self.record_button.setEnabled(True)
        if error:
            QMessageBox.warning(self, APP_NAME, error)
            return
        self._choose_sample(path, "My recording")

    def use_recording(self):
        source, _ = QFileDialog.getOpenFileName(self, "A recording of the voice", str(Path.home()),
                                                "Recordings (*.wav *.m4a *.mp3 *.aiff *.aif *.caf *.flac)")
        if not source:
            return
        try:
            path = human_voice.import_sample(source)
        except human_voice.VoiceError as exc:
            QMessageBox.warning(self, APP_NAME, str(exc))
            return
        self._choose_sample(str(path), "Imported recording")

    def _choose_sample(self, path, name):
        index = self.sample.findData(path)
        if index < 0:
            self.sample.addItem(name, path)
            index = self.sample.count() - 1
        self.sample.setCurrentIndex(index)

    def test_human_voice(self):
        self.requests.test_human_voice.emit(self.sample.currentData() or "", self.voice_effect.currentData() or "")

    def fill_voices(self, installed, selected):
        known = {self.voice.itemData(i) for i in range(self.voice.count())}
        for voice in installed:
            name = voice.get("name", "")
            if name and name not in known:
                self.voice.addItem(f"{name} ({voice.get('culture', '')})", name)
                known.add(name)

    def values(self):
        values = {
            "poll_minutes": self.poll.value(),
            "read_new_aloud": self.read_aloud.isChecked(),
            "voice": self.voice.currentData() or "",
            "speech_rate": self.rate.value(),
            "voice_engine": self.engine.currentData() if self.human_form and self.engine.isEnabled()
            else config.VOICE_ENGINE,
            "voice_sample": (self.sample.currentData() or "") if self.human_form else config.VOICE_SAMPLE,
            "voice_model": (self.voice_model.currentData() or config.VOICE_MODEL) if self.human_form
            else config.VOICE_MODEL,
            "voice_effect": (self.voice_effect.currentData() or "") if self.human_form else config.VOICE_EFFECT,
            "dictation_language": self.language.text().strip() or "en-GB",
            "wake_word": self.wake.isChecked(),
            "wake_sensitivity": self.sensitivity.currentData(),
            "hotkey": self.hotkey.isChecked(),
            "home_city": self.city.text().strip(),
            "music_folder": self.music.text().strip(),
            "address": self.address.text().strip(),
            "hud_at_start": self.hud_at_start.isChecked(),
            "ai_key": self.ai_key.text().strip(),
            "ai_model": self.ai_model.currentData() or config.AI_MODEL,
            "ai_for_mail": self.ai_mail.isChecked(),
            "contacts": _parse_pairs(self.contacts.toPlainText()),
            "websites": _parse_pairs(self.websites.toPlainText()),
        }
        values.update({key: box.toPlainText().splitlines() for key, box in self.lists.items()})
        return values

    def test_voice(self):
        values = self.values()
        self.requests.test_voice.emit(values["voice"], values["speech_rate"])

    def accept(self):
        try:
            settings.save(self.values())
        except OSError as exc:
            QMessageBox.warning(self, APP_NAME, f"Could not save the settings: {exc}")
            return
        if self.autostart.isEnabled() and self.autostart.isChecked() != startup.enabled():
            try:
                startup.set_enabled(self.autostart.isChecked())
            except OSError as exc:
                QMessageBox.warning(self, APP_NAME, f"Could not change the start-up setting: {exc}")
        super().accept()


class Views:
    """Shows the conversation in both the small Jarvis window and the HUD."""

    def __init__(self, *windows):
        self.windows = windows

    def add(self, who, text):
        for window in self.windows:
            window.add(who, text)

    def set_state(self, busy):
        for window in self.windows:
            window.set_state(busy)

    def flash(self, text):
        for window in self.windows:
            window.flash(text)


class TrayApp(QObject):
    """The tray icon and its menu. Owns the windows, the hotkey, "Hey Jarvis" and the alarms."""

    hotkey_pressed = pyqtSignal()  # emitted from the hotkey's own thread

    def __init__(self, worker=None, threaded=True, alarm_store=None):
        super().__init__()
        self.alarms = alarm_store or alarms.AlarmStore(config.ALARMS_FILE)
        self.requests = Requests()
        self.worker = worker or Worker(alarm_store=self.alarms)
        self.thread = None
        if threaded:
            self.thread = QThread()
            self.worker.moveToThread(self.thread)
        self._connect_worker()
        if self.thread:
            self.thread.start()

        self.alert = False
        self.paused = False
        self.busy_text = ""
        self.last_check = "not yet"
        self.inbox_window = None
        self.compose_dialog = None
        self.settings_dialog = None
        self.alarms_window = None
        self.alarm_dialogs = []
        self.hotkey = None
        self.wake = None
        self.audio_busy = False
        self.worker.ringing = lambda: bool(self.alarm_dialogs)
        self.jarvis_window = JarvisWindow(self)
        self.hud = hud.HudWindow(self)
        self.views = Views(self.jarvis_window, self.hud)
        self.worker.hud_open = lambda: self.hud.isVisible()
        self.dashboard_loaded = None  # when the HUD's weather and news were last fetched
        self.hotkey_pressed.connect(self.talk)

        self.tray = QSystemTrayIcon(tray_icon(), self)
        self.tray.setContextMenu(self._build_menu())
        self.tray.activated.connect(self._on_activated)
        self._update_status()
        self.tray.show()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._timed_check)
        self.timer.start(config.POLL_SECONDS * 1000)
        self.alarm_timer = QTimer(self)
        self.alarm_timer.timeout.connect(self.check_alarms)
        self.alarm_timer.start(1000)
        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.timeout.connect(self._hide_jarvis_if_unused)
        self.dashboard_timer = QTimer(self)
        self.dashboard_timer.timeout.connect(lambda: self.refresh_dashboard() if self.hud.isVisible() else None)
        self.dashboard_timer.start(15 * 60 * 1000)

    def _connect_worker(self):
        r, w = self.requests, self.worker
        r.check.connect(w.check)
        r.inbox.connect(w.load_inbox)
        r.read_important.connect(w.read_important)
        r.read_mail.connect(w.read_mail)
        r.reply.connect(w.prepare_reply)
        r.send.connect(w.send)
        r.dictate.connect(w.dictate)
        r.voices.connect(w.load_voices)
        r.test_voice.connect(w.test_voice)
        r.test_human_voice.connect(w.test_human_voice)
        r.record_voice.connect(w.record_voice)
        r.command.connect(w.command)
        r.listen_command.connect(w.listen_command)
        r.say.connect(w.say)
        r.recognize.connect(w.recognize)
        r.dashboard.connect(w.refresh_dashboard)
        w.audio_busy.connect(self._on_audio_busy)
        w.dashboard_ready.connect(lambda data: self.hud.update_data(data))
        w.busy.connect(self._on_busy)
        w.failed.connect(self._on_failed)
        w.checked.connect(self._on_checked)
        w.inbox_ready.connect(self._on_inbox)
        w.reply_ready.connect(self.open_compose)
        w.sent.connect(self._on_sent)
        w.dictated.connect(self._on_dictated)
        w.voices_ready.connect(self._on_voices)
        w.voice_recorded.connect(self._on_voice_recorded)
        w.heard.connect(lambda text: self.views.add("You", text) if text else None)
        w.replied.connect(self._on_replied)

    def _build_menu(self):
        self.menu = QMenu()
        self.status_action = self.menu.addAction("")
        self.status_action.setEnabled(False)
        self.menu.addSeparator()
        self.menu.addAction("Open the HUD").triggered.connect(self.open_hud)
        self.menu.addSeparator()
        self.menu.addAction(f"Talk to Jarvis   ({config.HOTKEY_LABEL})").triggered.connect(self.talk)
        self.menu.addAction("Jarvis window").triggered.connect(self.show_jarvis)
        self.menu.addSeparator()
        self.menu.addAction("Open inbox").triggered.connect(self.open_inbox)
        self.menu.addAction("Check mail now").triggered.connect(self.check_now)
        self.menu.addAction("Read important mail aloud").triggered.connect(
            lambda: self.requests.read_important.emit()
        )
        self.menu.addAction("New mail...").triggered.connect(lambda: self.open_compose(Draft(to="", subject="")))
        self.menu.addSeparator()
        self.menu.addAction("Alarms and reminders...").triggered.connect(self.open_alarms)
        self.menu.addAction("Stop speaking").triggered.connect(self.stop_speaking)
        self.menu.addSeparator()
        self.aloud_action = self.menu.addAction("Read new mail aloud")
        self.aloud_action.setCheckable(True)
        self.aloud_action.setChecked(config.READ_NEW_ALOUD)
        self.aloud_action.toggled.connect(lambda on: self._save_setting("read_new_aloud", on))
        self.wake_action = self.menu.addAction('Listen for "Hey Jarvis"')
        self.wake_action.setCheckable(True)
        self.wake_action.setChecked(config.WAKE_WORD_ENABLED and wakeword.available())
        self.wake_action.setEnabled(wakeword.available())
        self.wake_action.toggled.connect(self._set_wake_word)
        self.pause_action = self.menu.addAction("Pause mail checking")
        self.pause_action.setCheckable(True)
        self.pause_action.toggled.connect(self._set_paused)
        self.menu.addAction("Settings...").triggered.connect(self.open_settings)
        self.menu.addSeparator()
        self.menu.addAction("Quit").triggered.connect(self.quit)
        return self.menu

    # --- talking to Jarvis --------------------------------------------------------

    def start_listening(self):
        """Switch on the hotkey and "Hey Jarvis" (and load the human voice), as the settings say."""
        self._update_hotkey()
        self._update_wake()
        self._update_human_voice()

    def _update_hotkey(self):
        wanted = config.HOTKEY_ENABLED and hotkey.available()
        if wanted and self.hotkey is None:
            self.hotkey = hotkey.create(self.hotkey_pressed.emit)
            self.hotkey.start()
            self.hotkey.ready.wait(2)
            if not self.hotkey.registered:
                self.hotkey = None
                self._on_failed(f"{config.HOTKEY_LABEL} is already used by another program, so it won't start Jarvis.")
        elif not wanted and self.hotkey is not None:
            self.hotkey.stop()
            self.hotkey = None

    def _update_wake(self):
        wanted = config.WAKE_WORD_ENABLED and wakeword.available()
        if wanted and self.wake is None:
            self.wake = wakeword.WakeWordListener()
            self.wake.heard.connect(self._on_wake_heard)
            self.wake.command.connect(self._on_command_audio)
            self.wake.nothing_said.connect(self._on_nothing_said)
            self.wake.failed.connect(self._on_wake_failed)
            self.wake.start()
        elif not wanted and self.wake is not None:
            self.wake.stop()
            self.wake = None
        self.wake_action.blockSignals(True)
        self.wake_action.setChecked(wanted)
        self.wake_action.blockSignals(False)

    def _set_wake_word(self, on):
        self._save_setting("wake_word", on)
        self._update_wake()

    def _on_wake_failed(self, message):
        self._on_failed(message)

    def talk(self):
        """Listen for one spoken command (also stops Jarvis if it's talking)."""
        self.stop_speaking()
        self.show_jarvis()
        if self.wake is not None and self.wake.isRunning():
            self.wake.record_now()  # the open microphone records straight away
            if not self.audio_busy:
                self.wake.resume()
            return
        if self.busy_text != "Listening":
            self.requests.listen_command.emit()  # the worker records it instead

    def _on_wake_heard(self):
        self.show_jarvis()
        self.views.set_state("Listening")
        sound.listening()

    def _on_command_audio(self, raw):
        self.views.set_state("Thinking")
        self.requests.recognize.emit(raw)

    def _on_nothing_said(self):
        self.views.flash("I didn't hear a command.")
        self.hide_timer.start(8000)

    def _on_audio_busy(self, busy):
        self.audio_busy = busy
        if self.wake is None:
            return
        if busy:
            self.wake.pause()  # free the microphone, and don't hear Jarvis's own voice
        else:
            QTimer.singleShot(0 if self.wake.recording_requested else 400, self._resume_wake)

    def run_command(self, text):
        self.worker.cancelled.set()
        speech.stop()
        self.views.add("You", text)
        self.requests.command.emit(text)

    def stop_speaking(self):
        self.worker.cancelled.set()
        speech.stop()

    def show_jarvis(self):
        self.hide_timer.stop()
        hud_showing = self.hud.isVisible() and not self.hud.isMinimized()
        if hud_showing and macos.available() and not self.hud.isActiveWindow():
            hud_showing = False  # a Mac won't raise it over other apps without taking the focus
        if hud_showing:
            self.hud.raise_()  # the HUD shows the conversation; no need for the small window too
        else:
            self.jarvis_window.pop_up()

    def reopened(self, state):
        """Jarvis.app opened again (Launchpad, Spotlight, Finder) while running: show the HUD."""
        if state != Qt.ApplicationState.ApplicationActive:
            return
        if not any(w.isVisible() for w in QApplication.topLevelWidgets() if not isinstance(w, QMenu)):
            self.open_hud()

    def open_hud(self):
        self.jarvis_window.hide()
        if self.hud.isMinimized() or not self.hud.isVisible():
            self.hud.showMaximized()
        self._bring_up(self.hud)
        stale = self.dashboard_loaded is None or datetime.now() - self.dashboard_loaded > timedelta(minutes=10)
        if stale:
            self.refresh_dashboard()

    def refresh_dashboard(self):
        self.dashboard_loaded = datetime.now()
        self.requests.dashboard.emit(not self.paused)

    def _hide_jarvis_if_unused(self):
        window = self.jarvis_window
        if not self.busy_text and not window.isActiveWindow() and not window.underMouse():
            window.hide()

    def _on_replied(self, text, reply):
        for message in (reply.say, *reply.more):
            if message:
                self.views.add("Jarvis", message)
        if isinstance(reply.data, dict):  # analysed mail or news, the weather: the HUD shows it
            self.hud.update_data(reply.data)
            if {"weather", "news"} <= set(reply.data):
                self.dashboard_loaded = datetime.now()  # just fetched: opening the HUD needn't fetch again
        actions = {
            "dashboard": self.open_hud,
            "alarm_stop": lambda: [dialog.accept() for dialog in list(self.alarm_dialogs)],
            "alarm_snooze": lambda: [dialog.snooze() for dialog in list(self.alarm_dialogs)],
            "compose": lambda: self.open_compose(reply.data),
            "inbox": self.open_inbox,
            "alarms": self.open_alarms,
            "settings": self.open_settings,
            "screenshot": self.take_screenshot,
        }
        if reply.action in actions:
            actions[reply.action]()

    def take_screenshot(self):
        folder = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.PicturesLocation)) / "Screenshots"
        path = folder / f"jarvis-{datetime.now():%Y%m%d-%H%M%S}.png"
        was_visible = self.jarvis_window.isVisible()
        self.jarvis_window.hide()  # keep Jarvis itself out of the picture
        QTimer.singleShot(300, lambda: self._grab_screen(path, was_visible))

    def _grab_screen(self, path, show_again):
        path.parent.mkdir(parents=True, exist_ok=True)
        if macos.available():  # macOS's own tool; it asks once for Screen Recording permission
            try:
                saved = macos.screenshot(path)
            except macos.MacError:
                saved = False
        else:
            screen = QGuiApplication.primaryScreen()
            saved = screen is not None and screen.grabWindow(0).save(str(path))
        if show_again:
            self.jarvis_window.pop_up()
        message = f"Screenshot saved to {path}" if saved else "Sorry, I couldn't take the screenshot."
        self.tray.showMessage(APP_NAME, message, QSystemTrayIcon.MessageIcon.Information, 6000)
        self.views.add("Jarvis", message)

    # --- alarms, timers and reminders ------------------------------------------------

    def check_alarms(self):
        for alarm in self.alarms.pop_due(datetime.now()):
            self.ring(alarm)

    def ring(self, alarm):
        text = alarm.announcement()
        if datetime.now() - alarm.due > timedelta(minutes=2):
            text += " (This went off while Jarvis wasn't running.)"
        self.tray.showMessage(APP_NAME, text, QSystemTrayIcon.MessageIcon.Information, 15000)
        if alarm.kind == "reminder":
            sound.chime()
            self.requests.say.emit(text)
        else:
            sound.play_alarm()
        dialog = AlarmDialog(alarm, text, self)
        self.alarm_dialogs.append(dialog)
        self._bring_up(dialog)

    def open_alarms(self):
        if self.alarms_window is None:
            self.alarms_window = AlarmsWindow(self)
        self.alarms_window.refresh()
        self._bring_up(self.alarms_window)

    # --- mail checking ------------------------------------------------------------------

    def check_now(self):
        self.requests.check.emit()

    def _timed_check(self):
        if not self.paused:
            self.check_now()

    def _set_paused(self, paused):
        self.paused = paused
        self._update_status()

    def _save_setting(self, key, value):
        values = settings.current()
        values[key] = value
        try:
            settings.save(values)
        except OSError as exc:
            self._on_failed(f"Could not save the settings: {exc}")

    def set_alert(self, on):
        self.alert = on
        self.tray.setIcon(tray_icon(alert=on))

    def _update_status(self):
        text = self.busy_text or f"Last mail check: {self.last_check}"
        if self.paused:
            text += " (mail checking paused)"
        self.status_action.setText(text)
        self.tray.setToolTip(f"{APP_NAME}\n{text}")

    # --- what the worker reports ---------------------------------------------------------

    def _on_busy(self, text):
        self.busy_text = text
        self._update_status()
        if text in JarvisWindow.STATES or not text:
            self.views.set_state(text)
        if self.inbox_window:
            self.inbox_window.set_busy(text)
        if not text:
            if any(d.alarm.kind != "reminder" for d in self.alarm_dialogs):
                sound.play_alarm()  # still ringing: keep going after Jarvis's answer
            if self.jarvis_window.isVisible():
                self.hide_timer.start(15000)

    def _resume_wake(self):
        if self.wake and not self.audio_busy:
            self.wake.resume()

    def _on_failed(self, message):
        self.tray.showMessage(APP_NAME, message, QSystemTrayIcon.MessageIcon.Warning, 10000)
        if self.jarvis_window.isVisible():
            self.views.add("Jarvis", message)
        if self.compose_dialog:
            self.compose_dialog.job_failed(message)

    def _on_checked(self, new_count, important):
        self.last_check = f"{datetime.now():%H:%M} ({new_count} new, {len(important)} important)"
        if important:
            self.set_alert(True)
        self._update_status()
        if new_count and self.inbox_window and self.inbox_window.isVisible():
            self.requests.inbox.emit()

    def _on_inbox(self, mails):
        if self.inbox_window:
            self.inbox_window.show_mails(mails)

    def _on_sent(self, draft):
        self.tray.showMessage(APP_NAME, f"Sent to {draft.to}.", QSystemTrayIcon.MessageIcon.Information, 5000)
        if self.compose_dialog:
            self.compose_dialog.was_sent()

    def _on_dictated(self, text):
        if self.compose_dialog and self.compose_dialog.waiting:
            self.compose_dialog.add_dictated(text)

    def _on_voices(self, installed, selected):
        if self.settings_dialog:
            self.settings_dialog.fill_voices(installed, selected)

    # --- windows ---------------------------------------------------------------------------

    def _on_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.open_hud()

    @staticmethod
    def _bring_up(window):
        window.show()
        window.raise_()
        window.activateWindow()

    def open_inbox(self):
        if self.inbox_window is None:
            self.inbox_window = InboxWindow(self)
        self._bring_up(self.inbox_window)
        self.set_alert(False)
        self.requests.inbox.emit()

    def open_compose(self, draft):
        if self.compose_dialog and self.compose_dialog.isVisible():
            self._bring_up(self.compose_dialog)  # finish the open one first
            return
        self.compose_dialog = ComposeDialog(draft, self.requests)
        self._bring_up(self.compose_dialog)

    def _on_voice_recorded(self, path, error):
        if self.settings_dialog and self.settings_dialog.isVisible():
            self.settings_dialog.voice_recorded(path, error)

    def open_settings(self):
        if self.settings_dialog and self.settings_dialog.isVisible():
            self._bring_up(self.settings_dialog)
            return
        self.settings_dialog = SettingsDialog(self.requests)
        self.settings_dialog.accepted.connect(self._settings_saved)
        self._bring_up(self.settings_dialog)

    def _settings_saved(self):
        self.timer.setInterval(config.POLL_SECONDS * 1000)
        self.aloud_action.blockSignals(True)
        self.aloud_action.setChecked(config.READ_NEW_ALOUD)
        self.aloud_action.blockSignals(False)
        self._update_hotkey()
        self._update_wake()
        self._update_status()
        self._update_human_voice()
        self.hud.update_brain()
        if self.hud.isVisible():
            self.refresh_dashboard()  # the city may have changed

    def _update_human_voice(self):
        """Load the human voice if it's chosen (and have it make the usual replies while idle), and
        free its memory if it isn't."""
        if human_voice.wanted():
            human_voice.ENGINE.phrases = lambda: commands.usual_replies() + [VOICE_SAMPLE]
            human_voice.ENGINE.start()  # restarts it if another model was chosen
            human_voice.ENGINE.warm()  # the voice recording may have changed (does nothing while it loads)
        else:
            human_voice.ENGINE.stop()

    def quit(self):
        self.timer.stop()
        self.alarm_timer.stop()
        sound.stop()
        self.stop_speaking()
        human_voice.ENGINE.stop()
        if self.hotkey:
            self.hotkey.stop()
        if self.wake:
            self.wake.stop()
        self.tray.hide()
        if self.thread:
            self.thread.quit()
            if not self.thread.wait(3000):
                os._exit(0)  # the worker is stuck in a long job (a login, speech); just leave
        QApplication.quit()


def _log_to_file_if_needed():
    """pythonw and Jarvis.app have no console, so prints and errors go to a log file instead."""
    in_app = getattr(sys, "frozen", None) == "macosx_app"  # started from Jarvis.app
    if sys.stdout is not None and sys.stderr is not None and not in_app:
        return
    config.APP_DIR.mkdir(parents=True, exist_ok=True)
    path = config.APP_DIR / "app.log"
    if path.exists() and path.stat().st_size > 1_000_000:
        path.unlink()
    log = open(path, "a", encoding="utf-8", buffering=1)
    sys.stdout = log if in_app else sys.stdout or log
    sys.stderr = log if in_app else sys.stderr or log


def _report_error(kind, value, tb):
    """Last resort for errors in the windows: log them and say so, but keep running."""
    traceback.print_exception(kind, value, tb)
    if QApplication.instance():
        QMessageBox.warning(None, APP_NAME, f"Something went wrong: {value}")


def quit_on_terminate(tray_app):
    """A polite "please stop" from another program (install.sh updating Jarvis, a logout)
    quits Jarvis the same way its Quit item does. Python only sees the signal when it
    runs some code, which the one-second alarm check makes sure of."""
    def stop(_signal, _frame):
        QTimer.singleShot(0, tray_app.quit)

    signal.signal(signal.SIGTERM, stop)
    return stop


def run():
    config.adopt_old_app_dir()
    _log_to_file_if_needed()
    print(f"--- {datetime.now():%Y-%m-%d %H:%M} {APP_NAME} starting")
    settings.load()
    try:
        windows.take_over_start_entry()
    except OSError as exc:
        print(f"Could not update the Windows start-up entry: {exc}")
    sys.excepthook = _report_error
    qt_app = QApplication(sys.argv[:1])
    qt_app.setApplicationName(APP_NAME)
    qt_app.setQuitOnLastWindowClosed(False)  # closing a window keeps Jarvis running in the tray
    qt_app.setWindowIcon(QIcon(draw_icon()))
    apply_theme(qt_app)

    config.APP_DIR.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(config.APP_DIR / "app.lock"))
    if not lock.tryLock(100):
        where = "in the menu bar" if macos.available() else "next to the clock"
        QMessageBox.information(None, APP_NAME, f"Jarvis is already running. Its icon is {where}.")
        return 0

    tray_app = TrayApp()
    quit_on_terminate(tray_app)
    tray_app.start_listening()
    if config.HUD_AT_START or not QSystemTrayIcon.isSystemTrayAvailable():
        tray_app.open_hud()  # (with no tray to sit in, a window has to show)
    else:
        tray_app.tray.showMessage(
            APP_NAME, f"Jarvis is running. {IDLE_TEXT}.", QSystemTrayIcon.MessageIcon.Information, 6000,
        )
    if macos.available():
        qt_app.applicationStateChanged.connect(lambda state: tray_app.reopened(state))
    QTimer.singleShot(2000, tray_app.check_now)
    code = qt_app.exec()
    lock.unlock()
    return code
