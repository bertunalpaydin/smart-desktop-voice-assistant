from jarvis import config


def test_old_mail_assistant_folder_is_brought_over_once():
    config.OLD_APP_DIR.mkdir(parents=True)
    (config.OLD_APP_DIR / "token.json").write_text("login", encoding="utf-8")
    (config.OLD_APP_DIR / "app.lock").write_text("", encoding="utf-8")
    (config.OLD_APP_DIR / "app.log").write_text("old log", encoding="utf-8")
    config.adopt_old_app_dir()
    assert (config.APP_DIR / "token.json").read_text(encoding="utf-8") == "login"
    assert not (config.APP_DIR / "app.lock").exists()
    assert not (config.APP_DIR / "app.log").exists()

    (config.OLD_APP_DIR / "token.json").write_text("older", encoding="utf-8")
    config.adopt_old_app_dir()  # the new folder exists now, so nothing is copied again
    assert (config.APP_DIR / "token.json").read_text(encoding="utf-8") == "login"


def test_nothing_happens_without_an_old_folder():
    config.adopt_old_app_dir()
    assert not config.APP_DIR.exists()
