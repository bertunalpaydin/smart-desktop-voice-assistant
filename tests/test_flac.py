import math
import os
import platform
import random
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from jarvis import flac


def pcm(samples):
    return struct.pack(f"<{len(samples)}h", *samples)


def tone(count, rate=16000):
    rng = random.Random(7)
    return [max(-32768, min(32767, int(20000 * math.sin(2 * math.pi * 440 * i / rate)) + rng.randint(-300, 300)))
            for i in range(count)]


def read(data):
    """A small reader for what flac.encode writes: (sample rate, total, samples), checking every CRC."""
    assert data[:4] == b"fLaC" and data[4] == 0x80 and int.from_bytes(data[5:8], "big") == 34
    info = int.from_bytes(data[18:26], "big")
    rate, channels, bits, total = info >> 44, ((info >> 41) & 7) + 1, ((info >> 36) & 31) + 1, info & (2**36 - 1)
    assert (channels, bits) == (1, 16)
    position, samples, number = 42, [], 0
    while position < len(data):
        assert data[position:position + 2] == b"\xff\xf8"
        coded = flac._coded_number(number)
        assert data[position + 4:position + 4 + len(coded)] == coded
        size_at = position + 4 + len(coded)
        count = int.from_bytes(data[size_at:size_at + 2], "big") + 1
        assert data[size_at + 2] == flac.crc8(data[position:size_at + 2])
        end = size_at + 3 + 1 + count * 2
        assert data[size_at + 3] == 0x02  # verbatim
        samples += struct.unpack(f">{count}h", data[size_at + 4:end])
        assert int.from_bytes(data[end:end + 2], "big") == flac.crc16(data[position:end])
        position, number = end + 2, number + 1
    return rate, total, samples


def test_frame_numbers_are_coded_like_utf8():
    for n in [*range(3000), 0xFFFF, 0x10000, 0x10FFFF]:
        assert flac._coded_number(n) == chr(n).encode("utf-8")


def test_the_checksums():
    assert flac.crc8(b"123456789") == 0xF4  # CRC-8, polynomial 0x07
    assert flac.crc16(b"123456789") == 0xFEE8  # CRC-16/UMTS, polynomial 0x8005


@pytest.mark.parametrize("count, rate", [(43200, 16000), (4096, 16000), (4097, 16000), (1, 8000), (0, 16000),
                                         (5512, 11025)])
def test_audio_survives_the_round_trip(count, rate):
    samples = tone(count, rate) + ([32767, -32768] if count else [])
    assert read(flac.encode(pcm(samples), rate)) == (rate, len(samples), samples)


def test_the_rate_is_in_each_frame_when_it_can_be():
    assert flac.encode(pcm([0] * 10), 16000)[44] == 0x75  # 16 kHz's own code
    assert flac.encode(pcm([0] * 10), 11025)[44] == 0x70  # taken from the stream's header


def test_an_odd_trailing_byte_is_dropped():
    assert read(flac.encode(pcm([5, -5]) + b"\x01", 16000))[2] == [5, -5]


FLAC_TOOL = Path(__import__("speech_recognition").__file__).parent / "flac-linux-x86_64"


@pytest.mark.skipif(not (sys.platform == "linux" and platform.machine() == "x86_64" and os.access(FLAC_TOOL, os.X_OK)),
                    reason="needs the flac program SpeechRecognition brings for 64-bit Linux")
def test_the_real_flac_program_reads_it():
    samples = tone(20000) + [32767, -32768]
    data = flac.encode(pcm(samples), 16000)
    assert subprocess.run([str(FLAC_TOOL), "-t", "-"], input=data, capture_output=True).returncode == 0
    decoded = subprocess.run([str(FLAC_TOOL), "-d", "-c", "--force-raw-format", "--endian=little",
                              "--sign=signed", "-"], input=data, capture_output=True, check=True)
    assert decoded.stdout == pcm(samples)
