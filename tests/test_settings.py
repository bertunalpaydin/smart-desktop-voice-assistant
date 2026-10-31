import json

from jarvis import config, settings


def test_current_shows_what_is_in_effect():
    values = settings.current()
    assert values["poll_minutes"] == config.POLL_SECONDS // 60
    assert values["keywords"] == config.KEYWORDS
    assert values["read_new_aloud"] is config.READ_NEW_ALOUD


def test_save_applies_the_values_and_writes_them():
    settings.save({
        "poll_minutes": 10,
        "voice": " Hazel ",
        "vip_domains": ["UWE.ac.uk ", "", "uwe.ac.uk"],
        "keywords": ["Interview", " offer "],
    })
    assert config.POLL_SECONDS == 600
    assert config.VOICE == "Hazel"
    assert config.VIP_DOMAINS == ["uwe.ac.uk"]
    assert config.KEYWORDS == ["Interview", "offer"]
    saved = json.loads(config.SETTINGS_FILE.read_text(encoding="utf-8"))
    assert saved["poll_minutes"] == 10 and saved["vip_domains"] == ["uwe.ac.uk"]


def test_load_applies_a_saved_file():
    config.SETTINGS_FILE.write_text(json.dumps({"speech_rate": 4, "read_new_aloud": False}), encoding="utf-8")
    settings.load()
    assert config.SPEECH_RATE == 4
    assert config.READ_NEW_ALOUD is False


def test_wrong_types_and_unknown_keys_are_ignored():
    before = settings.current()
    settings.apply({
        "poll_minutes": "5", "speech_rate": True, "read_new_aloud": "yes",
        "keywords": "interview", "dictation_language": "  ", "unknown": 1,
    })
    assert settings.current() == before


def test_numbers_stay_in_range():
    settings.apply({"poll_minutes": 500, "speech_rate": -50})
    assert config.POLL_SECONDS == 3600
    assert config.SPEECH_RATE == -10
    settings.apply({"poll_minutes": 0})
    assert config.POLL_SECONDS == 60


def test_a_broken_file_is_reported_and_skipped(capsys):
    config.SETTINGS_FILE.write_text("{not json", encoding="utf-8")
    before = settings.current()
    settings.load()
    assert settings.current() == before
    assert "using the defaults" in capsys.readouterr().out


def test_no_file_means_the_defaults():
    before = settings.current()
    settings.load()
    assert settings.current() == before


def test_jarvis_settings_are_saved_and_cleaned():
    values = settings.current()
    values.update({
        "home_city": "  London ",
        "music_folder": " D:/Songs ",
        "wake_word": False,
        "hotkey": False,
        "contacts": {" Mum ": " mum@example.com ", "": "x", "Empty": "  "},
        "websites": {"UWE": " https://www.uwe.ac.uk/ "},
    })
    settings.save(values)
    assert (config.HOME_CITY, config.MUSIC_FOLDER) == ("London", "D:/Songs")
    assert (config.WAKE_WORD_ENABLED, config.HOTKEY_ENABLED) == (False, False)
    assert config.CONTACTS == {"Mum": "mum@example.com"}
    assert config.WEBSITES == {"uwe": "https://www.uwe.ac.uk/"}
    assert settings.current()["contacts"] == {"Mum": "mum@example.com"}


def test_bad_jarvis_values_are_ignored():
    before = settings.current()
    settings.apply({"home_city": "  ", "wake_word": "yes", "contacts": ["Mum"], "websites": None, "music_folder": 3})
    assert settings.current() == before


def test_wake_word_sensitivity():
    values = settings.current()
    assert values["wake_sensitivity"] == "normal"
    values["wake_sensitivity"] = "high"
    settings.save(values)
    assert config.WAKE_WORD_SENSITIVITY == "high"
    settings.apply({"wake_sensitivity": "extreme"})
    assert config.WAKE_WORD_SENSITIVITY == "high"


def test_hud_and_ai_settings():
    values = settings.current()
    assert (values["address"], values["hud_at_start"], values["ai_key"], values["ai_for_mail"]) == ("sir", True, "", False)
    values.update({"address": " Bertun ", "hud_at_start": False, "ai_key": " sk-ant-1 ", "ai_model": "claude-sonnet-5-5",
                   "ai_for_mail": True})
    settings.save(values)
    expected = ("Bertun", False, "sk-ant-1", "claude-sonnet-5-5", True)
    assert (config.ADDRESS, config.HUD_AT_START, config.AI_API_KEY, config.AI_MODEL, config.AI_FOR_MAIL) == expected
    settings.apply({"address": 5, "hud_at_start": "yes", "ai_key": None, "ai_model": "  ", "ai_for_mail": "no"})
    assert (config.ADDRESS, config.HUD_AT_START, config.AI_API_KEY, config.AI_MODEL, config.AI_FOR_MAIL) == expected


def test_human_voice_settings():
    values = settings.current()
    assert (values["voice_engine"], values["voice_sample"]) == ("mac", "")
    values.update({"voice_engine": "chatterbox", "voice_sample": " /voices/me.wav "})
    settings.save(values)
    assert (config.VOICE_ENGINE, config.VOICE_SAMPLE) == ("chatterbox", "/voices/me.wav")
    settings.apply({"voice_engine": "robot", "voice_sample": 7})
    assert (config.VOICE_ENGINE, config.VOICE_SAMPLE) == ("chatterbox", "/voices/me.wav")
    assert config.VOICE_MODEL == "auto"
    settings.apply({"voice_model": "turbo"})
    settings.apply({"voice_model": "huge"})
    assert config.VOICE_MODEL == "turbo"


def test_voice_effect_setting():
    assert settings.current()["voice_effect"] == ""
    settings.apply({"voice_effect": "helmet"})
    settings.apply({"voice_effect": "chipmunk"})
    assert config.VOICE_EFFECT == "helmet"
