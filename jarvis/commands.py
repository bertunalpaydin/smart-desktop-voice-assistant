"""Jarvis's brain: works out what a spoken or typed command means, and does it."""
import ast
import math
import operator
import random
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import quote

from google.auth.exceptions import RefreshError, TransportError
from googleapiclient.errors import HttpError

from . import (ai, alarms, analysis, compose, config, dictation, forecast, importance, macos, notes, speech, sysinfo,
               system, web)
from .auth import AuthError
from .models import Draft
from .powershell import PowerShellError


@dataclass(frozen=True)
class Reply:
    say: str
    action: str = ""  # something only the window can do: compose, screenshot, inbox, alarms, settings
    data: object = None
    more: tuple = ()  # further things to read out after `say`, one by one


HELP = (
    "I can tell you the time and the date, check and read your email, write an email, "
    "play music on YouTube or from your music folder, pause or skip songs and set the volume, "
    "set alarms, timers and reminders, open and close apps, websites and folders, search the web or YouTube, "
    "look things up on Wikipedia, do sums, tell you the weather, take notes, take a screenshot and lock the computer. "
    "I can also brief you on your day, and analyse your email and the news: say brief me, analyse my email, "
    f"or what's the news. Just say Hey Jarvis, or press {config.HOTKEY_SPOKEN}."
)

JOKES = [
    "Why do programmers prefer dark mode? Because light attracts bugs.",
    "I'd tell you a UDP joke, but you might not get it.",
    "There are 10 kinds of people: those who understand binary and those who don't.",
    "A SQL query walks into a bar, goes up to two tables and asks: can I join you?",
    "Why did the developer go broke? He used up all his cache.",
]

