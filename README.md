# Jarvis

A voice assistant for the desktop, for Windows and macOS, written in Python. Say **"Hey Jarvis"** (or press **Ctrl+Alt+J**, on a Mac **Control+Option+J**) and give a command: play music, set alarms, timers and reminders, open apps and websites, search the web, check the weather, or deal with email. It sits in the system tray (the menu bar on a Mac) and also watches Gmail in the background: important mail gets a notification and is read out loud.

Its main screen is a full-screen HUD in the style of the films: a turning arc reactor shows when Jarvis is listening, thinking or talking, surrounded by the weather, the computer's state, today's alarms, an analysed inbox (what needs a reply, what is important, what can wait) and the day's news, grouped across BBC News, The Guardian and Sky News. With an optional Claude API key, Claude writes the briefings and answers anything Jarvis has no command for.

Personal project.

## What it can do

| Say | What happens |
| --- | --- |
| "Play Bohemian Rhapsody" | Finds it on YouTube and plays it in the browser |
| "Play some music" | A random song from your Music folder |
| "Play lofi on Spotify" | Searches in the Spotify app |
| "Pause", "Resume", "Next song", "Turn it up", "Mute" | Media and volume keys, so it works with YouTube, Spotify and most players |
| "Set the volume to 30" | Sets the volume to 30% |
| "Set an alarm for 7 am", "Wake me up at 6:30 on Saturday", "Set an alarm for 10 minutes" | An alarm that rings until you stop or snooze it, by clicking or by saying "stop" or "snooze" |
| "Set a timer for 10 minutes" | A timer |
| "Remind me to call Mum at 6 pm", "...in 20 minutes", "...on Friday", "Remind me tomorrow morning to ..." | A reminder, said out loud when it is time (a day without a time means 9 am; "at 3" means 3 pm) |
| "What alarms do I have?", "When is my alarm?", "How long is left?", "Cancel my timers" | Lists them (with the time left on timers) or cancels them |
| "Check my email", "Read my emails" | Counts unread and important mail, reads the important ones |
| "Read my last email", "Read my last 3 emails", "Who emailed me last?" | The newest mail in the inbox, important or not (leaving out Promotions and Social) |
| "Send an email to Mum" | Asks for the subject and the text, then opens the mail for you to check and send |
| "Open Spotify", "Open calculator", "Open GitHub", "Open my downloads" | Any app in the Start menu (even when the name is heard slightly wrong), Windows' own apps, a website or one of your folders. If nothing matches, it searches the web for it |
| "Close Spotify" | Closes the app's windows, as if you clicked X (it can still ask to save your work) |
| "Search for Python decorators", "Search YouTube for lofi" | Opens the results |
| "Who was Alan Turing?", "What is a black hole?" | Reads the start of the Wikipedia article |
| "What's the weather?", "Weather in London" | Current weather and today's forecast |
| "What's the weather tomorrow?", "...on Saturday afternoon", "...this weekend", "...this week" | The forecast for the day, part of the day or days asked about, up to a week ahead |
| "Will it rain tomorrow?", "Do I need an umbrella?", "Is it raining?", "Will it snow on Monday?", "How windy is it?", "When is sunset?" | A straight answer, with the numbers behind it |
| "What's the news?", "What's the tech news?" | A spoken briefing on the day's news (or one section of it). Stories reported by several outlets come first |
| "Good morning", "Brief me", "What did I miss?" | The daily briefing: time, weather, what's left today, the inbox and the top stories |
| "Analyse my email", "Anything important in my email?" | Sorts unread mail into needs a reply, important and the rest, with deadlines, and sums it up |
| "Open the HUD" | The full-screen dashboard |
| "What's 15% of 80?", "What's 25 times 4?" | Sums |
| "Take a note: exam on Monday", "Read my notes" | Short notes |
| "Take a screenshot", "Lock the computer" | |
| "How much battery do I have?", "How's my computer doing?" | Battery, processor, memory and disk |
| "What time is it?", "What time is it in Tokyo?", "What's the date tomorrow?", "What date is next Friday?", "Tell me a joke", "Help" | |

