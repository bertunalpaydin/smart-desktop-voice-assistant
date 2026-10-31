from jarvis import hotkey


class FakeApi:
    def __init__(self, messages, register_ok=True):
        self._messages = messages
        self.register_ok = register_ok
        self.registered = []
        self.unregistered = []
        self.quit_posted = []

    def current_thread_id(self):
        return 42

    def register(self, hotkey_id, modifiers, key):
        self.registered.append((hotkey_id, modifiers, key))
        return self.register_ok

    def unregister(self, hotkey_id):
        self.unregistered.append(hotkey_id)

    def messages(self):
        yield from self._messages

    def post_quit(self, thread_id):
        self.quit_posted.append(thread_id)


def test_ctrl_alt_j_calls_back_and_other_messages_are_ignored():
    pressed = []
    api = FakeApi([(hotkey.WM_HOTKEY, hotkey.HOTKEY_ID), (0x0200, 0), (hotkey.WM_HOTKEY, 99),
                   (hotkey.WM_HOTKEY, hotkey.HOTKEY_ID)])
    key = hotkey.Hotkey(lambda: pressed.append(1), api=api)
    key.run()
    assert pressed == [1, 1]
    assert api.registered == [(1, hotkey.MOD_CONTROL | hotkey.MOD_ALT | hotkey.MOD_NOREPEAT, ord("J"))]
    assert api.unregistered == [1]
    assert key.registered and key.ready.is_set()
    key.stop()
    assert api.quit_posted == [42]


def test_when_another_program_has_the_hotkey():
    api = FakeApi([(hotkey.WM_HOTKEY, hotkey.HOTKEY_ID)], register_ok=False)
    pressed = []
    key = hotkey.Hotkey(lambda: pressed.append(1), api=api)
    key.run()
    assert not key.registered and key.ready.is_set()
    assert pressed == [] and api.unregistered == []


def test_works_as_a_thread():
    pressed = []
    key = hotkey.Hotkey(lambda: pressed.append(1), api=FakeApi([(hotkey.WM_HOTKEY, hotkey.HOTKEY_ID)]))
    key.start()
    key.join(2)
    assert pressed == [1]
