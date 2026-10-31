"""A small FLAC writer in pure Python, for sending speech to Google's recogniser.

SpeechRecognition makes FLAC with a `flac` program it carries, but its Mac copy is
built for Intel only, so Apple silicon Macs can't run it. FLAC can also hold audio
as it is ("verbatim"), which is simple to write and which every decoder reads; a few
seconds of speech is then a few hundred kB instead of half that, which is fine.
"""
import array
import struct
import sys

BLOCK = 4096  # samples per frame
# frame header codes for common sample rates; others are taken from the stream's header
RATE_CODES = {8000: 0b0100, 16000: 0b0101, 22050: 0b0110, 24000: 0b0111, 32000: 0b1000, 44100: 0b1001,
              48000: 0b1010, 96000: 0b1011}


def _table(polynomial, width):
    top, mask = 1 << (width - 1), (1 << width) - 1
    table = []
    for byte in range(256):
        crc = byte << (width - 8)
        for _ in range(8):
            crc = ((crc << 1) ^ polynomial) if crc & top else crc << 1
        table.append(crc & mask)
    return table


_CRC8, _CRC16 = _table(0x07, 8), _table(0x8005, 16)


def crc8(data):
    crc = 0
    for byte in data:
        crc = _CRC8[crc ^ byte]
    return crc


def crc16(data):
    crc = 0
    for byte in data:
        crc = ((crc << 8) & 0xFFFF) ^ _CRC16[(crc >> 8) ^ byte]
    return crc


def _coded_number(n):
    """A frame number, coded the way UTF-8 codes characters."""
    if n < 0x80:
        return bytes([n])
    payload = []
    while True:
        payload.insert(0, 0x80 | (n & 0x3F))
        n >>= 6
        room = 6 - len(payload)  # bits left in the first byte
        if n < (1 << room):
            first = (0xFF << (room + 1)) & 0xFF
            return bytes([first | n] + payload)


def encode(pcm, rate):
    """16-bit mono PCM (little-endian, as in a WAV file) -> the bytes of a FLAC file."""
    samples = array.array("h")
    samples.frombytes(bytes(pcm[: len(pcm) // 2 * 2]))
    if sys.byteorder == "little":
        samples.byteswap()  # FLAC stores samples big-endian
    data, total = samples.tobytes(), len(samples)

    stream_info = (struct.pack(">HH", BLOCK, BLOCK) + bytes(6)  # block sizes; frame sizes unknown
                   + ((rate << 44) | (0 << 41) | (15 << 36) | total).to_bytes(8, "big")  # mono, 16 bits
                   + bytes(16))  # no MD5
    parts = [b"fLaC", bytes([0x80]) + len(stream_info).to_bytes(3, "big"), stream_info]

    rate_code = RATE_CODES.get(rate, 0)
    for number, start in enumerate(range(0, total, BLOCK)):
        count = min(BLOCK, total - start)
        header = bytes([0xFF, 0xF8,  # sync code; fixed block size
                        0x70 | rate_code,  # block size in 16 bits at the end of the header
                        0x08])  # mono, 16 bits per sample
        header += _coded_number(number) + struct.pack(">H", count - 1)
        header += bytes([crc8(header)])
        frame = header + b"\x02" + data[start * 2:(start + count) * 2]  # one verbatim subframe
        parts.append(frame + struct.pack(">H", crc16(frame)))
    return b"".join(parts)
