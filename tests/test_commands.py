from datetime import datetime, timedelta

import pytest

from jarvis import ai, alarms, analysis, commands, config, dictation, system, web
from jarvis.models import Mail
from jarvis.web import NewsItem
from jarvis.powershell import PowerShellError

from fakes import weather_sample

NOW = datetime(2026, 10, 1, 14, 0)  # a Thursday afternoon


class FakeSystem:
    def __init__(self, apps=None, music=None, broken_links=()):
        self.log = []
        self.apps = {"Spotify": "spotify-id", "Visual Studio Code": "code-id", "Google Chrome": "chrome-id",
                     "WhatsApp": "whatsapp-id", "Discord": "discord-id"} if apps is None else apps
        self.music = music
        self.broken_links = broken_links

    def open_url(self, url):
        self.log.append(("open", url))
        return not url.startswith(self.broken_links) if self.broken_links else True

    def press(self, key, times=1):
        self.log.append(("press", key, times))

    def lock(self):
        self.log.append(("lock",))

    def start(self, target):
        self.log.append(("start", target))

    def installed_apps(self):
        if isinstance(self.apps, Exception):
            raise self.apps
        return self.apps

    def launch_app(self, app_id):
        self.log.append(("launch", app_id))

    def play_random_music(self, folder):
        self.log.append(("music", folder))
        return self.music

    def close_app(self, name):
        self.log.append(("close", name))
        return 0 if name == "nothing open" else 1

    def set_volume(self, percent):
        self.log.append(("volume", percent))

    def open_folder(self, name):
        self.log.append(("folder", name))
        return f"C:/Users/me/{name}"


class FakeWeb:
    youtube_search_url = staticmethod(web.youtube_search_url)
    google_url = staticmethod(web.google_url)

    def __init__(self, video="https://www.youtube.com/watch?v=abcdefghijk", summary="A short summary.",
                 weather="It's sunny.", error=None):
        self.video, self.summary, self.report, self.error = video, summary, weather, error
        self.asked = []

    def _answer(self, value, question):
        self.asked.append(question)
        if self.error:
            raise self.error
        return value

    def youtube_first_video(self, query):
        return self._answer(self.video, query)

    def wikipedia_summary(self, query):
        return self._answer(self.summary, query)

    def weather(self, city):
        return self._answer(self.report, city)

    def news_items(self):
        return self._answer([
            NewsItem("Storm Amy hits Scotland", "Winds of 100mph.", "https://bbc/1", "UK", "BBC News", 0),
            NewsItem("Storm Amy batters Scotland", "", "https://sky/1", "News", "Sky News", 0),
            NewsItem("Budget plans unveiled", "", "https://bbc/2", "Politics", "BBC News", 1),
        ], "news")

    def weather_data(self, city):
        if self.report is None:
            return self._answer(None, city)
        return self._answer(weather_sample(city.title(), NOW), city)

    def place_time(self, city):
        if self.report is None:
            return self._answer(None, city)
        return self._answer((city.title(), NOW + timedelta(hours=8)), city)


def mail(id, sender, subject, important=False):
    name, _, address = sender.partition("|")
    return Mail(id, name, address, subject, "preview", NOW, important=important)


class FakeGmail:
    def __init__(self, mails=(), inbox=None):
        self.mails = list(mails)
        self.inbox = list(mails) if inbox is None else list(inbox)  # newest first, read or not
        self.asked = []

    def unread(self, limit=15, skip_ids=()):
        return list(self.mails)

    def latest(self, limit=1):
        self.asked.append(limit)
        return self.inbox[:limit]

    def body(self, mail_id):
        return f"Body of {mail_id}."


def make(gmail=None, sys_api=None, web_api=None, heard=(), **kwargs):
    said, answers = [], list(heard)

    def listen(**kwargs):
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    assistant = commands.Assistant(
        gmail=(lambda: gmail) if gmail is not None else None,
        speak=lambda text, **kw: said.append(text),
        listen=listen,
        alarm_store=alarms.AlarmStore(config.ALARMS_FILE),
        now=lambda: NOW,
        system_api=sys_api or FakeSystem(),
        web_api=web_api or FakeWeb(),
        **kwargs,
    )
    return assistant, said


def intent(text):
    """The command's meaning when no alarm is ringing."""
    command = commands.normalise(text)
    skip = ("alarm_stop", "alarm_snooze")
    return next((name for name, pattern in commands._PATTERNS if name not in skip and pattern.search(command)), None)


@pytest.mark.parametrize("text, expected", [
    ("Hey Jarvis, what time is it?", "what time is it"),
    ("Jarvis can you please play some music.", "play some music"),
    ("Set an alarm for 7 a.m.", "set an alarm for 7 am"),
    ("Email ali@gmail.com please", "email ali@gmail.com"),
    ("Wake me up at 6.30!", "wake me up at 6.30"),
])
def test_normalise(text, expected):
    assert commands.normalise(text) == expected


@pytest.mark.parametrize("text, name", [
    ("what time is it", "time"),
    ("what's the time", "time"),
    ("what's the date today", "date"),
    ("what day is it", "date"),
    ("hello", "greeting"),
    ("thank you Jarvis", "thanks"),
    ("tell me a joke", "joke"),
    ("what can you do", "help"),
    ("never mind", "cancel"),
    ("set a timer for 10 minutes", "timer"),
    ("10 minute timer", "timer"),
    ("set an alarm for 7 am", "alarm"),
    ("wake me up at 6:30", "alarm"),
    ("remind me to call mum in 20 minutes", "reminder"),
    ("what alarms do I have", "alarms_list"),
    ("do I have any reminders", "alarms_list"),
    ("cancel my alarms", "alarms_cancel"),
    ("delete all timers", "alarms_cancel"),
    ("check my email", "mail_check"),
    ("do I have any new emails", "mail_check"),
    ("read my emails", "mail_read"),
    ("read my important email", "mail_read"),
    ("send an email to Ali", "mail_send"),
    ("write a message to mum", "mail_send"),
    ("email Ali", "mail_send"),
    ("open my inbox", "inbox"),
    ("play Bohemian Rhapsody", "play"),
    ("play lofi on spotify", "play"),
    ("play music", "play_music"),
    ("play some music", "play_music"),
    ("play", "media_resume"),
    ("resume the music", "media_resume"),
    ("pause", "media_pause"),
    ("pause the song", "media_pause"),
    ("stop the music", "media_stop"),
    ("stop", "media_stop"),
    ("next song", "media_next"),
    ("skip this song", "media_next"),
    ("previous track", "media_previous"),
    ("turn it up", "volume_up"),
    ("volume down", "volume_down"),
    ("mute", "mute"),
    ("search for python decorators", "search"),
    ("google the best laptops", "search"),
    ("search youtube for lofi", "youtube_search"),
    ("cat videos on youtube", "youtube_search"),
    ("what's the weather", "weather"),
    ("is it going to rain tomorrow", "weather"),
    ("what's the weather like in London", "weather"),
    ("who is Alan Turing", "wiki"),
    ("what is machine learning", "wiki"),
    ("tell me about the moon", "wiki"),
    ("take a note buy milk", "note_add"),
    ("remember that the exam is on Monday", "note_add"),
    ("read my notes", "note_read"),
    ("take a screenshot", "screenshot"),
    ("lock the computer", "lock"),
    ("open settings", "open"),  # Windows settings
    ("open jarvis settings", "settings"),
    ("close spotify", "close"),
    ("set the volume to 40", "volume_set"),
    ("volume 60 percent", "volume_set"),
    ("what's the news", "news"),
    ("read me the headlines", "news"),
    ("what's 25 times 4", "calculate"),
    ("how long is left on my timer", "alarms_list"),
    ("open spotify", "open"),
    ("launch visual studio code", "open"),
    ("go to github", "open"),
    ("make me a sandwich", None),
])
def test_what_each_command_means(text, name):
    assert intent(text) == name


