"""Stands in for the human voice: the real worker's serve() and Engine (with its cache), with a
pretend model instead of Chatterbox, so human_voice.Engine can be tested with a real process.
Text with BAD fails, CRASH ends the process, SLOW takes a second. With FAIL_LOAD in the environment
the model "doesn't load"; with NO_VOICE it has no voice of its own."""
import os
import sys
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis import voice_worker  # noqa: E402


class Pretend(voice_worker.Engine):
    def __init__(self, cache):
        conds = None if os.environ.get("NO_VOICE") else "built-in"
        super().__init__(types.SimpleNamespace(sr=8000, conds=conds), "cpu", cache, "pretend")

    def speak(self, text, sample=None):
        import numpy

        if "BAD" in text:
            raise ValueError("cannot say that")
        if "CRASH" in text:
            os._exit(3)
        if "SLOW" in text:
            time.sleep(1)
        if not sample and self.built_in is None:
            raise RuntimeError("this model has no voice of its own")
        return numpy.zeros(80 + 10 * len(text), dtype="float32")  # a hundredth of a second and a bit


def load(cache):
    if os.environ.get("FAIL_LOAD"):
        raise MemoryError("not enough memory")
    return Pretend(cache)


if __name__ == "__main__":
    out = sys.argv[sys.argv.index("--out") + 1]
    cache = sys.argv[sys.argv.index("--cache") + 1] if "--cache" in sys.argv else None
    sys.exit(voice_worker.serve(lambda: load(cache), out, sys.stdin, voice_worker._protocol_output()))