_PATTERNS = [(name, re.compile(pattern)) for name, pattern in [
    # only while an alarm is ringing; otherwise these fall through to the next patterns
    ("alarm_stop", r"^(stop|stop it|stop the (alarm|timer|reminder)|turn (it|the (alarm|timer)) off|"
                   r"turn off the (alarm|timer)|close the alarm|dismiss|i'm up|im up|ok|okay|enough|alarm off)$"),
    ("alarm_snooze", r"\bsnooze\b"),
    ("cancel", r"^(cancel|never ?mind|nothing|forget it|that's all|thats all|no thanks)$"),
    ("help", r"\b(help|what can you do)\b"),
    ("briefing", r"\b(daily briefing|morning briefing|brief me|my briefing|what did i miss|catch me up)\b"
                 r"|^good (morning|afternoon|evening)( jarvis)?$"),
    ("dashboard", r"\b(open|show)( me)?( the| your)? (dashboard|hud|main screen)\b|^(the )?(dashboard|hud)$"),
    ("greeting", r"^(hi|hello|hey|hiya|yo|what's up|whats up|how's it going)( there)?$"),
    ("goodbye", r"^(good ?night|goodbye|good bye|bye|bye bye|see you|see you later|see ya|that's it for now)$"),
    ("thanks", r"\b(thanks|thank you|cheers)\b"),
    ("who_are_you", r"\bwho are you\b|\bwhat('s| is) your name\b|\bwho (made|created|built|programmed|wrote) you\b"),
    ("how_are_you", r"\bhow are you\b"),
    ("joke", r"\bjoke\b"),
    ("time", r"\bwhat time is it\b|\bwhat('s| is) the (time|hour)\b|\bwhat hour is it\b|\b(tell|say|give) me the time\b"
             r"|^(the )?time( now)?$"
             r"|^(the )?(current |local )?time (in|at) \w"),
    ("date", r"\b(what|which)('s| is| was| will be)( the)? date\b|\b(today's|tomorrow's|yesterday's) date\b"
             r"|\b(what|which) (day|date)( of the week)? (is|was|will) (it|today|tomorrow|yesterday|this|next|on|in|"
             r"the day|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b"
             r"|\bwhat will the (date|day) be\b|^(the )?date( today| tomorrow| yesterday)?$"),
    # alarms come before anything else that might grab their numbers
    ("alarms_cancel", r"\b(cancel|delete|remove|clear|turn off|stop|switch off)( all)?( of)?( my| the| any)?"
                      r" (alarms?|timers?|reminders?)\b"),
    ("alarms_list", r"\b(what|which|list|show|any)( are)?( my)?( (alarms?|timers?|reminders?)( and| or|,)?)+\b"
                    r"|\bhow (much time|long)( is)? left\b|\btime left\b"
                    r"|^(when|what time)\b.*\b(is|are|did|does|will)\b.*\b(alarms?|timers?|reminders?)\b"
                    r"|\bhow long (until|till|before)\b.*\b(alarms?|timers?|reminders?)\b"
                    r"|\b(is|are) (there|my)( any| an?)? (alarms?|timers?|reminders?)\b"
                    r"|\bdid i set (an? |any )?(alarms?|timers?|reminders?)\b"),
    ("timer", r"\btimer\b"),
    ("reminder", r"\bremind me\b|\b(set|add|create|make|put in|new)( me)?( an?)? reminder\b|^reminder\b"),
    ("alarm", r"\balarm\b|\bwake me( up)?\b|\bwake up me\b"),
    # mail
    ("inbox", r"\b(open|show)( me)? (my )?inbox\b"),
    ("mail_analysis", r"\b(analy[sz]e|summari[sz]e|sum up|go through)\b.*\b(e-?mails?|inbox|mail)\b"
                      r"|\bwhat'?s important in my (e-?mails?|inbox|mail)\b"
                      r"|\b(anything|any) important (in my )?(e-?mails?|mail|inbox)\b"),
    ("mail_send", r"\b(send|write|compose)( an?)?( e-?mail| mail| message) to (?P<who>.+)$|^e-?mail (?P<who2>.+)$"),
    ("mail_latest", r"\b(read|what('s| is| was)|tell me|say|check|open|play|show)\b.*\b(last|latest|newest|most recent|"
                    r"recent)\b.*\b(e-?mails?|mails?|messages?)\b|\bwho (sent|emailed|mailed|messaged|wrote to) me( last|"
                    r" most recently)?\b|^(my |the )?(last|latest|newest|most recent) (\w+ )?(e-?mails?|mails?|messages?)$"),
    ("mail_read", r"\bread\b.*\b(e-?mails?|mails?|inbox|messages)\b"),
    ("mail_check", r"\b(check|any|new|how many|unread)\b.*\b(e-?mails?|mails?|inbox)\b|\bdo i have\b.*\b(e-?mails?|mail)\b"),
    # music and sound
    ("media_stop", r"^stop( the| playing| this)?( music| song| video)?$"),
    ("media_pause", r"^pause( the| this)?( music| song| video)?$"),
    ("media_resume", r"^(resume|continue|unpause)( the| this)?( music| song| video)?$|^play$"),
    ("media_next", r"^(next|skip)( song| track| video| one| this| it| music)?$|\b(next|skip (this|the)) (song|track)\b"
                   r"|\b(change|skip) (the |this )?(song|track|music)\b"),
    ("media_previous", r"^(previous|go back|back)( song| track| video| one)?$|\b(previous|last) (song|track)\b"),
    ("volume_set", r"\bvolume (to |at )?(?P<level>\d{1,3})( percent| %|%)?$|\bvolume (to|at) (?P<level2>\d{1,3})\b"),
    ("volume_up", r"\b(volume up|turn (it |the volume |the sound )?up|turn up the (volume|sound)|louder|"
                  r"(increase|raise|up) (the )?(volume|sound)|more volume)\b"),
    ("volume_down", r"\b(volume down|turn (it |the volume |the sound )?down|turn down the (volume|sound)|quieter|"
                    r"(lower|decrease|reduce) (the )?(volume|sound)|less volume)\b"),
    ("mute", r"\b(mute|unmute)\b"),
    ("play_music", r"^play (me |us )?(some |my )?music$|^play (me )?something$|^music$"),
    ("play", r"^play (?P<query>.+?)( on (?P<service>youtube|spotify))?$"),
    # the web
    ("youtube_search", r"^search youtube for (?P<query>.+)$|^(search |look up )?(?P<query2>.+) on youtube$"),
    ("search", r"^(search( (in|on) google| (in|on) the internet| on the web| the web| google| online)?( for)?|google|"
               r"look up) (?P<query>.+)$"),
    # "rain" and "umbrella" only where they're about the weather, not "who sang Purple Rain"
    ("weather", r"\b(weather|forecast|snowing|snowy|windy|sunrise|sunset)\b|\btemperature\b(?! of)"
                r"|\b(will|is|was|does) it( still)?( be| going to| gonna)?( be)? (rain|raining|snow|sunny|cloudy|windy|hot|"
                r"cold|warm|chilly|freezing|foggy|nice|wet|stormy|dry)\b"
                r"|\bhow (hot|cold|warm|chilly|windy|wet) (is it|will it be|is it going to be|it is|it's|it will be)\b"
                r"|\b(chance|chances|probability) of (rain|snow|showers)\b"
                r"|\b(is there|will there be|there is|there will be|there's) (any )?(rain|snow)\b"
                r"|\b(will|going to|gonna) (be )?(rain|raining|rainy|snow|snowing|snowy)\b|\bhow many degrees?\b"
                r"|\b(today|tonight|tomorrow|weekend)\b.*\b(rain|raining|rainy|snow|snowing)\b"
                r"|^(any )?(rain|raining|rainy|showers|drizzle)\b"
                r"|\b(rain|raining|rainy|showers|drizzle)\b.*\b(today|tonight|tomorrow|weekend|week|later|now|outside|"
                r"this (morning|afternoon|evening)|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b"
                r"|\b(need|take|bring|carry|pack)( a| an| my)? (umbrella|coat|jacket|raincoat)\b"
                r"|\bwhen does the sun (rise|set|come up|go down)\b"),
    ("news", r"\b(news|headlines)\b"),
    ("system_status", r"\b(my|the) (battery|charge)\b.*\b(level|percent|percentage|left|status|at|is)\b"
                      r"|\bbattery (level|percentage|status|life left)\b|\bhow much (battery|charge)\b"
                      r"|\b(is|am i) (it |my (computer|laptop|mac|pc|battery) )?charging\b"
                      r"|\b(cpu|processor|memory|ram|disk|storage) (usage|use|load|level|space)\b"
                      r"|\bhow (busy|full) is (my|the) (cpu|processor|disk|computer|laptop|mac|pc)\b"
                      r"|\bhow much (memory|ram|storage|disk space|space) (is |am i |have i )?(left|free|used|using|"
                      r"available|got)\b|\b(my|the) (cpu|processor|memory|ram|disk|storage)\b.*\b(usage|used|load|level|"
                      r"percent|full|left|free|busy)\b"
                      r"|\b(system|computer|laptop|mac|pc) (status|health|stats)\b|\bhow('s| is) (my|the) "
                      r"(computer|laptop|mac|pc|system) doing\b|^(battery|cpu|memory|ram)$"),
    ("calculate", r"^(what('s| is)|calculate|how much is|work out) (?P<expr>.*\d.*)$"),
    ("wiki", r"^(who|what) (is|are|was|were) (?P<query>.+)$|^tell me about (?P<query2>.+)$"),
    # the computer
    ("settings", r"\b(open|show) (your|jarvis|jarvis's|the jarvis) settings\b|^jarvis settings$"),
    ("note_read", r"\b(read|what are|show)( me)?( my| the)? notes\b|\bwhat did i (note|write down)\b"),
    ("note_add", r"^(take a note|make a note|note down|note this|note that|note|remember that|remember)( that)?:? "
                 r"(?P<text>.+)$"),
    ("screenshot", r"\bscreen ?shot\b|\bcapture (the|my) screen\b"),
    ("lock", r"\block (the |my )?(computer|pc|screen|laptop|mac)\b|^screen off$"
             r"|\b(turn off|switch off|sleep|put to sleep) (the |my )?(computer |laptop )?(screen|display|monitor)\b"),
    ("power_off", r"\b(shut ?down|restart|reboot|turn off|power off|switch off) (the |my |this )?"
                  r"(computer|pc|laptop|mac|system|machine)\b"),
    ("open", r"^(open|launch|start|run|go to) (?P<target>.+)$"),
    ("close", r"^(close|quit|exit) (?P<target>.+)$"),
]]