def test_time_date_and_small_talk():
    jarvis, _ = make()
    assert jarvis.handle("What time is it?").say == "It's 2:00 PM."
    assert jarvis.handle("what's the date").say == "Today is Thursday, 1 October 2026."
    assert jarvis.handle("hello").say == "Good afternoon, sir. What can I do for you?"
    assert jarvis.handle("tell me a joke").say in commands.JOKES
    assert "alarms" in jarvis.handle("help").say
    assert jarvis.handle("").say == "Sorry, I didn't catch that."
    assert "don't know how" in jarvis.handle("make me a sandwich").say


def test_timer():
    jarvis, _ = make()
    assert jarvis.handle("set a timer for an hour and a half").say == "Timer set for 1 hour and 30 minutes."
    assert jarvis.handle("set a timer for 5").say == "Timer set for 5 minutes."
    assert "How long" in jarvis.handle("set a timer").say
    timers = jarvis.alarms.all()
    assert [t.due for t in timers] == [NOW + timedelta(minutes=5), NOW + timedelta(minutes=90)]
    assert timers[0].text == "5 minutes"


def test_alarms():
    jarvis, _ = make()
    assert jarvis.handle("set an alarm for 7 am").say == "Alarm set for 7:00 AM tomorrow."
    assert jarvis.handle("wake me up at half past six").say == "Alarm set for 6:30 AM tomorrow."  # morning
    assert jarvis.handle("set an alarm for 6:30 pm").say == "Alarm set for 6:30 PM."
    assert jarvis.handle("set an alarm in 20 minutes").say == "Alarm set for 2:20 PM."
    assert "For when" in jarvis.handle("set an alarm").say
    assert len(jarvis.alarms.all()) == 4


@pytest.mark.parametrize("text, what, due", [
    ("remind me to call Mum in 20 minutes", "call Mum", NOW + timedelta(minutes=20)),
    ("Remind me to put the cake in the oven at 5 pm", "put the cake in the oven", NOW.replace(hour=17)),
    ("remind me at 6 pm to go to the gym", "go to the gym", NOW.replace(hour=18)),
    ("remind me to email Dr Smith tomorrow at 9", "email Dr Smith", NOW.replace(hour=9) + timedelta(days=1)),
    ("remind me in half an hour to stretch", "stretch", NOW + timedelta(minutes=30)),
])
def test_reminders_keep_your_words(text, what, due):
    jarvis, _ = make()
    reply = jarvis.handle(text)
    reminder = jarvis.alarms.all()[0]
    assert (reminder.kind, reminder.text, reminder.due) == ("reminder", what, due)
    assert reply.say.startswith(f"Okay, I'll remind you to {what} at ")


def test_a_reminder_without_a_time():
    jarvis, _ = make()
    assert "Say something like" in jarvis.handle("remind me to call mum").say
    assert jarvis.alarms.all() == []


def test_listing_and_cancelling_alarms():
    jarvis, _ = make()
    assert jarvis.handle("what alarms do I have").say == "You have no alarms, timers or reminders."
    jarvis.handle("set a timer for 10 minutes")
    jarvis.handle("remind me to stretch in 1 hour")
    reply = jarvis.handle("what are my alarms")
    assert reply.action == "alarms"
    assert reply.say == ("You have 2: a timer for 10 minutes, ending at 2:10 PM (10 minutes left); "
                         "a reminder to stretch at 3:00 PM.")
    assert jarvis.handle("cancel my timers").say == "Cancelled 1 timer."
    assert jarvis.handle("cancel my timers").say == "There were no timers to cancel."
    assert jarvis.handle("cancel all alarms").say == "There were no alarms to cancel."
    assert jarvis.handle("clear my reminders").say == "Cancelled 1 reminder."


def test_playing_on_youtube():
    sys_api, web_api = FakeSystem(), FakeWeb()
    jarvis, _ = make(sys_api=sys_api, web_api=web_api)
    assert jarvis.handle("Play Bohemian Rhapsody").say == "Playing Bohemian Rhapsody on YouTube."
    assert web_api.asked == ["Bohemian Rhapsody"]
    assert sys_api.log == [("open", "https://www.youtube.com/watch?v=abcdefghijk")]


@pytest.mark.parametrize("web_api", [FakeWeb(video=None), FakeWeb(error=web.WebError("offline"))])
def test_playing_falls_back_to_youtube_search(web_api):
    sys_api = FakeSystem()
    jarvis, _ = make(sys_api=sys_api, web_api=web_api)
    assert jarvis.handle("play lofi beats").say == "Here's what YouTube found for lofi beats."
    assert sys_api.log == [("open", "https://www.youtube.com/results?search_query=lofi+beats")]


def test_playing_on_spotify_uses_the_app_or_the_website():
    sys_api = FakeSystem()
    jarvis, _ = make(sys_api=sys_api)
    assert jarvis.handle("play lofi beats on spotify").say == "Searching Spotify for lofi beats."
    assert sys_api.log == [("open", "spotify:search:lofi%20beats")]
    sys_api = FakeSystem(broken_links=("spotify:",))  # Spotify isn't installed
    jarvis, _ = make(sys_api=sys_api)
    jarvis.handle("play lofi on spotify")
    assert sys_api.log[-1] == ("open", "https://open.spotify.com/search/lofi")


def test_playing_your_own_music(monkeypatch):
    monkeypatch.setattr(config, "MUSIC_FOLDER", "D:/Songs")
    sys_api = FakeSystem(music="Song One")
    jarvis, _ = make(sys_api=sys_api)
    assert jarvis.handle("play some music").say == "Playing Song One."
    assert sys_api.log == [("music", "D:/Songs")]
    sys_api = FakeSystem(music=None)
    jarvis, _ = make(sys_api=sys_api)
    assert "YouTube Music" in jarvis.handle("play music").say
    assert sys_api.log[-1] == ("open", "https://music.youtube.com/")