Commands can also be typed in the Jarvis window. Pressing Ctrl+Alt+J while Jarvis is talking stops it.

The mail part:

- **Importance rules**: VIP senders, keywords, Gmail's own "important" marker and its promotions/social tabs. Each mail shows why it was rated the way it was.
- **Notifications**: a Windows notification, and optionally a spoken announcement, for each new important mail. Each mail is announced once.
- **Reading aloud**: links, quoted earlier mail and signatures are skipped.
- **Replying and writing**: dictate or type, edit the text, then confirm. Replies stay in the same Gmail conversation. Nothing is ever sent by voice alone.

## Install (Windows)

Needs Python 3.10 or newer.

### 1. Google Cloud setup (once, for the mail part)

1. Create a project in the [Google Cloud console](https://console.cloud.google.com/) and enable the **Gmail API** for it.
2. Google Auth Platform > **Branding**: click *Get started*, fill in an app name and your email. With a personal @gmail.com account choose **External** as the audience.
3. Google Auth Platform > **Audience**: add your own Gmail address under *Test users*.
4. Google Auth Platform > **Clients** > *Create client* > type **Desktop app**. Download the JSON file.
5. Save it as `secrets/credentials.json` in the project. On first use it is copied to `%LOCALAPPDATA%\jarvis` (on a Mac, `~/Library/Application Support/Jarvis`), where the login is kept too.

### 2. Install

Double-click `install.bat`. It installs the packages and puts a **Jarvis** shortcut on the desktop.

Start Jarvis from that shortcut. The first time:

- the "Hey Jarvis" model is downloaded (a few MB);
- for mail, a browser opens for the Google login. Google warns that the app isn't verified because it is your own project; choose *Advanced*, continue, and tick both permissions (read and send).

If you used the earlier Mail Assistant version, its login and settings are taken over automatically, and its desktop shortcut is replaced.

## Install (Mac)

Needs an Apple silicon Mac (M1 or later) with macOS 13 Ventura or newer, and Python 3.13 from [python.org](https://www.python.org/downloads/macos/) (or Homebrew's `python@3.13`). Not 3.14 or 3.15 yet: onnxruntime, which runs the "Hey Jarvis" model, has no build of them for macOS 13.

1. For mail, do the Google Cloud setup above and put `credentials.json` in `secrets/`.
2. In Terminal, run `bash macos/install.sh` from the project folder.
3. Open **Jarvis** from Launchpad, Spotlight or `~/Applications`. The first time, macOS asks whether Jarvis may use the microphone: choose *Allow*.

The installer needs no administrator password and changes nothing outside your user folder:

- It makes a private Python environment in `~/Library/Application Support/Jarvis/venv`, so nothing is installed into the system's or python.org's Python.
- It installs the exact package versions in `macos/requirements-py313.txt` from PyPI only. pip checks every file against the SHA-256 hash in that list and accepts only ready-made packages (wheels), so no package runs code while it is installed. The list is made from `macos/requirements.in` with `uv pip compile --generate-hashes`.
- It copies the code to `~/Library/Application Support/Jarvis/app` and builds `Jarvis.app` in `~/Applications` with py2app's alias mode: a small app that runs that code with that environment. The app is built on your Mac, so Gatekeeper has nothing to block.
- It ends with `macos/check.py`, which tries every Mac feature Jarvis uses without changing anything. Everything it prints is also saved in `macos/install.log`.

Run it again after changing the code to update Jarvis. `bash macos/uninstall.sh` removes the app, the environment and the login item, and keeps your data unless you add `--everything`.

On a Mac, Jarvis uses macOS's own tools: `say` for its voice (Daniel, a British voice, when no other is chosen), `afplay` for sounds, notifications through AppleScript, `open` for apps, files and folders, and `pmset` to turn the screen off. Pausing and skipping work in Spotify and Apple Music, through AppleScript; macOS asks once whether Jarvis may control each of them. A screenshot asks once for Screen Recording permission. Because the app is signed on your Mac rather than by Apple, macOS asks for the microphone again after each reinstall.

### A human voice (Mac, optional)

Jarvis can speak with [Chatterbox](https://github.com/resemble-ai/chatterbox), an open speech model by Resemble AI (MIT licence), instead of the Mac's voices: Chatterbox Nano (fast, the default) or Chatterbox Turbo (a little better, slower; add `--turbo` to the installer). It runs on your Mac (on its graphics chip), so nothing you say goes anywhere, and it can sound like a short recording: your own voice, or one of someone who agreed to it.

1. Install Jarvis first (above), then run `bash macos/install_voice.sh`. It takes a few minutes and about 4 GB of disk space.
2. In Jarvis: *Settings > Human voice*, choose *Human voice: Chatterbox* and *Save*. *Model* is *Automatic* at first: Turbo, which sounds better, if your Mac makes speech with it faster than it is spoken, otherwise Nano. *Effect* can make the voice deeper and metallic, as if through a mask (a sound effect made by Jarvis, not anyone's voice). *Record my voice...* records 12 seconds of you reading a short text; *Use a recording...* takes a recording of 10 to 15 seconds (WAV, M4A, MP3 or AIFF).

What the voice installer does, again only inside your user folder and without an administrator password:

- It makes a second environment, `~/Library/Application Support/Jarvis/voice-engine/venv`, so PyTorch and the model never touch Jarvis's own.
- It installs the exact versions in `macos/voice/requirements-py313.txt` from PyPI only, checked against their SHA-256 hashes, ready-made packages only. The list leaves out what Chatterbox lists but never uses for English (its Gradio demo, the Chinese and Japanese text tools, and a developers' tool that one dependency lists by mistake).
- `macos/voice/download_model.py` downloads one fixed version of the model from Resemble AI's page on Hugging Face and checks every file against the checksum Hugging Face lists for it; it stops if Hugging Face's malware scan flagged any file. Only weights (`.safetensors`), the built-in voice (read with `weights_only`, so it can only hold numbers) and the tokenizer's text files are downloaded.
- It measures each model on the Mac's graphics chip and on its processor, and Jarvis uses whichever was faster.

`jarvis/voice_worker.py` runs the model as a separate process; `jarvis/human_voice.py` keeps the delay short. The first piece of a reply is short (up to the first comma), and each piece plays as soon as it's ready while the next one is made. Jarvis's usual replies ("Okay.", "Next one.", the greetings) are made while it's idle, and everything it says is cached, so those come out at once. The model warms up when it loads, so the first reply isn't slow either. While the model loads (a few seconds after Jarvis starts), or if it ever fails, Jarvis uses the Mac's voice. Everything Chatterbox says carries Resemble AI's inaudible Perth watermark, which marks it as made by AI. `bash macos/uninstall.sh` removes it with Jarvis; your recordings stay unless you add `--everything`.

## Using it

Say "Hey Jarvis" and the command, in one go ("Hey Jarvis, what time is it?") or with a pause after the chime. Or press Ctrl+Alt+J, or click the tray icon and type, or click one of the suggestions in the Jarvis window.

"Open settings" opens the Windows settings; "open Jarvis settings" opens Jarvis's own.

### The HUD

Clicking the tray icon opens the HUD (it also opens when Jarvis starts, unless you turn that off in Settings > Jarvis). The weather and news refresh every 15 minutes while it is open, the inbox with every mail check. Click a mail to open the inbox, or a story or a headline in the ticker to read it in the browser. Hovering over the ticker stops it. F11 switches to full screen. The animations only run while the HUD is on screen, so it costs nothing when it's closed.

### Claude (optional)

Without an API key Jarvis analyses mail and news with its own rules, offline. With a key from [console.anthropic.com](https://console.anthropic.com) in Settings > AI:

- Claude writes the news briefing, in Jarvis's voice.
- Anything Jarvis has no command for ("why is the sky blue?", "and how far away is it?") goes to Claude, which remembers the last few questions.
- Mail goes to Claude only if you tick "Let Claude analyse my unread email"; it then sees the sender, subject and first lines of each unread mail.

The API is paid per use; Claude Haiku costs least. If Claude can't be reached, Jarvis says so and falls back to its own rules.

Right-click the tray icon for the menu: talk to Jarvis, the Jarvis window, the inbox, checking mail now, reading important mail aloud, new mail, alarms and reminders, stop speaking, the "Hey Jarvis" switch, pausing mail checks, and the settings.

In **Settings**:

- *General*: how often to check mail, the voice and its speed, the language you speak, start with Windows.
- *Jarvis*: "Hey Jarvis" on or off and how easily it wakes up (if it often misses you, choose *High*; if it wakes up by itself, choose *Low*), Ctrl+Alt+J on or off, your city for the weather, your music folder, **contacts** for "send an email to ..." (`Mum = mum@example.com`, one per line) and extra **websites** for "open ..." (`Uni = https://www.uwe.ac.uk/`).
- *Mail rules*: VIP domains and senders, muted domains, keywords.

On Windows 11 the icon may start out hidden behind the `^` arrow next to the clock. Drag it onto the taskbar to keep it visible.

### Command line

```
python main.py ask "set a timer for 10 minutes"   # give Jarvis a typed command
python main.py talk           # say one command
python main.py                # list unread mail and how each one is rated
python main.py read           # read important unread mail aloud
python main.py read 3         # read mail number 3 from the list aloud
python main.py reply 2        # answer mail number 2, by voice or typing (--type: typing only)
python main.py send someone@example.com  # write a new mail
python main.py watch          # check mail regularly in the terminal (--quiet: no reading aloud)
python main.py voices         # list the installed voices and play a test
python main.py toast          # show a test notification
python main.py app            # start Jarvis in the tray
python main.py shortcut       # make the desktop shortcut again
```

In the terminal a mail is sent only when you type `y`.

## What counts as important mail

A mail needs 2 points:

- sender in the VIP domains or VIP senders: +3
- a keyword in the subject: +2 (only in the preview text: +1)
- marked important by Gmail: +1
- Gmail promotions or social tab: -3
- sender in the muted domains: never important

The defaults are in `jarvis/config.py`. Changes made in the settings window are saved to `%LOCALAPPDATA%\jarvis\settings.json` and override them.

## How it works

- `wakeword.py`: listens for "Hey Jarvis" with [openWakeWord](https://github.com/dscripka/openWakeWord), a small neural network that runs offline on its own thread. After the wake word it keeps the same microphone stream open and records the command, including what was said straight after "Jarvis" (it keeps the last second of audio). The command ends after about a second of quiet; what counts as quiet is measured from the room's own background noise. Right after the microphone opens, the model's first scores are ignored, because it can give false scores while it warms up. It lets go of the microphone while Jarvis talks, so it never hears itself.
- `hotkey.py`: Ctrl+Alt+J through the Win32 `RegisterHotKey` call, with its own message loop on a background thread. On a Mac, Control+Option+J through Carbon's `RegisterEventHotKey` (called with `ctypes`), which needs no Accessibility permission; the key arrives through the app's own event loop.
- `dictation.py`: turns recorded audio into text with `SpeechRecognition` and Google's speech service, asking for all of its guesses. `commands.best()` then picks the first guess that is a command Jarvis knows, which helps with accents. Without "Hey Jarvis", it records with `sounddevice`, measuring the room noise before the chime so it isn't measuring your voice.
- `commands.py`: the brain. An ordered list of patterns maps the text to an intent ("timer", "play", "open", ...), and each intent has a small handler. Services such as the web, the computer and Gmail are passed in, so every command is tested with fakes.
- `alarms.py`: understands times and days ("half past six", "7 am tomorrow", "Friday evening at 8", "next Monday", "in an hour and a half") and keeps alarms, timers and reminders in a JSON file, so they survive a restart. "Next Friday" is next week's Friday; "this Friday" or "on Friday" is the coming one.
- `forecast.py`: answers weather questions from the forecast by day and by hour: which place (but not "in the morning" or "for tomorrow"), which day or part of it, and whether the question is really about rain, snow, wind or the sun.
- `web.py`: YouTube search, Wikipedia's REST API, the weather and the RSS feeds of BBC News, The Guardian and Sky News, read in parallel. None of them need an API key. The weather comes from [Open-Meteo](https://open-meteo.com/); when it doesn't answer, from [MET Norway](https://api.met.no/) instead (the HUD then says "from MET Norway"), with sunrise and sunset worked out on the computer (NOAA's formula). A request that times out or finds the service busy is tried once more, and the HUD tries again after 1, 2 and 5 minutes, keeping the last weather on screen. Places that were found once are remembered.
- `analysis.py`: groups headlines about the same story (shared keywords between titles), ranks stories by how many outlets report them and where, and finds the words that keep coming up. For mail it sorts each message into needs a reply, important, update or low priority from Gmail's labels, the importance rules and the wording (questions, requests, deadlines like "by Friday"), and writes the spoken summary. With Claude, it asks for the same as JSON and falls back to the rules for anything missing or malformed.
- `ai.py`: a small client for Anthropic's Messages API using only the standard library. Errors (bad key, no credit, rate limits, no internet) become plain sentences Jarvis can say.
- `hud.py`: the HUD, drawn with `QPainter`: the arc reactor, framed panels, meters and the news ticker. `sysinfo.py` reads the processor, memory and battery through Windows' own API (`ctypes`), with no extra package, and through `psutil` on a Mac.
- `system.py`: media and volume keys, the Start menu app list (`Get-StartApps`) and matching spoken app names against it (ignoring spaces and punctuation, then the closest name with `difflib`), closing apps, folders, locking the screen.
- `gmail_client.py`: Gmail API through `google-api-python-client`. Lists unread mail, turns MIME parts (plain text or HTML) into readable text, and builds replies with the right `In-Reply-To` and `References` headers.
- `importance.py`, `watcher.py`, `store.py`: the scoring rules and the background check. The ids of mail already seen are kept in SQLite, so nothing is announced twice.
- `notifier.py`, `speech.py`, `powershell.py`: Windows notifications and System.Speech through Windows PowerShell. Speech can be stopped half-way.
- `app.py`: the PyQt6 tray app with a dark theme (one Qt style sheet), the HUD, the Jarvis panel (an animated circle shows when it is listening, thinking or talking) and the other windows. Everything slow (Gmail, speech, the microphone, the web) runs on one worker `QThread`, one job at a time. The windows talk to it through signals, so they never freeze.
- `settings.py`, `windows.py`, `startup.py`: `settings.json`, starting with Windows (a value under the `Run` registry key) or with a Mac (a launch agent in `~/Library/LaunchAgents`), and the desktop shortcut.
- `macos.py`: everything Mac-specific, using only the tools that come with macOS. Text never becomes part of a script: it reaches `say` on stdin and AppleScript as arguments, each prefixed with a letter so that a mail subject such as `-e ...` can't be read as an option.

## Privacy and security

- "Hey Jarvis" is recognised on the computer itself. Only the command you say after it goes to Google's speech service to be turned into text.
- For mail, the app asks Google for permission to read and to send, nothing else, so it can't delete or change anything. Nothing is sent without a final confirmation, and never by voice alone.
- The login, settings, alarms, notes and the list of mail already seen live in `%LOCALAPPDATA%\jarvis`, outside the project, so OneDrive and git never pick them up. `secrets/` is git-ignored as well.
- Text from mail is passed to PowerShell as data (base64 environment variables) and never pasted into a script, so a hostile subject line can't run as code.
- The Claude API key is stored in `settings.json` in the same folder. Mail is sent to Claude only if you allow it in Settings > AI, and Claude's analysis is only shown and read out; it can't make Jarvis do anything.
- While the Google consent screen is in *Testing*, the login expires after 7 days. The app then opens the browser to log in again.

## Limits

- Commands are in English.
- Alarms ring while Jarvis is running and the computer is awake; one that was missed rings as soon as Jarvis starts again.
- Spotify can be searched and controlled with the media keys, but starting a song directly would need Spotify's own API.
- On a Mac, pause, skip and previous work only in Spotify and Apple Music (Windows' media keys reach any player), and "lock the computer" turns the screen off, which locks it if your Mac asks for the password after sleep.
- Speech recognition needs the internet. Background noise or music playing loudly makes "Hey Jarvis" harder to hear; Ctrl+Alt+J always works.
- The offline news grouping works on headline words, so two very differently worded headlines about one story can stay apart.

## Tests

```
pip install -r requirements-dev.txt
pytest
```

The tests use fakes for Gmail, PowerShell, macOS's tools, the web, the microphone and the wake word model, and the window tests run without a screen. No login, internet, Windows or Mac is needed.

## Project layout

```
app.pyw               starts Jarvis without a console window
install.bat           one-time setup on Windows: packages and desktop shortcut
macos/
    install.sh        setup on a Mac: private Python, locked packages, Jarvis.app
    uninstall.sh      removes it again
    install_voice.sh  the human voice (optional): its own environment, the model
    requirements*     the Mac's package list, with SHA-256 hashes
    voice/            the human voice's package lists and its checked model download
    setup.py          py2app settings for Jarvis.app
    launcher.py       what Jarvis.app runs
    check.py          tries the Mac features after installing
    make_icon.py      draws Jarvis.icns
main.py               command line
jarvis/
    app.py            tray app and windows (PyQt6)
    ai.py             Claude through Anthropic's API (optional)
    alarms.py         alarms, timers, reminders and time parsing
    analysis.py       news and mail analysis
    auth.py           Google login
    commands.py       understanding and carrying out commands
    compose.py        writing mail in the terminal
    config.py         defaults
    dictation.py      microphone to text
    flac.py           sound in FLAC for the speech service (the Mac's own copy is Intel only)
    forecast.py       answers to weather questions
    gmail_client.py   Gmail API
    hotkey.py         Ctrl+Alt+J (Windows), Control+Option+J (Mac)
    hud.py            the full-screen HUD
    human_voice.py    the human voice on a Mac: starts the worker, plays its speech
    icons.py          button icons
    importance.py     rating rules
    macos.py          the Mac's voice, sounds, notifications, apps, players, volume, login item
    models.py         Mail and Draft
    notes.py          notes
    notifier.py       Windows notifications
    powershell.py     runs fixed PowerShell scripts safely
    settings.py       settings.json
    startup.py        opening Jarvis when you log in (Windows and Mac)
    sound.py          alarm sounds
    speech.py         reading aloud
    store.py          mail already seen (SQLite)
    sysinfo.py        processor, memory, battery and disk readings
    system.py         media keys, apps, music, locking
    wakeword.py       "Hey Jarvis"
    watcher.py        checking for new mail
    voice_worker.py   runs Chatterbox-Turbo in the voice's own environment
    web.py            YouTube, Wikipedia, weather, places and their time, news feeds
    windows.py        start with Windows, desktop shortcut
tests/
```

## Credits

Weather data by [Open-Meteo](https://open-meteo.com/) (CC BY 4.0) and, as a fallback, by [MET Norway](https://api.met.no/) (CC BY 4.0). News from the RSS feeds of BBC News, The Guardian and Sky News.