_FILLER_START = re.compile(r"^(up|the|my|a|an|me|us)\s+")
_FILLER_END = re.compile(r"\s+(for me|please|now|right now|quickly|app|application|program|website|site|browser|folder)$")
_OPERATORS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
              ast.Pow: operator.pow, ast.Mod: operator.mod}
_SUM_WORDS = [
    (r"(\d+(?:\.\d+)?) ?(percent|%) of", r"\1/100*"),
    (r"\bsquare root of (\d+(?:\.\d+)?)", r"sqrt(\1)"),
    (r"\bmultiplied by\b|\btimes\b|×|\bx\b", "*"),
    (r"\bdivided by\b|\bover\b|÷", "/"),
    (r"\bplus\b", "+"),
    (r"\bminus\b|\btake away\b", "-"),
    (r"\bto the power of\b|\^", "**"),
    (r"\bsquared\b", "**2"),
    (r"\bcubed\b", "**3"),
]


# what Jarvis says most often: the human voice makes these while idle, so they come out at once
_USUAL = (
    "Okay.", "Done.", "Noted.", "You're welcome.", "Stopped.", "Paused.", "Playing.", "Next one.", "Going back.",
    "Volume up.", "Volume down.", "Opening the HUD.", "Here's your inbox.", "Opening the settings.",
    "Taking a screenshot.", "Turning the screen off.", "Okay, alarm off.", "Okay, I'll ring again in 5 minutes.",
    "Nothing is ringing right now.", "Sorry, I didn't catch that.", "All systems running. What can I do for you?",
    "I'm Jarvis: Just A Rather Very Intelligent System, at your service.",
    "Sorry, I don't know how to do that yet. Say help to hear what I can do.",
    "You have no alarms, timers or reminders.", "You have no unread email.",
    "I couldn't get that from the internet, because there's no internet connection. Try again in a moment.",
    "For when? Say something like: set an alarm for 7 am.",
    "How long? Say something like: set a timer for 10 minutes.",
)


def usual_replies():
    """Replies Jarvis gives word for word again and again (with how it addresses you)."""
    address = f", {config.ADDRESS}" if config.ADDRESS else ""
    greetings = [f"Good {part}{address}. What can I do for you?" for part in ("morning", "afternoon", "evening")]
    return [f"Yes{address}?", f"Yes{address}? What can I do for you?", *greetings, *_USUAL,
            f"Good night{address}. Sleep well.", f"Goodbye{address}. I'll be here if you need me.", *JOKES, HELP]


def clean_target(text):
    """'up spotify for me' -> 'spotify': the words around a name that aren't part of it."""
    previous = None
    while previous != text:
        previous = text
        text = _FILLER_END.sub("", _FILLER_START.sub("", text.strip()))
    return text.replace(" dot ", ".").strip()