@pytest.mark.parametrize("text, key, times", [
    ("pause", "play_pause", 1), ("resume", "play_pause", 1), ("stop the music", "stop", 1),
    ("next song", "next", 1), ("previous song", "previous", 1), ("turn it up", "volume_up", 5),
    ("quieter", "volume_down", 5), ("mute", "mute", 1),
])
def test_media_and_volume_keys(text, key, times):
    sys_api = FakeSystem()
    jarvis, _ = make(sys_api=sys_api)
    jarvis.handle(text)
    assert sys_api.log == [("press", key, times)]


@pytest.mark.parametrize("text, expected_log, reply", [
    ("open calculator", [("start", "calc.exe")], "Opening calculator."),
    ("open the settings app", [("start", "ms-settings:")], "Opening settings."),
    ("can you open up WhatsApp for me", [("launch", "whatsapp-id")], "Opening WhatsApp."),
    ("open what's up", [("launch", "whatsapp-id")], "Opening WhatsApp."),
    ("open discord app now", [("launch", "discord-id")], "Opening Discord."),
    ("open microsoft edge browser", [("start", "microsoft-edge:")], "Opening microsoft edge."),
    ("open spotify", [("launch", "spotify-id")], "Opening Spotify."),
    ("open google", [("open", "https://www.google.com/")], "Opening google."),
    ("open chrome", [("launch", "chrome-id")], "Opening Google Chrome."),
    ("launch vs code", [("launch", "code-id")], "Opening Visual Studio Code."),
    ("open blackboard", [("open", "https://blackboard.uwe.ac.uk/")], "Opening blackboard."),
    ("go to bbc dot co dot uk", [("open", "https://bbc.co.uk")], "Opening bbc.co.uk."),
    ("open Photoshop", [("open", "https://www.google.com/search?q=Photoshop")],
     "I couldn't find an app called Photoshop, so I searched the web for it."),
])
def test_opening_things(text, expected_log, reply):
    sys_api = FakeSystem()
    jarvis, _ = make(sys_api=sys_api)
    assert jarvis.handle(text).say == reply
    assert sys_api.log == expected_log


def test_opening_still_works_when_the_app_list_fails(monkeypatch):
    monkeypatch.setitem(config.WEBSITES, "uni", "https://www.uwe.ac.uk/")
    sys_api = FakeSystem(apps=PowerShellError("Get-StartApps failed"))
    jarvis, _ = make(sys_api=sys_api)
    assert jarvis.handle("open uni").say == "Opening uni."
    assert jarvis.handle("open spotify").say == "Opening spotify."  # the website instead
    assert sys_api.log[-1] == ("open", "https://open.spotify.com/")


def test_searching():
    sys_api = FakeSystem()
    jarvis, _ = make(sys_api=sys_api)
    assert jarvis.handle("search for Python decorators").say == "Here's what Google found for Python decorators."
    assert jarvis.handle("search youtube for lofi").say == "Here's what YouTube found for lofi."
    assert sys_api.log == [("open", "https://www.google.com/search?q=Python+decorators"),
                           ("open", "https://www.youtube.com/results?search_query=lofi")]


def test_wikipedia():
    web_api = FakeWeb(summary="Alan Turing was a mathematician.")
    jarvis, _ = make(web_api=web_api)
    assert jarvis.handle("who was Alan Turing").say == "Alan Turing was a mathematician."
    assert jarvis.handle("what is a black hole").say == "Alan Turing was a mathematician."
    assert web_api.asked == ["alan turing", "black hole"]
    sys_api = FakeSystem()
    jarvis, _ = make(sys_api=sys_api, web_api=FakeWeb(summary=None))
    assert "searched the web" in jarvis.handle("who is xyzzy").say
    assert sys_api.log == [("open", "https://www.google.com/search?q=xyzzy")]


def test_weather(monkeypatch):
    monkeypatch.setattr(config, "HOME_CITY", "Bristol")
    web_api = FakeWeb()
    jarvis, _ = make(web_api=web_api)
    assert jarvis.handle("what's the weather").say == (
        "Right now in Bristol it's 14 degrees with light rain. Today's high is 15 and the low is 9. "
        "There's a 70 percent chance of rain.")
    assert jarvis.handle("what's the weather like in New York today").say.startswith("Right now in New York it's")
    assert web_api.asked == ["Bristol", "new york"]
    jarvis, _ = make(web_api=FakeWeb(weather=None))
    assert jarvis.handle("weather in Atlantis").say == "I couldn't find a place called Atlantis."
    assert jarvis.handle("what's the weather").say == (
        "I couldn't find your city, Bristol. Check it in Jarvis's settings.")


@pytest.mark.parametrize("text, said", [
    # the day that was asked about, not today (NOW is a Thursday)
    ("What's the weather tomorrow?",
     "Tomorrow in Bristol: overcast skies, between 8 and 13 degrees, with a 20 percent chance of rain."),
    ("what will the weather be like tomorrow in London",
     "Tomorrow in London: overcast skies, between 8 and 13 degrees, with a 20 percent chance of rain."),
    ("weather in Paris on Saturday",
     "On Saturday in Paris: partly cloudy skies, between 10 and 16 degrees, with a 10 percent chance of rain."),
    ("what's the weather the day after tomorrow",
     "On Saturday in Bristol: partly cloudy skies, between 10 and 16 degrees, with a 10 percent chance of rain."),
    ("what's the weather like next Thursday",
     "On Thursday 8 October in Bristol: mostly clear skies, between 9 and 15 degrees, with a 5 percent chance of rain."),
    ("weather tomorrow morning",
     "Tomorrow morning in Bristol: overcast skies, between 8 and 10 degrees, with a 10 percent chance of rain."),
    ("what's the weather like tonight",
     "Tonight in Bristol: overcast skies, between 9 and 12 degrees, with a 35 percent chance of rain."),
    ("will it rain tomorrow", "Probably not. There's only a 20 percent chance of rain in Bristol tomorrow."),
    ("is it going to rain on Sunday", "Yes, probably. There's a 90 percent chance of rain in Bristol on Sunday."),
    ("will it rain this afternoon", "Yes, probably. There's a 70 percent chance of rain in Bristol this afternoon."),
    ("is it raining", "Yes, it's raining in Bristol right now: light rain."),
    ("do I need an umbrella tomorrow",
     "You shouldn't need one. There's only a 20 percent chance of rain in Bristol tomorrow."),
    ("will it snow on Monday", "Yes, snow is expected in Bristol on Monday: light snow, between -1 and 3 degrees."),
    ("how windy will it be on Sunday", "On Sunday in Bristol, the wind will reach 25 miles per hour."),
    ("when is sunset", "The sun sets in Bristol at 6:40 PM today."),
    ("what time is sunrise tomorrow", "Tomorrow, the sun rises in Bristol at 7:11 AM."),
    ("what was the weather yesterday", "I can only tell you the weather from today on."),
    ("what's the weather in 10 days", "I can only see 7 days ahead."),
])
def test_weather_for_the_day_asked(monkeypatch, text, said):
    monkeypatch.setattr(config, "HOME_CITY", "Bristol")
    jarvis, _ = make()
    assert jarvis.handle(text).say == said


