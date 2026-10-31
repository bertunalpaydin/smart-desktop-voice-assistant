import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAC = sys.platform == "darwin"

# Everything Jarvis keeps about you (login, settings, alarms, notes) lives in a
# per-user folder outside the project, so OneDrive, iCloud and git never see it and
# a new copy of the project keeps working without moving anything.
_data_home = os.environ.get("LOCALAPPDATA")
_DATA = Path(_data_home) if _data_home else Path.home() / ".local" / "share"
APP_DIR = Path.home() / "Library" / "Application Support" / "Jarvis" if MAC else _DATA / "jarvis"
OLD_APP_DIR = _DATA / "mail-assistant"  # earlier versions were called Mail Assistant (Windows only)
CREDENTIALS_FILE = APP_DIR / "credentials.json"
TOKEN_FILE = APP_DIR / "token.json"
DB_FILE = APP_DIR / "assistant.db"
SETTINGS_FILE = APP_DIR / "settings.json"  # changes made in the settings window
ALARMS_FILE = APP_DIR / "alarms.json"
NOTES_FILE = APP_DIR / "notes.txt"

# Put the file from Google Cloud here; it is copied to APP_DIR on first use.
# Earlier versions kept token.json here as well; it is picked up the same way.
PROJECT_SECRETS_DIR = ROOT / "secrets"


def _source_project():
    """On a Mac, install.sh runs a copy of the code and notes where the project itself is."""
    try:
        return Path((ROOT / "source.txt").read_text(encoding="utf-8").strip())
    except OSError:
        return None


SOURCE_PROJECT = _source_project()

# Reading and sending. Changing this list means one more login in the browser.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]

UNREAD_QUERY = "in:inbox is:unread"
# "read my last email": the newest mail in the inbox, read or not, leaving out Gmail's Promotions and Social tabs
LATEST_QUERY = "in:inbox -category:promotions -category:social"
MAX_UNREAD = 15
POLL_SECONDS = 120

# --- What counts as important -------------------------------------------------
# A mail needs IMPORTANT_THRESHOLD points to count.
#   VIP sender +3 | keyword in subject +2 (only in the text +1)
#   marked important by Gmail +1 | Gmail promotions/social tab -3
# Muted senders are never important. Matching ignores case.
IMPORTANT_THRESHOLD = 2

VIP_DOMAINS = ["uwe.ac.uk"]  # subdomains count too, e.g. live.uwe.ac.uk
VIP_SENDERS = []             # exact addresses
MUTED_DOMAINS = []           # e.g. "netflix.com"

KEYWORDS = [
    "interview", "assessment", "application", "placement", "next steps",
    "deadline", "urgent", "action required", "security alert",
    "payment failed", "invoice",
]

# --- Reading aloud ----------------------------------------------------------------
# VOICE is part of a voice name, e.g. "Hazel" on Windows or "Daniel" on a Mac. Left
# empty, a British English voice is used if there is one, then any English one.
# `python main.py voices` lists the voices on this computer.
VOICE = ""
SPEECH_RATE = 0       # -10 (slow) to 10 (fast)
# "mac": the computer's own voices; "chatterbox": the human voice (a Mac with macos/install_voice.sh)
VOICE_ENGINE = "mac"
VOICE_SAMPLE = ""     # a recording for the human voice to sound like ("" for Chatterbox's own voice)
# the human voice's model: "turbo" (sounds best), "nano" (faster, more synthetic), or "auto": Turbo if this
# Mac makes speech with it faster than it is spoken (the installer measures it), else Nano
VOICE_MODEL = "auto"
VOICE_EFFECT = ""     # "" or "helmet": the human voice deeper and metallic, as if through a mask
READ_MAX_CHARS = 600  # longer mail is cut off when read aloud
READ_NEW_ALOUD = True  # say new important mail out loud when it arrives

# --- Replying and sending -----------------------------------------------------
DICTATION_LANGUAGE = "en-GB"  # the language you speak when dictating a message

# --- Jarvis ---------------------------------------------------------------------
HOTKEY_ENABLED = True  # the key below starts listening, from any app
HOTKEY_LABEL = "\u2303\u2325J" if MAC else "Ctrl+Alt+J"  # Control+Option+J on a Mac
HOTKEY_SPOKEN = "Control Option J" if MAC else "Control Alt J"
WAKE_WORD_ENABLED = True  # listen for "Hey Jarvis" (offline, on this computer)
WAKE_WORD_SENSITIVITY = "normal"  # "low", "normal" or "high": how easily "Hey Jarvis" wakes it up
HOME_CITY = "Bristol"  # for the weather
ADDRESS = "sir"  # how Jarvis addresses you in greetings and briefings ("" for not at all)
HUD_AT_START = True  # open the full-screen HUD when Jarvis starts

# --- the AI brain (optional) ---------------------------------------------------------
# With a Claude API key from console.anthropic.com, Claude writes the news and mail
# analysis and answers anything Jarvis has no command for. Without one, all of
# Jarvis still works, offline rules do the analysis.
AI_API_KEY = ""
AI_MODEL = "claude-haiku-4-5-20251001"
AI_FOR_MAIL = False  # let Claude read your unread mail's sender, subject and first lines
MUSIC_FOLDER = ""  # empty: your Music folder
CONTACTS = {}  # name -> address, for "send an email to <name>"
WEBSITES = {
    "youtube": "https://www.youtube.com/",
    "youtube music": "https://music.youtube.com/",
    "google": "https://www.google.com/",
    "gmail": "https://mail.google.com/",
    "google calendar": "https://calendar.google.com/",
    "calendar": "https://calendar.google.com/",
    "google drive": "https://drive.google.com/",
    "drive": "https://drive.google.com/",
    "maps": "https://maps.google.com/",
    "github": "https://github.com/",
    "linkedin": "https://www.linkedin.com/",
    "netflix": "https://www.netflix.com/",
    "spotify": "https://open.spotify.com/",
    "blackboard": "https://blackboard.uwe.ac.uk/",
    "outlook": "https://outlook.office.com/",
    "chatgpt": "https://chatgpt.com/",
    "wikipedia": "https://en.wikipedia.org/",
    "news": "https://www.bbc.co.uk/news",
}


def adopt_old_app_dir():
    """Bring the login and data of the old Mail Assistant folder over, once."""
    if OLD_APP_DIR.is_dir() and not APP_DIR.exists():
        shutil.copytree(OLD_APP_DIR, APP_DIR, ignore=shutil.ignore_patterns("*.lock", "*.log"))
