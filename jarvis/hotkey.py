"""The listening key, from any app: Ctrl+Alt+J on Windows (its own small message
loop on a thread), Control+Option+J on a Mac (Carbon's hot keys, which need no
Accessibility permission)."""
import sys
import threading

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x0001, 0x0002, 0x4000
VK_J = 0x4A
HOTKEY_ID = 1


class Hotkey(threading.Thread):
    def __init__(self, on_press, api=None):
        super().__init__(daemon=True)
        self._on_press = on_press
        self._api = api
        self._thread_id = None
        self.ready = threading.Event()
        self.registered = False

    def run(self):
        api = self._api or _Win32()
        self._thread_id = api.current_thread_id()
        self.registered = api.register(HOTKEY_ID, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_J)
        self.ready.set()
        if not self.registered:
            return  # another program already uses Ctrl+Alt+J
        try:
            for message, wparam in api.messages():
                if message == WM_HOTKEY and wparam == HOTKEY_ID:
                    self._on_press()
        finally:
            api.unregister(HOTKEY_ID)

    def stop(self):
        if self._thread_id is not None:
            (self._api or _Win32()).post_quit(self._thread_id)


class _Win32:
    def __init__(self):
        import ctypes
        from ctypes import wintypes

        self._ctypes, self._wintypes = ctypes, wintypes
        self._user32 = ctypes.windll.user32
        self._kernel32 = ctypes.windll.kernel32

    def current_thread_id(self):
        return self._kernel32.GetCurrentThreadId()

    def register(self, hotkey_id, modifiers, key):
        return bool(self._user32.RegisterHotKey(None, hotkey_id, modifiers, key))

    def unregister(self, hotkey_id):
        self._user32.UnregisterHotKey(None, hotkey_id)

    def messages(self):
        msg = self._wintypes.MSG()
        while self._user32.GetMessageW(self._ctypes.byref(msg), None, 0, 0) > 0:
            yield msg.message, msg.wParam

    def post_quit(self, thread_id):
        self._user32.PostThreadMessageW(thread_id, WM_QUIT, 0, 0)


def available():
    return sys.platform in ("win32", "darwin")


def create(on_press):
    """The hot key for this computer, not started yet."""
    return MacHotkey(on_press) if sys.platform == "darwin" else Hotkey(on_press)


# --- Mac ------------------------------------------------------------------------------

def _four_chars(code):
    return int.from_bytes(code.encode("ascii"), "big")


class MacHotkey:
    """Control+Option+J. start() must run on the main thread: the key arrives through
    the app's own event loop, and on_press is called there."""

    KEY_J = 38  # kVK_ANSI_J
    CONTROL, OPTION = 0x1000, 0x0800  # controlKey, optionKey

    def __init__(self, on_press, carbon=None):
        self._on_press = on_press
        self._carbon = carbon
        self._handler = self._hotkey = self._callback = None
        self.ready = threading.Event()
        self.registered = False

    def start(self):
        try:
            carbon = self._carbon = self._carbon or _Carbon()
            self._callback = carbon.callback(self._pressed)  # kept, or ctypes would free it
            self._handler = carbon.install_handler(self._callback)
            self._hotkey = carbon.register(self.KEY_J, self.CONTROL | self.OPTION, HOTKEY_ID)
            self.registered = self._hotkey is not None
            if not self.registered:  # another app has Control+Option+J
                carbon.remove_handler(self._handler)
                self._handler = None
        except (OSError, AttributeError):
            self.registered = False  # no Carbon: no hot key, everything else still works
        finally:
            self.ready.set()

    def _pressed(self, _next_handler, _event, _data):
        try:
            self._on_press()
        except Exception:  # an error must not escape into Carbon's event loop
            import traceback

            traceback.print_exc()
        return 0  # noErr

    def stop(self):
        if self._carbon is None:
            return
        if self._hotkey is not None:
            self._carbon.unregister(self._hotkey)
            self._hotkey = None
        if self._handler is not None:
            self._carbon.remove_handler(self._handler)
            self._handler = None
        self.registered = False


class _Carbon:
    """The few Carbon Event Manager calls a global hot key needs."""

    KEYBOARD = _four_chars("keyb")  # kEventClassKeyboard
    HOT_KEY_PRESSED = 5  # kEventHotKeyPressed
    SIGNATURE = _four_chars("JRVS")

    def __init__(self):
        import ctypes

        self._ctypes = ctypes
        self._lib = ctypes.CDLL("/System/Library/Frameworks/Carbon.framework/Carbon")

        class EventTypeSpec(ctypes.Structure):
            _fields_ = [("eventClass", ctypes.c_uint32), ("eventKind", ctypes.c_uint32)]

        class EventHotKeyID(ctypes.Structure):
            _fields_ = [("signature", ctypes.c_uint32), ("id", ctypes.c_uint32)]

        self.EventTypeSpec, self.EventHotKeyID = EventTypeSpec, EventHotKeyID
        self.Handler = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
        lib = self._lib
        lib.GetEventDispatcherTarget.restype = ctypes.c_void_p
        lib.GetEventDispatcherTarget.argtypes = []
        lib.InstallEventHandler.restype = ctypes.c_int32
        lib.InstallEventHandler.argtypes = [ctypes.c_void_p, self.Handler, ctypes.c_ulong,
                                            ctypes.POINTER(EventTypeSpec), ctypes.c_void_p,
                                            ctypes.POINTER(ctypes.c_void_p)]
        lib.RemoveEventHandler.restype = ctypes.c_int32
        lib.RemoveEventHandler.argtypes = [ctypes.c_void_p]
        lib.RegisterEventHotKey.restype = ctypes.c_int32
        lib.RegisterEventHotKey.argtypes = [ctypes.c_uint32, ctypes.c_uint32, EventHotKeyID, ctypes.c_void_p,
                                            ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)]
        lib.UnregisterEventHotKey.restype = ctypes.c_int32
        lib.UnregisterEventHotKey.argtypes = [ctypes.c_void_p]

    def callback(self, function):
        return self.Handler(function)

    def install_handler(self, callback):
        ctypes = self._ctypes
        spec = self.EventTypeSpec(self.KEYBOARD, self.HOT_KEY_PRESSED)
        handler = ctypes.c_void_p()
        status = self._lib.InstallEventHandler(self._lib.GetEventDispatcherTarget(), callback, 1,
                                               ctypes.byref(spec), None, ctypes.byref(handler))
        if status != 0:
            raise OSError(f"InstallEventHandler failed ({status})")
        return handler

    def register(self, key, modifiers, hotkey_id):
        ctypes = self._ctypes
        ref = ctypes.c_void_p()
        status = self._lib.RegisterEventHotKey(key, modifiers, self.EventHotKeyID(self.SIGNATURE, hotkey_id),
                                               self._lib.GetEventDispatcherTarget(), 0, ctypes.byref(ref))
        return ref if status == 0 else None  # -9878: another app already has it

    def unregister(self, ref):
        self._lib.UnregisterEventHotKey(ref)

    def remove_handler(self, handler):
        self._lib.RemoveEventHandler(handler)