def test_weather_for_the_weekend_and_the_week(monkeypatch):
    monkeypatch.setattr(config, "HOME_CITY", "Bristol")
    jarvis, _ = make()
    assert jarvis.handle("what's the weather like this weekend").say == (
        "Here's the weekend in Bristol. Saturday: partly cloudy skies, 10 to 16 degrees. "
        "Sunday: rain, 7 to 12 degrees, 90 percent chance of rain.")
    week = jarvis.handle("what's the forecast for this week").say
    assert week.startswith("Here's the next 7 days in Bristol. Today: light rain, 9 to 15 degrees")
    assert "Monday: light snow, -1 to 3 degrees, 40 percent chance of snow." in week
    assert jarvis.handle("will it rain this weekend").say == (
        "The chance of rain in Bristol over the weekend: Saturday, 10 percent; Sunday, 90 percent.")
    assert jarvis.handle("is it going to snow in the next few days").say == (
        "No snow is expected in Bristol over the next few days.")


@pytest.mark.parametrize("text, place", [
    ("what's the weather in London tomorrow", "london"),
    ("will it rain in London in the afternoon", "london"),
    ("London weather", "london"),
    ("what's the New York weather like", "new york"),
    ("weather for the next 3 days", "Bristol"),
    ("what's the weather like at the moment", "Bristol"),
    ("what's the weather like in my area", "Bristol"),
    ("is it cold outside", "Bristol"),
    ("what's the weather in Bristol UK", "bristol uk"),
])
def test_where_the_weather_is_for(monkeypatch, text, place):
    monkeypatch.setattr(config, "HOME_CITY", "Bristol")
    web_api = FakeWeb()
    jarvis, _ = make(web_api=web_api)
    jarvis.handle(text)
    assert web_api.asked == [place]


@pytest.mark.parametrize("text", ["who sang Purple Rain", "who sang Umbrella", "what causes rain",
                                  "what's the temperature of the sun", "play rain sounds", "open the weather app",
                                  "does it rain a lot in London", "what's my CPU temperature"])
def test_not_weather_questions(text):
    web_api, looked_up = FakeWeb(), []
    web_api.weather_data = looked_up.append
    jarvis, _ = make(web_api=web_api, sys_api=FakeSystem(apps={"Weather": "weather-id"}))
    jarvis.handle(text)
    assert looked_up == []


def test_no_internet_is_said_politely_with_the_reason():
    jarvis, _ = make(web_api=FakeWeb(error=web.WebError("api.open-meteo.com: timed out", "it took too long to answer")))
    assert jarvis.handle("what's the weather").say == (
        "I couldn't get that from the internet, because it took too long to answer. Try again in a moment.")


def test_windows_only_commands_elsewhere():
    class NotWindows(FakeSystem):
        def press(self, key, times=1):
            raise system.Unavailable("That only works on Windows.")

    jarvis, _ = make(sys_api=NotWindows())
    assert jarvis.handle("pause").say == "That only works on Windows."


def test_notes_keep_your_capitals():
    jarvis, _ = make()
    assert jarvis.handle("read my notes").say == "You don't have any notes yet."
    assert jarvis.handle("Take a note: Exam on Monday in 3Q12").say == "Noted."
    assert jarvis.handle("read my notes").say == "Your latest notes: Exam on Monday in 3Q12."


def test_things_only_the_window_can_do():
    sys_api = FakeSystem()
    jarvis, _ = make(sys_api=sys_api)
    assert jarvis.handle("take a screenshot").action == "screenshot"
    assert jarvis.handle("open my inbox").action == "inbox"
    assert jarvis.handle("open jarvis settings").action == "settings"
    assert jarvis.handle("lock the computer").say == "Locking the computer."
    assert sys_api.log == [("lock",)]


IMPORTANT = mail("1", "Recruiter|jobs@company.com", "Interview next steps")
OTHER = mail("2", "Shop|deals@shop.com", "Sale")


def test_checking_and_reading_mail(monkeypatch):
    monkeypatch.setattr(config, "KEYWORDS", ["interview"])
    jarvis, _ = make(gmail=FakeGmail([IMPORTANT, OTHER]))
    assert jarvis.handle("check my email").say == "You have 2 unread emails, 1 of them important."
    reply = jarvis.handle("read my emails")
    assert reply.say == "You have 1 important unread email."
    assert reply.more == ("Email from Recruiter. Subject: Interview next steps. Body of 1.",)
    jarvis, _ = make(gmail=FakeGmail([]))
    assert jarvis.handle("any new emails").say == "You have no unread email."
    jarvis, _ = make(gmail=FakeGmail([OTHER] * config.MAX_UNREAD))
    assert jarvis.handle("check my inbox").say.startswith(f"You have at least {config.MAX_UNREAD} unread emails, none")


def test_mail_without_gmail_set_up():
    jarvis, _ = make(gmail=None)
    assert jarvis.handle("check my email").say == "Mail isn't set up here."


def test_writing_an_email_by_voice_opens_it_for_checking(monkeypatch):
    monkeypatch.setattr(config, "CONTACTS", {"Mum": "mum@example.com"})
    jarvis, said = make(gmail=FakeGmail(), heard=["Sunday lunch", "See you at one"])
    reply = jarvis.handle("send an email to my mum", voice=True)
    assert said == ["What's the subject?", "What should it say?"]
    assert reply.action == "compose" and reply.say == "Here it is. Check it and press Send."
    assert (reply.data.to, reply.data.subject, reply.data.body) == ("mum@example.com", "Sunday lunch", "See you at one")


def test_writing_an_email_when_typed_asks_nothing():
    jarvis, said = make(gmail=FakeGmail(), heard=["should not be used"])
    reply = jarvis.handle("email ali@example.com")
    assert said == [] and reply.data.to == "ali@example.com" and reply.data.body == ""


@pytest.mark.parametrize("who, address", [
    ("ali at gmail dot com", "ali@gmail.com"),  # spelled out
    ("Recruiter", "jobs@company.com"),  # someone who emailed you
    ("ayse", "ayse.k@uni.ac.uk"),  # first name of a contact
])
def test_finding_the_address(monkeypatch, who, address):
    monkeypatch.setattr(config, "CONTACTS", {"Ayse Kaya": "ayse.k@uni.ac.uk"})
    jarvis, _ = make(gmail=FakeGmail([IMPORTANT]))
    assert jarvis.handle(f"send an email to {who}").data.to == address


def test_unknown_address_and_unheard_answers():
    jarvis, _ = make(gmail=FakeGmail(), heard=[dictation.DictationError("silence"), "Hello"])
    reply = jarvis.handle("send an email to Bob", voice=True)
    assert reply.say == "I don't have an address for Bob, so type it in the window."
    assert (reply.data.to, reply.data.subject, reply.data.body) == ("", "", "Hello")



# --- what was fixed and added after the first real use ----------------------------

