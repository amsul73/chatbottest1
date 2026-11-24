import math
import struct

import pytest

SAMPLE_RATE = 16000


def tone(seconds: float, amplitude: int = 9000, freq: int = 220) -> bytes:
    n = int(SAMPLE_RATE * seconds)
    return b"".join(
        struct.pack("<h", int(amplitude * math.sin(2 * math.pi * freq * i / SAMPLE_RATE)))
        for i in range(n)
    )


def silence(seconds: float) -> bytes:
    return b"\x00\x00" * int(SAMPLE_RATE * seconds)


@pytest.fixture
def make_tone():
    return tone


@pytest.fixture
def make_silence():
    return silence
