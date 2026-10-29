import io

from app.audio import AudioCapture


class _P:
    def __init__(self, text):
        self.stderr = io.BytesIO(text.encode())


def test_arecord_overruns_are_counted():
    c = AudioCapture("null")
    c._read_stderr(_P("Recording raw data 'stdin' : Signed 16 bit Little Endian, Rate 48000 Hz, Stereo\n"
                      "overrun!!! (at least 12.345 ms long)\n"
                      "overrun!!! (at least 3.100 ms long)\n"))
    assert c.stats() == {"overruns": 2, "slow": 0, "max_ms": 0.0, "restarts": 0}
    assert list(c._stderr)[-1].startswith("overrun!!!")