@pytest.mark.parametrize("text, minutes", [
    ("set an alarm for 1 minute", 1),
    ("set an alarm for one minute", 1),
    ("Set alarm 1 minute from now", 1),
    ("set an alarm after 5 minutes", 5),
    ("alarm in half an hour", 30),
    ("wake me up in 8 hours", 480),
])
def test_an_alarm_for_a_length_of_time_starts_now(text, minutes):
    jarvis, _ = make()
    jarvis.handle(text)
    assert jarvis.alarms.all()[0].due == NOW + timedelta(minutes=minutes)


def test_an_alarm_at_a_time_still_means_the_clock():
    jarvis, _ = make()
    jarvis.handle("set an alarm for 7:30 am")
    jarvis.handle("set an alarm at 1 pm")
    jarvis.handle("set an alarm at 1")  # the next 1 o'clock: 1 AM tonight
    dues = sorted(a.due for a in jarvis.alarms.all())
    tomorrow = NOW.replace(hour=0) + timedelta(days=1)
    assert dues == [tomorrow.replace(hour=1), tomorrow.replace(hour=7, minute=30), tomorrow.replace(hour=13)]


def test_more_ways_to_ask_for_a_reminder():
    jarvis, _ = make()
    assert jarvis.handle("remind me to stretch after 10 minutes").say == "Okay, I'll remind you to stretch at 2:10 PM."
    assert jarvis.handle("remind me in 5 minutes").say == "Okay, I'll remind you at 2:05 PM."
    texts = sorted((a.due, a.text) for a in jarvis.alarms.all())
    assert texts == [(NOW + timedelta(minutes=5), ""), (NOW + timedelta(minutes=10), "stretch")]


def test_closing_apps():
    sys_api = FakeSystem()
    jarvis, _ = make(sys_api=sys_api)
    assert jarvis.handle("close spotify").say == "Closing Spotify."
    assert jarvis.handle("close nothing open").say == "I couldn't find an open window for nothing open."
    assert "Quit" in jarvis.handle("close jarvis").say
    assert sys_api.log == [("close", "Spotify"), ("close", "nothing open")]


def test_setting_the_volume():
    sys_api = FakeSystem()
    jarvis, _ = make(sys_api=sys_api)
    assert jarvis.handle("set the volume to 40%").say == "Volume set to 40 percent."
    jarvis.handle("volume 250")
    assert sys_api.log == [("volume", 40), ("volume", 100)]


def test_opening_your_folders():
    sys_api = FakeSystem()
    jarvis, _ = make(sys_api=sys_api)
    assert jarvis.handle("open my downloads folder").say == "Opening your downloads folder."
    assert sys_api.log == [("folder", "downloads")]


def test_the_news():
    reply = make()[0].handle("what's the news")
    assert reply.say == ("Here's the news. The top story: Storm Amy hits Scotland. Winds of 100mph. "
                         "Also today: Budget plans unveiled.")
    assert isinstance(reply.data["news"], analysis.NewsBrief)


@pytest.mark.parametrize("text, answer", [
    ("what's 25 times 4", "That's 100."),
    ("what is 15% of 80", "That's 12."),
    ("calculate 100 divided by 8", "That's 12.5."),
    ("what's the square root of 81", "That's 9."),
    ("how much is 2 to the power of 10", "That's 1,024."),
    ("what's 7 divided by 0", "You can't divide by zero."),
])
def test_sums(text, answer):
    assert make()[0].handle(text).say == answer


def test_a_question_with_a_number_that_is_not_a_sum_goes_to_wikipedia():
    web_api = FakeWeb(summary="Apollo 11 landed on the Moon.")
    assert make(web_api=web_api)[0].handle("what was apollo 11").say == "Apollo 11 landed on the Moon."


def test_stopping_and_snoozing_a_ringing_alarm_by_voice():
    ringing = [True]
    sys_api = FakeSystem()
    jarvis, _ = make(sys_api=sys_api, ringing=lambda: ringing[0])
    assert jarvis.handle("stop").action == "alarm_stop"
    assert jarvis.handle("snooze").action == "alarm_snooze"
    assert sys_api.log == []  # the music was left alone
    ringing[0] = False
    assert jarvis.handle("stop").say == "Stopped."  # nothing ringing: stop the music
    assert jarvis.handle("snooze").say == "Nothing is ringing right now."


def test_the_best_guess_is_one_jarvis_understands():
    assert commands.best(["blah blah", "open what's up", "open WhatsApp"]) == "open what's up"
    assert commands.best(["hmm", "huh"]) == "hmm"
    assert commands.best([]) == ""


@pytest.mark.parametrize("text, cleaned", [
    ("up spotify for me", "spotify"), ("the discord app now", "discord"), ("bbc dot co dot uk", "bbc.co.uk"),
    ("opera browser", "opera"), ("my downloads folder", "downloads"),
])
def test_clean_target(text, cleaned):
    assert commands.clean_target(text) == cleaned



# --- the HUD, briefings and the AI brain ------------------------------------------

class FakeAI:
    def __init__(self, answer="Forty-two, sir.", error=None, on=True, json=None):
        self.answer, self.error, self.on, self.json = answer, error, on, json or {}
        self.asked = []

    def enabled(self):
        return self.on

    def complete(self, prompt, history=(), **kwargs):
        self.asked.append((prompt, list(history)))
        if self.error:
            raise self.error
        return self.answer

    def complete_json(self, prompt, **kwargs):
        self.asked.append((prompt, []))
        if self.error:
            raise self.error
        return self.json


@pytest.mark.parametrize("text, name", [
    ("good morning", "briefing"), ("good evening jarvis", "briefing"), ("brief me", "briefing"),
    ("give me my daily briefing", "briefing"), ("what did i miss", "briefing"),
    ("open the hud", "dashboard"), ("show me the dashboard", "dashboard"),
    ("analyse my emails", "mail_analysis"), ("summarize my inbox", "mail_analysis"),
    ("what's important in my email", "mail_analysis"), ("anything important in my mail", "mail_analysis"),
    ("hello", "greeting"),
])
def test_new_intents(text, name):
    assert intent(text) == name


def test_the_daily_briefing():
    gmail = FakeGmail([mail("1", "Sarah|sarah@gmail.com", "Lunch?")])
    jarvis, _ = make(gmail=gmail)
    jarvis.alarms.add("reminder", NOW + timedelta(hours=2), "call Mum")
    jarvis.alarms.add("alarm", NOW + timedelta(days=1))  # tomorrow: not in today's briefing
    reply = jarvis.handle("good morning")
    assert reply.say == "Good afternoon, sir. It's 2:00 PM on Thursday the 1st of October."
    assert reply.action == "dashboard"
    assert reply.more[0].startswith("Right now in Bristol it's 14 degrees")
    assert reply.more[1] == "Still to come today: a reminder to call Mum at 4:00 PM."
    assert reply.more[2].startswith("You have 1 unread email.")
    assert reply.more[3] == "In the news: Storm Amy hits Scotland; Budget plans unveiled."
    assert set(reply.data) == {"weather", "mail", "news"}


