import sys

from . import macos, powershell

# Windows PowerShell's own app id, so a toast works without registering anything.
APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"

# This script never changes. Mail text goes in as base64 environment variables
# and is added to the toast as plain text nodes, so a hostile subject line can't
# run as PowerShell code or be read as markup.
_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
function Decode($name) {
    $value = [Environment]::GetEnvironmentVariable($name)
    if ($value) { [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($value)) } else { '' }
}
try {
    [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
    [Windows.UI.Notifications.ToastNotification, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
    [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null

    $xml = New-Object Windows.Data.Xml.Dom.XmlDocument
    $xml.LoadXml('<toast><visual><binding template="ToastGeneric"><text/><text/></binding></visual></toast>')
    $nodes = $xml.GetElementsByTagName('text')
    $nodes.Item(0).AppendChild($xml.CreateTextNode((Decode 'MA_TITLE'))) | Out-Null
    $nodes.Item(1).AppendChild($xml.CreateTextNode((Decode 'MA_BODY'))) | Out-Null

    $toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($env:MA_APP_ID).Show($toast)
}
catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
"""


def _is_windows():
    return sys.platform == "win32"


def _clean(text, limit):
    text = "".join(ch for ch in " ".join(str(text).split()) if ch.isprintable())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _show_toast(title, message):
    env = {"MA_APP_ID": APP_ID, "MA_TITLE": powershell.b64(title), "MA_BODY": powershell.b64(message)}
    try:
        powershell.run(_SCRIPT, env, timeout=30)
    except powershell.PowerShellError as exc:
        return False, str(exc)
    return True, ""


def notify(title, message):
    """Show a Windows toast or a Mac notification. Anywhere else, or if that fails,
    print instead. Returns True only if a notification was shown."""
    title = _clean(title, 80) or "Jarvis"
    message = _clean(message, 200) or "(no subject)"
    if macos.available():
        try:
            macos.notify(title, message)
            return True
        except macos.MacError as exc:
            print(f"(notification failed: {exc})")
    elif _is_windows():
        shown, error = _show_toast(title, message)
        if shown:
            return True
        print(f"(toast failed: {error})")
    print(f"[notification] {title} - {message}")
    return False
