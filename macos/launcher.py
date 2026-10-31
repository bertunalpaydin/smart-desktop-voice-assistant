"""Starts Jarvis inside Jarvis.app (see setup.py)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))

from jarvis.app import run  # noqa: E402

sys.exit(run())