def test_a_briefing_carries_on_when_parts_fail(monkeypatch):
    class BrokenGmail(FakeGmail):
        def unread(self, limit=15, skip_ids=()):
            raise RuntimeError("not signed in")

    monkeypatch.setattr(config, "ADDRESS", "")
    jarvis, _ = make(gmail=BrokenGmail(), web_api=FakeWeb(error=web.WebError("offline")))
    reply = jarvis.handle("brief me")
    assert reply.say == "Good afternoon. It's 2:00 PM on Thursday the 1st of October."
    assert reply.more == ("I couldn't get the weather.", "I couldn't check your email.", "I couldn't get the news.")
    assert make()[0].handle("brief me").more[-1].startswith("In the news")  # no mail set up: no mail part


@pytest.mark.parametrize("day, suffix", [(1, "st"), (2, "nd"), (3, "rd"), (4, "th"), (11, "th"), (12, "th"),
                                         (13, "th"), (21, "st"), (22, "nd"), (23, "rd"), (31, "st")])
def test_briefing_dates(day, suffix):
    jarvis, _ = make()
    jarvis._now = lambda: datetime(2026, 10, day, 9, 0)
    assert jarvis.handle("good morning").say.endswith(f" the {day}{suffix} of October.")


def test_analysing_the_inbox():
    gmail = FakeGmail([mail("1", "Sarah|sarah@gmail.com", "Lunch?"), mail("2", "Shop|offers@shop.com", "50% off sale")])
    reply = make(gmail=gmail)[0].handle("analyse my emails")
    assert reply.say == ('You have 2 unread emails. 1 needs a reply: Sarah about "Lunch?". '
                         "The other one is an update or a newsletter.")
    assert [n.group for n in reply.data["mail"].notes] == ["reply", "low"]


def test_claude_analyses_the_inbox_only_when_allowed(monkeypatch):
    gmail = FakeGmail([mail("1", "Sarah|sarah@gmail.com", "Lunch?")])
    brain = FakeAI(json={"mails": [], "briefing": "Sarah wants lunch, sir."})
    assert make(gmail=gmail, ai_api=brain)[0].handle("analyse my email").say.startswith("You have 1 unread")
    assert brain.asked == []  # not allowed yet
    monkeypatch.setattr(config, "AI_FOR_MAIL", True)
    assert make(gmail=gmail, ai_api=brain)[0].handle("analyse my email").say == "Sarah wants lunch, sir."


def test_opening_the_hud():
    assert make()[0].handle("open the hud").action == "dashboard"


def test_claude_answers_what_no_command_covers():
    brain = FakeAI()
    jarvis, _ = make(ai_api=brain)
    assert jarvis.handle("Sing me something, Jarvis").say == "Forty-two, sir."
    assert jarvis.handle("and another").say == "Forty-two, sir."
    assert brain.asked[1] == ("and another", [{"role": "user", "content": "Sing me something, Jarvis"},
                                              {"role": "assistant", "content": "Forty-two, sir."}])
    failing = make(ai_api=FakeAI(error=ai.AIError("the API key was rejected")))[0]
    assert failing.handle("sing me something").say == "Sorry, I couldn't reach my AI just now: the API key was rejected."
    offline = make(ai_api=FakeAI(on=False))[0]
    assert offline.handle("sing me something").say.startswith("Sorry, I don't know how to do that yet")


def test_questions_go_to_claude_then_wikipedia():
    web_api = FakeWeb(summary="Paris is the capital of France.")
    assert make(web_api=web_api, ai_api=FakeAI("Paris, sir."))[0].handle("what is the capital of France").say == "Paris, sir."
    assert web_api.asked == []
    broken = FakeAI(error=ai.AIError("down"))
    assert make(web_api=web_api, ai_api=broken)[0].handle("what is the capital of France").say == \
        "Paris is the capital of France."


def test_claude_writes_the_news_when_set_up():
    brain = FakeAI("The storm leads the news, sir.")
    reply = make(ai_api=brain)[0].handle("what's the news")
    assert reply.say == "The storm leads the news, sir." and reply.data["news"].by_claude


# --- the day, the place and other words that change the answer ------------------------

@pytest.mark.parametrize("text, said", [
    ("what's the date", "Today is Thursday, 1 October 2026."),
    ("what's the date tomorrow", "Tomorrow is Friday, 2 October 2026."),
    ("what day is it tomorrow", "Tomorrow is Friday, 2 October 2026."),
    ("what day is tomorrow", "Tomorrow is Friday, 2 October 2026."),
    ("tomorrow's date", "Tomorrow is Friday, 2 October 2026."),
    ("what day was yesterday", "Yesterday was Wednesday, 30 September 2026."),
    ("what date is next Friday", "Next Friday is 9 October 2026."),
    ("what date is this Saturday", "The day after tomorrow is Saturday, 3 October 2026."),
    ("what date is Monday", "Monday is 5 October 2026."),
    ("what will the date be in 10 days", "In 10 days it will be Sunday, 11 October 2026."),
])
def test_dates(text, said):
    jarvis, _ = make()
    assert jarvis.handle(text).say == said


def test_the_time_somewhere_else():
    web_api = FakeWeb()
    jarvis, _ = make(web_api=web_api)
    assert jarvis.handle("what time is it").say == "It's 2:00 PM."
    assert jarvis.handle("what time is it at the moment").say == "It's 2:00 PM."
    assert jarvis.handle("what time is it in Tokyo").say == "It's 10:00 PM in Tokyo."
    assert jarvis.handle("time in New York").say == "It's 10:00 PM in New York."
    assert web_api.asked == ["tokyo", "new york"]
    later = FakeWeb()
    later.place_time = lambda city: ("Auckland", NOW + timedelta(hours=12))
    jarvis, _ = make(web_api=later)
    assert jarvis.handle("what's the time in Auckland").say == "It's 2:00 AM on Friday in Auckland."
    jarvis, _ = make(web_api=FakeWeb(weather=None))
    assert jarvis.handle("what time is it in Atlantis").say == "I couldn't find a place called Atlantis."


@pytest.mark.parametrize("text, due", [
    ("set an alarm for Monday at 8", datetime(2026, 10, 5, 8, 0)),
    ("wake me up at 7 on Saturday", datetime(2026, 10, 3, 7, 0)),
    ("set an alarm for 6:30 the day after tomorrow", datetime(2026, 10, 3, 6, 30)),
    ("set an alarm for tomorrow evening at 7", datetime(2026, 10, 2, 19, 0)),
    ("set an alarm for Thursday at 9 am", datetime(2026, 10, 8, 9, 0)),  # today's 9 am is over: next week's
    ("set an alarm for tonight at 11", datetime(2026, 10, 1, 23, 0)),
    ("set an alarm in 3 days at 8 am", datetime(2026, 10, 4, 8, 0)),
])
def test_alarms_on_other_days(text, due):
    jarvis, _ = make()
    jarvis.handle(text)
    assert [a.due for a in jarvis.alarms.all()] == [due]


