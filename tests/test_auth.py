from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from oauthlib.oauth2.rfc6749.errors import AccessDeniedError

from jarvis import auth, config


@pytest.fixture(autouse=True)
def folders(tmp_path, monkeypatch):
    """(app folder, project secrets folder), both empty."""
    app, project = tmp_path / "app", tmp_path / "project_secrets"
    app.mkdir()
    project.mkdir()
    monkeypatch.setattr(config, "CREDENTIALS_FILE", app / "credentials.json")
    monkeypatch.setattr(config, "TOKEN_FILE", app / "token.json")
    monkeypatch.setattr(config, "PROJECT_SECRETS_DIR", project)
    return app, project


def in_an_hour():
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)


def make_creds(expired=False, scopes=None):
    expiry = in_an_hour() - timedelta(hours=2) if expired else in_an_hour()
    return Credentials(
        token="access",
        refresh_token="refresh",
        token_uri="https://oauth2.googleapis.com/token",
        client_id="id",
        client_secret="secret",
        scopes=scopes or config.SCOPES,
        expiry=expiry,
    )


def save(creds):
    config.TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")


def forbid_login(monkeypatch):
    def fail():
        raise AssertionError("should not have asked for a new login")
    monkeypatch.setattr(auth, "_login", fail)


def fake_login(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(auth, "_login", lambda: sentinel)
    return sentinel


def fake_flow(monkeypatch, flow):
    monkeypatch.setattr(
        auth.InstalledAppFlow, "from_client_secrets_file", lambda path, scopes: flow
    )


def test_valid_token_is_reused(monkeypatch):
    save(make_creds())
    forbid_login(monkeypatch)
    creds = auth.get_credentials()
    assert creds.valid
    assert creds.token == "access"


def test_expired_token_is_refreshed_and_saved(monkeypatch):
    save(make_creds(expired=True))
    forbid_login(monkeypatch)

    def refresh(self, request):
        self.token = "new-access"
        self.expiry = in_an_hour()

    monkeypatch.setattr(Credentials, "refresh", refresh)
    creds = auth.get_credentials()
    assert creds.token == "new-access"
    assert "new-access" in config.TOKEN_FILE.read_text(encoding="utf-8")


def test_refresh_failure_falls_back_to_login(monkeypatch):
    save(make_creds(expired=True))

    def refresh(self, request):
        raise RefreshError("invalid_grant")

    monkeypatch.setattr(Credentials, "refresh", refresh)
    sentinel = fake_login(monkeypatch)
    assert auth.get_credentials() is sentinel


def test_token_with_other_scopes_triggers_login(monkeypatch):
    save(make_creds(scopes=["https://www.googleapis.com/auth/userinfo.email"]))
    sentinel = fake_login(monkeypatch)
    assert auth.get_credentials() is sentinel


@pytest.mark.parametrize("content", ["{not json", "{}"])
def test_broken_token_file_triggers_login(monkeypatch, content):
    config.TOKEN_FILE.write_text(content, encoding="utf-8")
    sentinel = fake_login(monkeypatch)
    assert auth.get_credentials() is sentinel


def test_missing_credentials_file_raises_auth_error():
    with pytest.raises(auth.AuthError, match="credentials.json"):
        auth.get_credentials()


def test_login_saves_token_and_asks_for_consent(monkeypatch):
    config.CREDENTIALS_FILE.write_text("{}", encoding="utf-8")
    seen = {}

    class Flow:
        def run_local_server(self, **kwargs):
            seen.update(kwargs)
            return make_creds()

    fake_flow(monkeypatch, Flow())
    creds = auth.get_credentials()
    assert seen == {"port": 0, "prompt": "consent"}
    assert creds.token == "access"
    assert config.TOKEN_FILE.exists()


def test_refused_login_becomes_auth_error(monkeypatch):
    config.CREDENTIALS_FILE.write_text("{}", encoding="utf-8")

    class Flow:
        def run_local_server(self, **kwargs):
            raise AccessDeniedError()

    fake_flow(monkeypatch, Flow())
    with pytest.raises(auth.AuthError):
        auth.get_credentials()


def test_credentials_put_in_the_project_are_copied_for_the_app(monkeypatch, folders):
    _, project = folders
    (project / "credentials.json").write_text('{"installed": {}}', encoding="utf-8")
    fake_login(monkeypatch)
    auth.get_credentials()
    assert config.CREDENTIALS_FILE.read_text(encoding="utf-8") == '{"installed": {}}'


def test_a_new_credentials_file_in_the_project_replaces_the_old_copy(monkeypatch, folders):
    _, project = folders
    config.CREDENTIALS_FILE.write_text("old", encoding="utf-8")
    (project / "credentials.json").write_text("new", encoding="utf-8")
    fake_login(monkeypatch)
    auth.get_credentials()
    assert config.CREDENTIALS_FILE.read_text(encoding="utf-8") == "new"


def test_a_login_an_older_version_kept_in_the_project_is_reused(monkeypatch, folders):
    _, project = folders
    (project / "token.json").write_text(make_creds().to_json(), encoding="utf-8")
    forbid_login(monkeypatch)
    assert auth.get_credentials().token == "access"
    assert config.TOKEN_FILE.exists()


def test_the_current_login_wins_over_an_old_one_in_the_project(monkeypatch, folders):
    _, project = folders
    save(make_creds())
    old = make_creds()
    old.token = "old-access"
    (project / "token.json").write_text(old.to_json(), encoding="utf-8")
    forbid_login(monkeypatch)
    assert auth.get_credentials().token == "access"


def test_a_copy_that_fails_is_reported_as_a_login_problem(monkeypatch, folders):
    _, project = folders
    (project / "credentials.json").write_text("x", encoding="utf-8")

    def denied(source, target):
        raise PermissionError("denied")

    monkeypatch.setattr(auth.shutil, "copyfile", denied)
    with pytest.raises(auth.AuthError, match="Could not copy"):
        auth.get_credentials()


def test_an_unticked_permission_is_a_clear_login_problem(monkeypatch):
    config.CREDENTIALS_FILE.write_text("{}", encoding="utf-8")

    class Flow:
        def run_local_server(self, **kwargs):
            raise Warning('Scope has changed from "read send" to "read".')

    fake_flow(monkeypatch, Flow())
    with pytest.raises(auth.AuthError, match="tick all the boxes"):
        auth.get_credentials()


def test_on_a_mac_the_message_names_the_project_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SOURCE_PROJECT", tmp_path / "Documents" / "Jarvis")
    with pytest.raises(auth.AuthError) as error:
        auth.get_credentials()
    message = str(error.value)
    assert str(tmp_path / "Documents" / "Jarvis" / "secrets") in message and "run macos/install.sh again" in message
    assert str(config.APP_DIR) in message


def test_the_project_folder_install_sh_noted(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "ROOT", tmp_path)
    assert config._source_project() is None  # not installed by install.sh
    (tmp_path / "source.txt").write_text("/Users/me/Documents/Jarvis\n", encoding="utf-8")
    assert config._source_project() == Path("/Users/me/Documents/Jarvis")


def test_the_login_and_the_key_are_private(tmp_path, monkeypatch):
    import os
    import stat

    if os.name != "posix":
        return
    token = tmp_path / "token.json"
    monkeypatch.setattr(config, "TOKEN_FILE", token)

    class Creds:
        def to_json(self):
            return "{}"

    auth._save_token(Creds())
    assert stat.S_IMODE(token.stat().st_mode) == 0o600
    source = tmp_path / "credentials.json"
    source.write_text("{}")
    auth._copy(source, tmp_path / "copy" / "credentials.json")
    assert stat.S_IMODE((tmp_path / "copy" / "credentials.json").stat().st_mode) == 0o600