def _evaluate(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPERATORS:
        left, right = _evaluate(node.left), _evaluate(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 100:
            raise ValueError("too big")
        return _OPERATORS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        value = _evaluate(node.operand)
        return -value if isinstance(node.op, ast.USub) else value
    if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "sqrt" and len(node.args) == 1 and not node.keywords:
        return math.sqrt(_evaluate(node.args[0]))
    raise ValueError("not a sum")


def calculate(text):
    """'25 times 4' -> 100. Returns None if the text isn't a sum."""
    text = alarms.words_to_numbers(text.casefold().replace(",", ""))
    text = re.sub(r"^(the|a)\s+", "", text.strip())
    for pattern, replacement in _SUM_WORDS:
        text = re.sub(pattern, replacement, text)
    text = text.strip().rstrip("?= ")
    if not re.fullmatch(r"[\d\s.+\-*/()%a-z]*", text) or not re.search(r"\d", text):
        return None
    try:
        return _evaluate(ast.parse(text, mode="eval").body)
    except (SyntaxError, ValueError, TypeError, OverflowError, RecursionError):
        return None


def number_text(value):
    if isinstance(value, float):
        value = round(value, 4)
        if value.is_integer():
            value = int(value)
    return f"{value:,}" if isinstance(value, int) else str(value)


def best(guesses):
    """Of the speech service's guesses, the first one that is a command Jarvis knows."""
    for text in guesses:
        command = normalise(text)
        if any(pattern.search(command) for _name, pattern in _PATTERNS):
            return text
    return guesses[0] if guesses else ""


def normalise(text):
    text = text.casefold().strip()
    text = re.sub(r"\b([ap])\.\s?m\b\.?", r"\1m", text)  # a.m. -> am
    text = re.sub(r"[^\w\s:.'@+%*/×÷^()-]", " ", text)  # drop punctuation, keep times, sums, addresses, "what's"
    text = re.sub(r"\.(?!\w)", " ", text)  # full stops, but not the dots in 7.30 or gmail.com
    previous = None
    while previous != text:
        previous = text
        text = text.strip()
        text = re.sub(r"^(hey |ok |okay |hi )?jarvis\b", "", text)
        if not re.search(r"\b(is|are|was|were|about|called|named|meet|close|quit|exit|open|restart|stop|kill)\s+jarvis$",
                         text):
            text = re.sub(r"(?<=\w)\s+jarvis$", "", text)  # "hello jarvis", "thank you jarvis"
        text = re.sub(r"^(please|can you|could you|would you|will you)\b", "", text)
        text = re.sub(r"\bplease\b", "", text)  # anywhere: "close please chrome"
    return " ".join(text.split())


class Assistant:
    def __init__(self, gmail=None, speak=None, listen=None, alarm_store=None, now=datetime.now,
                 system_api=system, web_api=web, ringing=None, ai_api=ai, sysinfo_api=sysinfo):
        self._gmail = gmail  # a function that returns the Gmail client (it may need to log in first)
        self._speak = speak or (lambda text, **kwargs: None)
        self._listen = listen  # for follow-up questions when the command was spoken
        self.alarms = alarm_store or alarms.AlarmStore(config.ALARMS_FILE)
        self._now = now
        self.system = system_api
        self.web = web_api
        self._ringing = ringing or (lambda: False)  # is an alarm going off right now?
        self.ai = ai_api
        self.sysinfo = sysinfo_api
        self._chat = []  # the conversation with Claude so far, for follow-up questions
        self._voice = False
        self._raw = ""
        self._apps = None
        self._apps_checked = 0.0

    def handle(self, text, voice=False, follow_up=False):
        """Do what the command asks. Returns a Reply to say and show."""
        command = normalise(text)
        self._raw = " ".join(text.split())
        self._voice = voice and self._listen is not None
        if not command:
            if re.search(r"\bjarvis\b", text, re.IGNORECASE):  # just "Hey Jarvis", then a pause
                address = f", {config.ADDRESS}" if config.ADDRESS else ""
                if self._voice and not follow_up:
                    heard = self._ask(f"Yes{address}?")
                    if heard.strip():
                        return self.handle(heard, voice=True, follow_up=True)
                return Reply(f"Yes{address}? What can I do for you?")
            return Reply("Sorry, I didn't catch that.")
        for intent, pattern in _PATTERNS:
            match = pattern.search(command)
            if not match:
                continue
            try:
                reply = getattr(self, f"_{intent}")(command, match)
            except web.WebError as exc:
                return Reply(f"I couldn't get that from the internet, because {exc.reason}. Try again in a moment.")
            except system.Unavailable as exc:
                return Reply(str(exc))
            except PowerShellError as exc:
                return Reply(f"Windows wouldn't let me do that: {exc}")
            except macos.MacError as exc:
                return Reply(f"Your Mac wouldn't let me do that: {exc}")
            except AuthError as exc:  # say it: a notification alone is easy to miss
                if "credentials.json not found" in str(exc):
                    return Reply("Your Gmail isn't connected on this computer yet. Put the credentials.json file "
                                 "from Google Cloud in the secrets folder of the Jarvis project, run the installer "
                                 "again, then ask me again.")
                return Reply(f"I couldn't log in to Gmail: {exc}")
            except RefreshError:
                return Reply("Your Gmail login has expired. Ask me again and log in once more in the browser.")
            except (HttpError, TransportError):
                return Reply("I couldn't reach Gmail just now. Try again in a moment.")
            if reply is not None:  # None: not this one after all, try the next pattern
                return reply
        if self.ai.enabled():  # no command for it: let Claude answer
            try:
                return Reply(self._ask_ai(self._raw))
            except ai.AIError as exc:
                return Reply(f"Sorry, I couldn't reach my AI just now: {exc}.")
        return Reply("Sorry, I don't know how to do that yet. Say help to hear what I can do.")

    def _ask_ai(self, question):
        answer = self.ai.complete(question, history=self._chat[-8:])
        self._chat += [{"role": "user", "content": question}, {"role": "assistant", "content": answer}]
        return answer

    def _ai_for_mail(self):
        return config.AI_FOR_MAIL and self.ai.enabled()

    # --- talking ------------------------------------------------------------------

    def _original(self, fragment):
        """The fragment as it was typed or heard, capitals and all."""
        words = [re.escape(w) for w in fragment.split()]
        found = re.search(r"\W+".join(words), self._raw, re.IGNORECASE) if words else None
        return found.group(0) if found else fragment

    def _alarm_stop(self, command, match):
        return Reply("Okay, alarm off.", "alarm_stop") if self._ringing() else None

    def _alarm_snooze(self, command, match):
        if not self._ringing():
            return Reply("Nothing is ringing right now.")
        return Reply("Okay, I'll ring again in 5 minutes.", "alarm_snooze")

    def _cancel(self, command, match):
        return Reply("Okay.")

    def _help(self, command, match):
        return Reply(HELP)

    def _greeting(self, command, match):
        hour = self._now().hour
        part = "morning" if hour < 12 else "afternoon" if hour < 18 else "evening"
        return Reply(f"Good {part}{', ' + config.ADDRESS if config.ADDRESS else ''}. What can I do for you?")

    def _goodbye(self, command, match):
        address = f", {config.ADDRESS}" if config.ADDRESS else ""
        if "night" in command:
            return Reply(f"Good night{address}. Sleep well.")
        return Reply(f"Goodbye{address}. I'll be here if you need me.")

    def _thanks(self, command, match):
        return Reply("You're welcome.")

    def _who_are_you(self, command, match):
        if re.search(r"\bwho (made|created|built|programmed|wrote) you\b", command):
            return Reply("I'm a personal assistant written in Python, inspired by the Jarvis of the Iron Man films.")
        return Reply("I'm Jarvis: Just A Rather Very Intelligent System, at your service.")

    def _how_are_you(self, command, match):
        return Reply("All systems running. What can I do for you?")

    def _joke(self, command, match):
        return Reply(random.choice(JOKES))

    def _time(self, command, match):
        place = forecast.place_in(command)
        if not place:
            return Reply(f"It's {alarms.clock(self._now())}.")
        found = self.web.place_time(place)
        if not found:
            return Reply(f"I couldn't find a place called {self._original(place)}.")
        name, there = found
        day = "" if there.date() == self._now().date() else f" on {there:%A}"
        return Reply(f"It's {alarms.clock(there)}{day} in {name}.")

    def _date(self, command, match):
        now = self._now()
        days, words = alarms.find_day(command, now.date())
        if not days:
            return Reply(f"Today is {now:%A}, {now.day} {now:%B} {now.year}.")
        day = now.date() + timedelta(days=days)
        full = f"{day:%A}, {day.day} {day:%B} {day.year}"
        named = {1: f"Tomorrow is {full}.", -1: f"Yesterday was {full}.", 2: f"The day after tomorrow is {full}.",
                 -2: f"The day before yesterday was {full}."}
        if days in named:
            return Reply(named[days])
        if any(name in words for name in alarms.WEEKDAYS):
            which = "Next " if "next" in words.split() else "This " if "this" in words.split() else ""
            return Reply(f"{which}{day:%A} is {day.day} {day:%B} {day.year}.")
        if days > 0:
            return Reply(f"In {days} days it will be {full}.")
        return Reply(f"{-days} days ago it was {full}.")

    # --- mail -----------------------------------------------------------------------

    def _client(self):
        if self._gmail is None:
            raise system.Unavailable("Mail isn't set up here.")
        return self._gmail()

    def _inbox(self, command, match):
        return Reply("Here's your inbox.", "inbox")

    def _mail_analysis(self, command, match):
        brief = analysis.brief_mail(self._client().unread(), use_ai=self._ai_for_mail(), brain=self.ai)
        return Reply(brief.spoken, data={"mail": brief})

    def _dashboard(self, command, match):
        return Reply("Opening the HUD.", "dashboard")

    def _briefing(self, command, match):
        """Good morning: the time, the weather, today's alarms, the inbox and the news."""
        now = self._now()
        part = "morning" if now.hour < 12 else "afternoon" if now.hour < 18 else "evening"
        day = now.day
        suffix = "th" if 11 <= day % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
        say = (f"Good {part}{', ' + config.ADDRESS if config.ADDRESS else ''}. "
               f"It's {alarms.clock(now)} on {now:%A} the {day}{suffix} of {now:%B}.")
        more, data = [], {}
        try:
            weather = self.web.weather_data(config.HOME_CITY)
            if weather:
                more.append(web.weather_text(weather))
                data["weather"] = weather
        except web.WebError:
            more.append("I couldn't get the weather.")
        today = [a for a in self.alarms.all() if a.due.date() == now.date()]
        if today:
            more.append("Still to come today: " + "; ".join(a.describe(now) for a in today[:4]) + ".")
        if self._gmail is not None:
            try:
                mail = analysis.brief_mail(self._client().unread(), use_ai=self._ai_for_mail(), brain=self.ai)
                more.append(mail.spoken)
                data["mail"] = mail
            except Exception:  # not signed in, no internet: the rest of the briefing still matters
                more.append("I couldn't check your email.")
        try:
            news = analysis.brief_news(self.web.news_items())
            if news.stories:
                more.append("In the news: " + "; ".join(s.title.rstrip(".") for s in news.stories[:3]) + ".")
            data["news"] = news
        except web.WebError:
            more.append("I couldn't get the news.")
        return Reply(say, "dashboard", data, tuple(more))

    def _mail_check(self, command, match):
        mails = self._client().unread()
        if not mails:
            return Reply("You have no unread email.")
        important = sum(1 for m in mails if importance.is_important(m))
        count = f"at least {len(mails)}" if len(mails) >= config.MAX_UNREAD else str(len(mails))
        return Reply(f"You have {count} unread email{'' if len(mails) == 1 else 's'}, "
                     f"{important or 'none'} of them important.")

    def _mail_read(self, command, match):
        client = self._client()
        unread = client.unread()
        important = [m for m in unread if importance.is_important(m)]
        if not important and unread:  # say what there is, not just "nothing important"
            plural = "" if len(unread) == 1 else "s"
            return Reply(f"None of your {len(unread)} unread email{plural} look important. The newest is from "
                         f"{unread[0].sender}: {unread[0].subject}. Say read my last email to hear it.")
        texts = tuple(speech.mail_text(m, client.body(m.id) or m.snippet) for m in important)
        return Reply(speech.summary(len(important)), more=texts)

    def _mail_latest(self, command, match):
        """"Read my last email": the newest ones in the inbox, important or not, read or not."""
        words = alarms.words_to_numbers(command)
        count = re.search(r"\b(\d+) (last |latest |newest |recent )?(e-?mails|mails|messages)\b", words)
        plural = re.search(r"\b(e-?mails|mails|messages)\b", command) and not re.search(r"\bwho\b", command)
        limit = min(5, int(count.group(1))) if count else 3 if plural else 1
        client = self._client()
        mails = client.latest(limit)
        if not mails:
            return Reply("Your inbox is empty.")
        if re.search(r"\bwho\b", command):  # "who emailed me last": just who and what about
            mail = mails[0]
            return Reply(f"Your latest email is from {mail.sender}, {self._when_received(mail)}: {mail.subject}.")
        texts = tuple(speech.mail_text(m, client.body(m.id) or m.snippet) for m in mails)
        if len(mails) == 1:
            return Reply(f"Your latest email came {self._when_received(mails[0])}.", more=texts)
        return Reply(f"Here are your {len(mails)} latest emails, newest first.", more=texts)

    def _when_received(self, mail):
        received = mail.received
        if received is None:
            return "recently"
        if received.tzinfo is not None:
            received = received.astimezone().replace(tzinfo=None)  # the computer's own time
        today = self._now().date()
        if received.date() == today:
            return f"today at {alarms.clock(received)}"
        if received.date() == today - timedelta(days=1):
            return f"yesterday at {alarms.clock(received)}"
        return f"on {received:%A} {received.day} {received:%B}"

    def _mail_send(self, command, match):
        who = (match.group("who") or match.group("who2")).strip()
        who = re.sub(r"^(my|the)\s+", "", who)
        address = self._address_for(who)
        subject = body = ""
        if self._voice:
            subject = self._ask("What's the subject?")
            body = self._ask("What should it say?")
        draft = Draft(to=address or "", subject=subject, body=body)
        if not address:
            return Reply(f"I don't have an address for {self._original(who)}, so type it in the window.", "compose",
                         draft)
        return Reply("Here it is. Check it and press Send.", "compose", draft)

    def _ask(self, question):
        self._speak(question)
        try:
            return self._listen(timeout=8, phrase_limit=60)
        except (dictation.DictationError, dictation.DictationUnavailable):
            return ""

    def _address_for(self, who):
        spoken = re.sub(r"\s+dot\s+", ".", re.sub(r"\s+at\s+", "@", who)).replace(" ", "")
        for candidate in (who, spoken):
            if compose.valid_address(candidate):
                return candidate
        contacts = {name.casefold(): address for name, address in config.CONTACTS.items()}
        if who in contacts:
            return contacts[who]
        for name, address in contacts.items():
            if who in name.split() or name.startswith(who):
                return address
        if self._gmail is not None:
            for mail in self._client().unread():
                if who and who in mail.sender_name.casefold().split():
                    return mail.sender_email
        return None

    # --- music and sound -----------------------------------------------------------

    def _play(self, command, match):
        query = re.sub(r"^(me|us|for me)\s+", "", match.group("query"))  # "play me Tarkan"
        query, service = self._original(query), match.group("service")
        if service == "spotify":
            if not self.system.open_url(f"spotify:search:{quote(query)}"):
                self.system.open_url(f"https://open.spotify.com/search/{quote(query)}")
            return Reply(f"Searching Spotify for {query}.")
        try:
            video = self.web.youtube_first_video(query)
        except web.WebError:
            video = None
        self.system.open_url(video or self.web.youtube_search_url(query))
        return Reply(f"Playing {query} on YouTube." if video else f"Here's what YouTube found for {query}.")

    def _play_music(self, command, match):
        song = self.system.play_random_music(config.MUSIC_FOLDER)
        if song:
            return Reply(f"Playing {song}.")
        self.system.open_url("https://music.youtube.com/")
        return Reply("I didn't find any music files, so I opened YouTube Music.")

    def _key(self, key, reply, times=1):
        self.system.press(key, times)
        return Reply(reply)

    def _media_stop(self, command, match):
        return self._key("stop", "Stopped.")

    def _media_pause(self, command, match):
        return self._key("play_pause", "Paused.")

    def _media_resume(self, command, match):
        return self._key("play_pause", "Playing.")

    def _media_next(self, command, match):
        return self._key("next", "Next one.")

    def _media_previous(self, command, match):
        return self._key("previous", "Going back.")

    def _volume_set(self, command, match):
        level = min(100, int(match.group("level") or match.group("level2")))
        self.system.set_volume(level)
        return Reply(f"Volume set to {level} percent.")

    def _volume_up(self, command, match):
        return self._key("volume_up", "Volume up.", times=5)

    def _volume_down(self, command, match):
        return self._key("volume_down", "Volume down.", times=5)

    def _mute(self, command, match):
        return self._key("mute", "Done.")

    # --- alarms, timers and reminders ------------------------------------------------

    def _timer(self, command, match):
        length = alarms.parse_duration(command)
        if length is None:
            lone = re.search(r"\b(\d+)\b", alarms.words_to_numbers(command))
            length = alarms.parse_duration(f"{lone.group(1)} minutes") if lone else None  # "a timer for 5"
        if length is None:
            return Reply("How long? Say something like: set a timer for 10 minutes.")
        text = alarms.duration_text(length)
        self.alarms.add("timer", self._now() + length, text)
        return Reply(f"Timer set for {text}.")

    def _when(self, text, context=""):
        """When a reminder is for: 'in 20 minutes', 'at 5' (with `context` such as 'on friday')."""
        text = re.sub(r"\s+from now$", "", text.strip())
        relative = re.match(r"(in|after) (?!the )(.+)$", text)
        if relative:
            length = alarms.parse_duration(relative.group(2))
            return self._now() + length if length else None
        return alarms.parse_clock(f"{text} {context}", self._now(), daytime=True)

    _USUAL_HOURS = {"morning": 9, "afternoon": 14, "evening": 18, "night": 20, None: 9}

    def _usual_time(self, days, text):
        """'remind me tomorrow' (at 9), 'remind me this evening' (at 6): a day or part of it, no time."""
        now = self._now()
        hour = self._USUAL_HOURS[alarms.find_part(text)]
        moment = datetime.combine(now.date() + timedelta(days=days or 0), datetime.min.time()).replace(hour=hour)
        if moment <= now and days is None and re.search(r"\bin the (morning|afternoon|evening)\b", text):
            moment += timedelta(days=1)  # "in the morning", said in the evening
        return moment if moment > now else None

    @staticmethod
    def _is_length(command):
        """'for 1 minute', 'in half an hour': a length of time, not a time on the clock."""
        has_unit = re.search(r"\b(seconds?|secs?|minutes?|mins?|hours?|hrs?)\b", command)
        clock_like = re.search(r"\d\s*(am|pm)\b|\d[:.]\d\d|o'clock|half past|quarter (past|to)|\bat \d", command)
        return bool(has_unit and not clock_like)

    def _alarm(self, command, match):
        moment = None
        if self._is_length(command):
            length = alarms.parse_duration(command)
            moment = self._now() + length if length else None
        if moment is None and "wake" in command and not re.search(r"\b(am|pm|evening|night|afternoon|tonight)\b", command):
            moment = alarms.parse_clock(command + " am", self._now())
        moment = moment or alarms.parse_clock(command, self._now())
        if moment is None:
            return Reply("For when? Say something like: set an alarm for 7 am.")
        self.alarms.add("alarm", moment)
        return Reply(f"Alarm set for {alarms.when_text(moment, self._now())}.")

    def _reminder(self, command, match):
        rest = re.sub(r"^.*?\b(remind me|reminder)\b\s*", "", command)
        if re.match(r"for \S+( \S+)? (seconds?|secs?|minutes?|mins?|hours?|hrs?)\b", rest):
            rest = "in " + rest[4:]  # "a reminder for 10 minutes"
        elif re.match(r"for \d", rest):
            rest = "at " + rest[4:]  # "a reminder for 5 pm"
        days, day_words = alarms.find_day(rest, self._now().date())
        if days is not None and days < 0:
            days, day_words = None, ""
        part = re.search(r"\b(?:in the|this|at) (?:morning|afternoon|evening|night)\b|\btonight\b", rest)
        named = [words for words in (day_words, part.group(0) if part else "") if words]  # "on friday", "tonight"
        context = " ".join(named)
        plain = rest
        for words in named:
            plain = plain.replace(words, " ", 1)
        plain = " ".join(plain.split())
        what, moment = plain, None
        first = re.match(r"(?P<when>(?:in|at|after) .+?) (?:to|about|that) (?P<what>.+)$", plain)
        if first:  # "remind me at 5 pm to call mum"
            what, moment = first.group("what"), self._when(first.group("when"), context)
        if moment is None:  # "remind me to call mum at 5 pm": the last "in"/"at" is usually the time
            words = plain.split()
            for i in range(len(words) - 1, -1, -1):
                if words[i] in ("in", "at", "after"):
                    moment = self._when(" ".join(words[i:]), context)
                    if moment:
                        what = " ".join(words[:i])
                        break
        if moment is None and context:  # "remind me tomorrow to call mum": the usual time of day
            what, moment = plain, self._usual_time(days, rest)
        what = self._original(re.sub(r"^(to|about|that)\s+", "", what).strip()) if moment else ""
        if moment is None:
            return Reply("Say something like: remind me to call mum in 20 minutes, or at 5 pm.")
        self.alarms.add("reminder", moment, what)
        when = alarms.when_text(moment, self._now())
        return Reply(f"Okay, I'll remind you to {what} at {when}." if what else f"Okay, I'll remind you at {when}.")

    def _alarms_list(self, command, match):
        items = self.alarms.all()
        if not items:
            return Reply("You have no alarms, timers or reminders.")
        now = self._now()
        return Reply(f"You have {len(items)}: " + "; ".join(a.describe(now) for a in items) + ".", "alarms")

    def _alarms_cancel(self, command, match):
        kind = next((k for k in ("alarm", "timer", "reminder") if k in command), None)
        if command.startswith("stop") and kind != "timer":  # said just after it rang: don't take away the next one
            return Reply("Nothing is ringing right now. To remove an alarm, say: cancel my alarm.")
        count = self.alarms.clear(kind)
        noun = f"{kind}s" if kind else "alarms, timers or reminders"
        if not count:
            return Reply(f"There were no {noun} to cancel.")
        return Reply(f"Cancelled {count} {kind + ('' if count == 1 else 's') if kind else 'of them'}.")

    # --- the web ----------------------------------------------------------------------

    def _search(self, command, match):
        query = self._original(match.group("query"))
        self.system.open_url(self.web.google_url(query))
        return Reply(f"Here's what Google found for {query}.")

    def _youtube_search(self, command, match):
        query = match.group("query") or match.group("query2")
        self.system.open_url(self.web.youtube_search_url(query))
        return Reply(f"Here's what YouTube found for {query}.")

    def _wiki(self, command, match):
        query = match.group("query") or match.group("query2")
        if re.match(r"(my|our)\b", query):
            return None  # "what are my notes": about you, not for Wikipedia
        if self.ai.enabled():
            try:
                return Reply(self._ask_ai(self._raw))
            except ai.AIError:
                pass  # Wikipedia, then
        query = re.sub(r"^(a|an|the)\s+", "", query)
        summary = self.web.wikipedia_summary(query)
        if summary:
            return Reply(summary)
        self.system.open_url(self.web.google_url(query))
        return Reply(f"I couldn't find {self._original(query)} on Wikipedia, so I searched the web.")

    _NEWS_TOPICS = {"tech": "Technology", "technology": "Technology", "business": "Business", "money": "Business",
                    "economy": "Business", "finance": "Business", "science": "Science", "health": "Health",
                    "sport": "Sport", "sports": "Sport", "football": "Sport", "politics": "Politics",
                    "political": "Politics", "world": "World", "international": "World", "uk": "UK", "british": "UK",
                    "entertainment": "Culture", "culture": "Culture"}

    def _news(self, command, match):
        items = self.web.news_items()
        topic = next((self._NEWS_TOPICS[word] for word in command.split() if word in self._NEWS_TOPICS), None)
        if topic:  # "tech news", "news about sport"
            name = topic if topic == "UK" else topic.lower()
            chosen = [item for item in items if item.section == topic]
            if chosen:
                brief = analysis.brief_news(chosen, use_ai=self.ai.enabled(), brain=self.ai, label=f"{name} news")
                return Reply(brief.spoken)
            brief = analysis.brief_news(items, use_ai=self.ai.enabled(), brain=self.ai)
            return Reply(f"I couldn't find any {name} news just now. {brief.spoken}", data={"news": brief})
        brief = analysis.brief_news(items, use_ai=self.ai.enabled(), brain=self.ai)
        return Reply(brief.spoken, data={"news": brief})

    def _calculate(self, command, match):
        try:
            value = calculate(match.group("expr"))
        except ZeroDivisionError:
            return Reply("You can't divide by zero.")
        return None if value is None else Reply(f"That's {number_text(value)}.")

    def _weather(self, command, match):
        if re.match(r"(open|launch|start|run|close|quit|exit) (the |my )?(weather|forecast)( app)?$", command):
            return None  # the Weather app, not the weather
        if "temperature" in command and re.search(r"\b(cpu|gpu|processor|computer|laptop|mac|pc|battery)\b", command):
            return None
        if (re.search(r"\b(usually|often|generally|normally|typically|on average|ever)\b|\bdoes it (\w+ )?(rain|snow|get)\b",
                      command) and alarms.find_day(command, self._now().date())[0] is None
                and alarms.find_part(command) is None):
            return None  # "does it rain a lot in London?" is about the climate, not the forecast
        place = forecast.place_in(command)
        data = self.web.weather_data(place or config.HOME_CITY)
        if not data:
            if place:
                return Reply(f"I couldn't find a place called {self._original(place)}.")
            return Reply(f"I couldn't find your city, {config.HOME_CITY}. Check it in Jarvis's settings.")
        return Reply(forecast.answer(data, command))

    # --- the computer -------------------------------------------------------------------

    def _settings(self, command, match):
        return Reply("Opening the settings.", "settings")

    def _note_add(self, command, match):
        notes.add(config.NOTES_FILE, self._original(match.group("text")), self._now())
        return Reply("Noted.")

    def _note_read(self, command, match):
        found = notes.recent(config.NOTES_FILE)
        if not found:
            return Reply("You don't have any notes yet.")
        return Reply("Your latest notes: " + "; ".join(found) + ".")

    def _screenshot(self, command, match):
        return Reply("Taking a screenshot.", "screenshot")

    def _system_status(self, command, match):
        def asked(*words):
            return any(re.search(rf"\b{word}\b", command) for word in words)

        everything = not asked("battery", "charging", "charge", "cpu", "processor", "memory", "ram", "disk", "storage")
        info = self.sysinfo.snapshot()
        if info.get("cpu") is None and (everything or asked("cpu", "processor")):
            time.sleep(0.5)  # the first reading only starts the measuring
            info = self.sysinfo.snapshot()
        said = []
        if everything or asked("battery", "charging", "charge"):
            if info.get("battery") is not None:
                said.append(f"The battery is at {info['battery']} percent"
                            + (" and charging." if info.get("plugged_in") else ", not charging."))
            elif not everything:
                said.append("This computer doesn't have a battery I can read.")
        if (everything or asked("cpu", "processor")) and info.get("cpu") is not None:
            said.append(f"The processor is at {info['cpu']} percent.")
        if (everything or asked("memory", "ram")) and info.get("memory") is not None:
            said.append(f"Memory use is at {info['memory']} percent.")
        if (everything or asked("disk", "storage")) and info.get("disk") is not None:
            said.append(f"The disk is {info['disk']} percent full.")
        return Reply(" ".join(said) or "I can't read that on this computer.")

    def _power_off(self, command, match):
        return Reply("I won't shut down or restart the computer myself, so nothing unsaved gets lost. "
                     "I can lock it, though: just say lock the computer.")

    def _lock(self, command, match):
        self.system.lock()
        return Reply("Turning the screen off." if macos.available() else "Locking the computer.")

    def _installed_apps(self):
        if self._apps is None or time.monotonic() - self._apps_checked > 600:
            try:
                self._apps = self.system.installed_apps()
            except (PowerShellError, system.Unavailable, macos.MacError):
                self._apps = {}
            self._apps_checked = time.monotonic()
        return self._apps

    def _open(self, command, match):
        target = clean_target(match.group("target"))
        if target in system.SYSTEM_APPS:
            self.system.start(system.SYSTEM_APPS[target])
            return Reply(f"Opening {self._original(target)}.")
        if target in system.FOLDERS:
            folder = self.system.open_folder(target)
            return Reply(f"Opening your {target} folder." if folder else f"I couldn't find your {target} folder.")
        apps = self._installed_apps()
        wanted = system.ALIASES.get(target, target)
        exact = next((name for name in apps if name.casefold() == wanted), None)
        if exact:
            self.system.launch_app(apps[exact])
            return Reply(f"Opening {exact}.")
        if target in config.WEBSITES:
            self.system.open_url(config.WEBSITES[target])
            return Reply(f"Opening {self._original(target)}.")
        name = system.find_app(target, apps)
        if name:
            self.system.launch_app(apps[name])
            return Reply(f"Opening {name}.")
        if re.fullmatch(r"[\w-]+(\.[\w-]+)+(/\S*)?", target):
            self.system.open_url(f"https://{target}")
            return Reply(f"Opening {target}.")
        self.system.open_url(self.web.google_url(self._original(target)))
        return Reply(f"I couldn't find an app called {self._original(target)}, so I searched the web for it.")

    def _close(self, command, match):
        target = clean_target(match.group("target"))
        if target in ("jarvis", "yourself", "you"):
            if macos.available():
                return Reply("To close me, press Command Q in my window, or use Quit in my menu bar icon.")
            return Reply("To close me, use Quit in the tray icon's menu.")
        name = system.find_app(target, self._installed_apps()) or target
        closed = self.system.close_app(name)
        return Reply(f"Closing {name}." if closed else f"I couldn't find an open window for {self._original(target)}.")