@pytest.mark.parametrize("text, what, due", [
    ("remind me to call mum on Friday at 5", "call mum", datetime(2026, 10, 2, 17, 0)),  # 5 in the afternoon
    ("remind me on Saturday to call mum", "call mum", datetime(2026, 10, 3, 9, 0)),  # no time: 9 in the morning
    ("remind me tomorrow morning to call mum", "call mum", datetime(2026, 10, 2, 9, 0)),
    ("remind me at 3 to call the bank", "call the bank", datetime(2026, 10, 1, 15, 0)),
    ("remind me tonight to take the bins out", "take the bins out", datetime(2026, 10, 1, 20, 0)),
    ("remind me in the evening to water the plants", "water the plants", datetime(2026, 10, 1, 18, 0)),
    ("remind me this evening at 8 to call dad", "call dad", datetime(2026, 10, 1, 20, 0)),
    ("remind me next Monday at 10 to submit my coursework", "submit my coursework", datetime(2026, 10, 5, 10, 0)),
    ("remind me in 3 days to pay rent", "pay rent", datetime(2026, 10, 4, 9, 0)),
])
def test_reminders_on_other_days(text, what, due):
    jarvis, _ = make()
    reply = jarvis.handle(text)
    assert [(a.text, a.due) for a in jarvis.alarms.all()] == [(what, due)]
    assert reply.say.startswith(f"Okay, I'll remind you to {what} at ")


@pytest.mark.parametrize("text", ["when is my alarm", "what time is my alarm", "is there an alarm set",
                                  "did I set an alarm", "how long until my alarm", "how long is left on my timer"])
def test_questions_about_alarms_list_them(text):
    jarvis, _ = make()
    jarvis.alarms.add("alarm", NOW + timedelta(hours=17))
    assert jarvis.handle(text).say == "You have 1: an alarm at 7:00 AM tomorrow."
    assert len(jarvis.alarms.all()) == 1


def test_stopping_alarms_and_timers():
    jarvis, _ = make()
    jarvis.alarms.add("timer", NOW + timedelta(minutes=5), "5 minutes")
    assert jarvis.handle("stop the timer").say == "Cancelled 1 timer."
    jarvis.alarms.add("alarm", NOW + timedelta(hours=17))
    assert jarvis.handle("turn off my alarm").say == "Cancelled 1 alarm."


def test_hellos_and_goodbyes():
    jarvis, _ = make()
    assert jarvis.handle("Hey Jarvis").say == "Yes, sir? What can I do for you?"
    assert jarvis.handle("hello Jarvis").say == "Good afternoon, sir. What can I do for you?"
    assert jarvis.handle("good night").say == "Good night, sir. Sleep well."
    assert jarvis.handle("bye").say == "Goodbye, sir. I'll be here if you need me."
    assert "Python" in jarvis.handle("who made you").say
    assert jarvis.handle("close Jarvis").say == "To close me, use Quit in the tray icon's menu."


def test_personal_questions_are_not_looked_up():
    web_api = FakeWeb()
    jarvis, _ = make(web_api=web_api)
    jarvis.handle("take a note buy milk")
    assert jarvis.handle("what are my notes").say.startswith("Your latest notes: buy milk")
    assert web_api.asked == []


def test_news_on_one_topic():
    jarvis, _ = make()
    assert jarvis.handle("any politics news").say == "Here's the politics news. The top story: Budget plans unveiled."
    assert jarvis.handle("what's the tech news").say.startswith(
        "I couldn't find any technology news just now. Here's the news. The top story: Storm Amy")


class FakeSysinfo:
    def __init__(self, *readings):
        self.readings = list(readings)

    def snapshot(self):
        return self.readings.pop(0) if len(self.readings) > 1 else self.readings[0]


def test_how_the_computer_is_doing(monkeypatch):
    monkeypatch.setattr(commands.time, "sleep", lambda seconds: None)
    reading = {"cpu": 12, "memory": 64, "battery": 76, "plugged_in": True, "disk": 70}
    jarvis, _ = make(sysinfo_api=FakeSysinfo(dict(reading, cpu=None), reading))
    assert jarvis.handle("how's my computer doing").say == (
        "The battery is at 76 percent and charging. The processor is at 12 percent. Memory use is at 64 percent. "
        "The disk is 70 percent full.")
    assert jarvis.handle("how much battery do I have").say == "The battery is at 76 percent and charging."
    assert jarvis.handle("what's my memory usage").say == "Memory use is at 64 percent."
    jarvis, _ = make(sysinfo_api=FakeSysinfo(dict(reading, battery=None, plugged_in=None)))
    assert jarvis.handle("is my laptop charging").say == "This computer doesn't have a battery I can read."
    jarvis, _ = make(sysinfo_api=FakeSysinfo({"cpu": None, "memory": None, "battery": None, "plugged_in": None,
                                               "disk": None}))
    assert jarvis.handle("what's the CPU usage").say == "I can't read that on this computer."


def test_shutting_down_is_left_to_you():
    sys_api = FakeSystem()
    jarvis, _ = make(sys_api=sys_api)
    assert jarvis.handle("shut down the computer").say.startswith("I won't shut down or restart the computer")
    assert sys_api.log == []


def test_hey_jarvis_and_a_pause_then_the_command():
    jarvis, said = make(heard=["what time is it"])
    assert jarvis.handle("Hey Jarvis", voice=True).say == "It's 2:00 PM."
    assert said == ["Yes, sir?"]
    jarvis, said = make(heard=[dictation.DictationError("silence")])
    assert jarvis.handle("Hey Jarvis", voice=True).say == "Yes, sir? What can I do for you?"
    jarvis, said = make(heard=["hey jarvis"])  # only one more try
    assert jarvis.handle("Hey Jarvis", voice=True).say == "Yes, sir? What can I do for you?"


@pytest.mark.parametrize("text, what, due", [
    ("set a reminder for 5 pm to call mum", "call mum", datetime(2026, 10, 1, 17, 0)),
    ("set a reminder for 10 minutes", "", NOW + timedelta(minutes=10)),
    ("add a reminder for tomorrow at 9 to call the dentist", "call the dentist", datetime(2026, 10, 2, 9, 0)),
    ("set a reminder to call mum at 6", "call mum", datetime(2026, 10, 1, 18, 0)),
])
def test_other_ways_to_set_a_reminder(text, what, due):
    jarvis, _ = make()
    jarvis.handle(text)
    assert [(a.kind, a.text, a.due) for a in jarvis.alarms.all()] == [("reminder", what, due)]


def test_stop_doesnt_take_away_the_next_alarm():
    jarvis, _ = make()
    jarvis.alarms.add("alarm", NOW + timedelta(hours=17))
    assert jarvis.handle("stop the alarm").say.startswith("Nothing is ringing right now.")
    assert len(jarvis.alarms.all()) == 1
    assert jarvis.handle("cancel any alarms").say == "Cancelled 1 alarm."


@pytest.mark.parametrize("text, name", [
    ("who is Jarvis", "wiki"),
    ("turn off the screen", "lock"),
    ("what is RAM used for", "wiki"),
    ("how much space is left on my disk", "system_status"),
    ("what's the best battery for a laptop", None),
    ("set a reminder for 5 pm", "reminder"),
    ("time in Tokyo", "time"),
    ("what day is it tomorrow", "date"),
    ("what day is the meeting", None),
])
def test_more_intents(text, name):
    assert intent(text) == name

