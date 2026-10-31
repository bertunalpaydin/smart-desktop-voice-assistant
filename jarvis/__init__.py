"""Jarvis, a desktop assistant for Windows and macOS."""
import os
import sys

# Python from python.org on a Mac doesn't use the system's certificates, so web
# requests would fail. Point it at certifi's (Mozilla's) list, for this process only.
if sys.platform == "darwin" and "SSL_CERT_FILE" not in os.environ:
    try:
        import certifi
    except ImportError:
        pass
    else:
        os.environ["SSL_CERT_FILE"] = certifi.where()
