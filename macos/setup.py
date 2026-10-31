"""Builds Jarvis.app with py2app in alias mode: a small app that runs the code and the
private Python environment that install.sh made. Run by install.sh:

    python setup.py py2app -A
"""
from setuptools import setup

VERSION = "3.1"

PLIST = {
    "CFBundleName": "Jarvis",
    "CFBundleDisplayName": "Jarvis",
    "CFBundleIdentifier": "local.jarvis.assistant",
    "CFBundleShortVersionString": VERSION,
    "CFBundleVersion": VERSION,
    "LSUIElement": True,  # lives in the menu bar, like the tray icon on Windows
    "LSMinimumSystemVersion": "13.0",
    "NSHighResolutionCapable": True,
    "NSMicrophoneUsageDescription": "Jarvis listens for “Hey Jarvis” and the commands you say.",
    "NSAppleEventsUsageDescription": "Jarvis controls apps such as Spotify and Music when you ask it to.",
    "NSHumanReadableCopyright": "Personal project",
}

setup(
    name="Jarvis",
    version=VERSION,
    app=["launcher.py"],
    options={"py2app": {"iconfile": "Jarvis.icns", "plist": PLIST}},
)