# --- English as people really speak it, accents and all --------------------------------

@pytest.mark.parametrize("text, said", [
    ("how is the tomorrow weather",
     "Tomorrow in Bristol: overcast skies, between 8 and 13 degrees, with a 20 percent chance of rain."),
    ("How will be the weather tomorrow?",
     "Tomorrow in Bristol: overcast skies, between 8 and 13 degrees, with a 20 percent chance of rain."),
    ("What is weather of tomorrow?",
     "Tomorrow in Bristol: overcast skies, between 8 and 13 degrees, with a 20 percent chance of rain."),
    ("Weather of Istanbul tomorrow",
     "Tomorrow in Istanbul: overcast skies, between 8 and 13 degrees, with a 20 percent chance of rain."),
    ("How is weather at Saturday?",
     "On Saturday in Bristol: partly cloudy skies, between 10 and 16 degrees, with a 10 percent chance of rain."),
    ("Tomorrow there is rain?", "Probably not. There's only a 20 percent chance of rain in Bristol tomorrow."),
    ("It will rain tomorrow?", "Probably not. There's only a 20 percent chance of rain in Bristol tomorrow."),
    ("Will snow tomorrow?", "No snow is expected in Bristol tomorrow. Expect overcast skies, between 8 and 13 degrees."),
    ("What's the chance of rain tomorrow?", "Probably not. There's only a 20 percent chance of rain in Bristol tomorrow."),
    ("What is the hour now?", "It's 2:00 PM."),
    ("What hour is it in Istanbul?", "It's 10:00 PM in Istanbul."),
    ("Say me the time", "It's 2:00 PM."),
    ("What is the date of tomorrow?", "Tomorrow is Friday, 2 October 2026."),
    ("Which day is today?", "Today is Thursday, 1 October 2026."),
    ("Set alarm at Monday 8", "Alarm set for 8:00 AM on Monday."),
    ("Wake up me at 7", "Alarm set for 7:00 AM tomorrow."),
    ("Remind me call mom tomorrow at 5", "Okay, I'll remind you to call mom at 5:00 PM tomorrow."),
    ("Play me Tarkan", "Playing Tarkan on YouTube."),
    ("Search in Google python tutorial", "Here's what Google found for python tutorial."),
    ("Close please Chrome", "Closing Google Chrome."),
    ("Open me Spotify", "Opening Spotify."),
    ("Turn up the volume", "Volume up."),
    ("Next music", "Next one."),
])
def test_how_people_really_ask(monkeypatch, text, said):
    monkeypatch.setattr(config, "HOME_CITY", "Bristol")
    jarvis, _ = make()
    assert jarvis.handle(text).say == said


def test_the_usual_replies_for_the_human_voice(monkeypatch):
    replies = commands.usual_replies()
    assert "Yes, sir? What can I do for you?" in replies and "Good evening, sir. What can I do for you?" in replies
    assert "Next one." in replies and commands.JOKES[0] in replies and commands.HELP in replies
    monkeypatch.setattr(config, "ADDRESS", "")
    assert "Yes? What can I do for you?" in commands.usual_replies()


def test_no_rain_is_said_as_such(monkeypatch):
    data = weather_sample("Bristol", NOW)
    assert web.weather_text(dict(data, rain=0)).endswith("No rain is expected.")
    data["days"][1] = dict(data["days"][1], rain=0)
    jarvis, _ = make()
    jarvis.web.weather_data = lambda city: data
    assert jarvis.handle("weather tomorrow").say.endswith("between 8 and 13 degrees, and no rain expected.")


def test_mail_problems_are_said_out_loud():
    from google.auth.exceptions import RefreshError

    from jarvis.auth import AuthError

    problems = iter([AuthError("credentials.json not found. Put the file ..."), AuthError("Login was refused"),
                     RefreshError("expired")])

    def gmail():
        raise next(problems)

    jarvis = commands.Assistant(gmail=gmail, alarm_store=alarms.AlarmStore(config.ALARMS_FILE), now=lambda: NOW,
                                system_api=FakeSystem(), web_api=FakeWeb())
    assert jarvis.handle("check my email").say.startswith("Your Gmail isn't connected on this computer yet.")
    assert jarvis.handle("read my emails").say == "I couldn't log in to Gmail: Login was refused"
    assert jarvis.handle("analyse my email").say.startswith("Your Gmail login has expired.")



@pytest.mark.parametrize("text, limit", [
    ("read my last email", 1), ("read my last mail", 1), ("read the latest email", 1), ("read me my newest message", 1),
    ("what's my latest email", 1), ("last email", 1), ("read my last three emails", 3), ("read my recent emails", 3),
    ("read my last 10 emails", 5),
])
def test_reading_the_latest_mail(text, limit):
    inbox = [Mail(str(i), f"Sender {i}", f"s{i}@example.com", f"Subject {i}", "preview", NOW - timedelta(hours=i))
             for i in range(6)]
    gmail = FakeGmail([], inbox)
    jarvis, _ = make(gmail=gmail)
    reply = jarvis.handle(text)
    assert gmail.asked == [limit]
    assert reply.more[0] == "Email from Sender 0. Subject: Subject 0. Body of 0."
    assert len(reply.more) == limit
    if limit == 1:
        assert reply.say == "Your latest email came today at 2:00 PM."


def test_who_emailed_me_last_and_an_empty_inbox():
    yesterday = Mail("9", "Ali", "ali@example.com", "Dinner", "", NOW - timedelta(days=1, hours=2))
    jarvis, _ = make(gmail=FakeGmail([], [yesterday]))
    assert jarvis.handle("who emailed me last").say == "Your latest email is from Ali, yesterday at 12:00 PM: Dinner."
    older = Mail("8", "Bo", "bo@example.com", "Hi", "", datetime(2026, 9, 20, 9, 0))
    jarvis, _ = make(gmail=FakeGmail([], [older]))
    assert jarvis.handle("read my last email").say == "Your latest email came on Sunday 20 September."
    jarvis, _ = make(gmail=FakeGmail([], []))
    assert jarvis.handle("read my last email").say == "Your inbox is empty."


def test_reading_mail_when_none_of_it_is_important():
    jarvis, _ = make(gmail=FakeGmail([mail("1", "Bo|bo@shop.com", "Your order"), mail("2", "Cy|cy@x.com", "Hi")]))
    assert jarvis.handle("read my emails").say == (
        "None of your 2 unread emails look important. The newest is from Bo: Your order. "
        "Say read my last email to hear it.")


@pytest.mark.parametrize("text, name", [("read my last email", "mail_latest"), ("who sent me the last email", "mail_latest"),
                                        ("read my emails", "mail_read"), ("check my email", "mail_check"),
                                        ("read the last message", "mail_latest")])
def test_mail_intents(text, name):
    assert intent(text) == name
