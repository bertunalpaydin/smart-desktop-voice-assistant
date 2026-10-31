import filecmp
import os
import shutil

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from oauthlib.oauth2.rfc6749.errors import OAuth2Error

from . import config


class AuthError(Exception):
    """Login can't continue (missing credentials.json, login refused, ...)."""


def _same_file(a, b):
    try:
        return b.exists() and filecmp.cmp(a, b, shallow=False)
    except OSError:
        return False


def _copy(source, target):
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        _private(target)
    except OSError as exc:
        raise AuthError(f"Could not copy {source} to {target}: {exc}") from exc


def _pick_up_project_files():
    """credentials.json is dropped into the project's secrets folder, and earlier
    versions kept token.json there too. Copy them to where the app reads them."""
    creds = config.PROJECT_SECRETS_DIR / "credentials.json"
    if creds.exists() and not _same_file(creds, config.CREDENTIALS_FILE):
        _copy(creds, config.CREDENTIALS_FILE)
    token = config.PROJECT_SECRETS_DIR / "token.json"
    if token.exists() and not config.TOKEN_FILE.exists():
        _copy(token, config.TOKEN_FILE)


def _load_token():
    if not config.TOKEN_FILE.exists():
        return None
    try:
        return Credentials.from_authorized_user_file(str(config.TOKEN_FILE))
    except ValueError:
        return None  # broken file, just log in again


def _private(path):
    """Only you can read it: the login, and the app's key from Google."""
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass  # a file system without Unix permissions


def _save_token(creds):
    config.TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
    _private(config.TOKEN_FILE)


def _login():
    if not config.CREDENTIALS_FILE.exists():
        if config.SOURCE_PROJECT:  # installed on a Mac: the file goes in the project, then install again
            where = (f"{config.SOURCE_PROJECT / 'secrets'} and run macos/install.sh again "
                     f"(or put it in {config.APP_DIR})")
        else:
            where = str(config.PROJECT_SECRETS_DIR)
        raise AuthError(
            f"credentials.json not found. Put the file from Google Cloud in "
            f"{where} (see 'Google Cloud setup' in the README)."
        )
    flow = InstalledAppFlow.from_client_secrets_file(
        str(config.CREDENTIALS_FILE), config.SCOPES
    )
    try:
        # prompt="consent" makes Google always return a refresh token,
        # otherwise a repeat login can come back without one.
        creds = flow.run_local_server(port=0, prompt="consent")
    except OAuth2Error as exc:
        raise AuthError(f"Login was refused or cancelled: {exc}") from exc
    except Warning as exc:  # oauthlib's way of saying a permission box was left unticked
        raise AuthError("Not every permission was granted. Log in again and tick all the boxes.") from exc
    _save_token(creds)
    return creds


def get_credentials():
    _pick_up_project_files()
    creds = _load_token()

    # Token was granted for other scopes than we need now.
    if creds and not set(config.SCOPES) <= set(creds.scopes or []):
        creds = None

    if creds and creds.valid:
        return creds

    if creds and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError:
            # Revoked, or expired (tokens last 7 days while the app is in Testing).
            return _login()
        _save_token(creds)
        return creds

    return _login()
