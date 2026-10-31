"""Settings changed in the app. They are saved as JSON in the per-user folder
and override the defaults in config.py, so changing them never touches code."""
import json

from . import config

_LISTS = {
    "vip_domains": "VIP_DOMAINS",
    "vip_senders": "VIP_SENDERS",
    "muted_domains": "MUTED_DOMAINS",
    "keywords": "KEYWORDS",
}


_DICTS = {"contacts": "CONTACTS", "websites": "WEBSITES"}


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _clean_list(items, lower):
    cleaned = []
    for item in items:
        if isinstance(item, str) and item.strip():
            item = item.strip().lower() if lower else item.strip()
            if item not in cleaned:
                cleaned.append(item)
    return cleaned


def _clean_dict(items, lower_keys):
    cleaned = {}
    for key, value in items.items():
        if isinstance(key, str) and isinstance(value, str) and key.strip() and value.strip():
            cleaned[key.strip().casefold() if lower_keys else key.strip()] = value.strip()
    return cleaned


def current():
    """The settings in effect right now."""
    values = {
        "poll_minutes": max(1, config.POLL_SECONDS // 60),
        "read_new_aloud": config.READ_NEW_ALOUD,
        "voice": config.VOICE,
        "speech_rate": config.SPEECH_RATE,
        "voice_engine": config.VOICE_ENGINE,
        "voice_sample": config.VOICE_SAMPLE,
        "voice_model": config.VOICE_MODEL,
        "voice_effect": config.VOICE_EFFECT,
        "dictation_language": config.DICTATION_LANGUAGE,
        "home_city": config.HOME_CITY,
        "music_folder": config.MUSIC_FOLDER,
        "wake_word": config.WAKE_WORD_ENABLED,
        "wake_sensitivity": config.WAKE_WORD_SENSITIVITY,
        "hotkey": config.HOTKEY_ENABLED,
        "address": config.ADDRESS,
        "hud_at_start": config.HUD_AT_START,
        "ai_key": config.AI_API_KEY,
        "ai_model": config.AI_MODEL,
        "ai_for_mail": config.AI_FOR_MAIL,
    }
    values.update({key: list(getattr(config, name)) for key, name in _LISTS.items()})
    values.update({key: dict(getattr(config, name)) for key, name in _DICTS.items()})
    return values


def apply(values):
    """Use the given settings. Unknown keys and values of the wrong type are ignored."""
    if _is_int(values.get("poll_minutes")):
        config.POLL_SECONDS = max(1, min(60, values["poll_minutes"])) * 60
    if isinstance(values.get("read_new_aloud"), bool):
        config.READ_NEW_ALOUD = values["read_new_aloud"]
    if isinstance(values.get("voice"), str):
        config.VOICE = values["voice"].strip()
    if _is_int(values.get("speech_rate")):
        config.SPEECH_RATE = max(-10, min(10, values["speech_rate"]))
    if values.get("voice_engine") in ("mac", "chatterbox"):
        config.VOICE_ENGINE = values["voice_engine"]
    if isinstance(values.get("voice_sample"), str):
        config.VOICE_SAMPLE = values["voice_sample"].strip()
    if values.get("voice_model") in ("auto", "nano", "turbo"):
        config.VOICE_MODEL = values["voice_model"]
    if values.get("voice_effect") in ("", "helmet"):
        config.VOICE_EFFECT = values["voice_effect"]
    language = values.get("dictation_language")
    if isinstance(language, str) and language.strip():
        config.DICTATION_LANGUAGE = language.strip()
    for key, name in _LISTS.items():
        if isinstance(values.get(key), list):
            setattr(config, name, _clean_list(values[key], lower=key != "keywords"))
    city = values.get("home_city")
    if isinstance(city, str) and city.strip():
        config.HOME_CITY = city.strip()
    if isinstance(values.get("music_folder"), str):
        config.MUSIC_FOLDER = values["music_folder"].strip()
    if isinstance(values.get("wake_word"), bool):
        config.WAKE_WORD_ENABLED = values["wake_word"]
    if isinstance(values.get("hotkey"), bool):
        config.HOTKEY_ENABLED = values["hotkey"]
    if values.get("wake_sensitivity") in ("low", "normal", "high"):
        config.WAKE_WORD_SENSITIVITY = values["wake_sensitivity"]
    if isinstance(values.get("address"), str):
        config.ADDRESS = values["address"].strip()
    if isinstance(values.get("hud_at_start"), bool):
        config.HUD_AT_START = values["hud_at_start"]
    if isinstance(values.get("ai_key"), str):
        config.AI_API_KEY = values["ai_key"].strip()
    if isinstance(values.get("ai_model"), str) and values["ai_model"].strip():
        config.AI_MODEL = values["ai_model"].strip()
    if isinstance(values.get("ai_for_mail"), bool):
        config.AI_FOR_MAIL = values["ai_for_mail"]
    for key, name in _DICTS.items():
        if isinstance(values.get(key), dict):
            setattr(config, name, _clean_dict(values[key], lower_keys=key == "websites"))


def load(path=None):
    """Apply settings.json if there is one. A broken file is reported and skipped."""
    path = path or config.SETTINGS_FILE
    try:
        values = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return
    except (OSError, ValueError) as exc:
        print(f"Could not read {path} ({exc}); using the defaults.")
        return
    if isinstance(values, dict):
        apply(values)


def save(values, path=None):
    """Apply the settings and write them to settings.json."""
    path = path or config.SETTINGS_FILE
    apply(values)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(current(), indent=2, ensure_ascii=False), encoding="utf-8")
