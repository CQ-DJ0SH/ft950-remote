"""Low-level driver for the Yaesu FT-950 CAT protocol.

The radio speaks ASCII commands of two letters plus fixed-width parameters,
terminated by ``;`` (e.g. ``FA14074000;``). Read commands are answered with the
same layout; **set commands are never acknowledged** — unlike the Kenwood
protocol there is no ``?`` on a rejected value. An out-of-range set (e.g.
``SH020;`` in DATA-U) is silently dropped. Every write that matters is therefore
"fire & verify": send the set, read the value back, compare.

This module owns the serial port directly (no hamlib). Reference: the FT-950
CAT Operation Reference Book, ``docs/FT-950_CAT_Manual_ENG.pdf``.

The driver is synchronous and thread-safe (one lock serialises access — the
radio cannot interleave commands). FastAPI calls it via ``asyncio.to_thread``.
Auto-information (``AI1``) is switched off on open: unsolicited frames would
land in the middle of our own replies.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, asdict
from typing import Optional

import serial


MODEL_ID = "0310"          # ``ID;`` answer of the FT-950

# --- Lookup tables -----------------------------------------------------------

# MD / IF / OI / MR mode codes.
MODES = {
    "1": "LSB", "2": "USB", "3": "CW", "4": "FM", "5": "AM",
    "6": "RTTY-L", "7": "CW-R", "8": "DATA-L", "9": "RTTY-U",
    "A": "PKT-FM", "B": "FM-N", "C": "DATA-U", "D": "AM-N",
}
MODE_CODES = {v: k for k, v in MODES.items()}
MW_MODES = [m for m in MODE_CODES if m != "AM-N"]
# CTCSS tone table (CN 00-49), CAT manual p. 5
CTCSS_TONES = [67.0, 69.3, 71.9, 74.4, 77.0, 79.7, 82.5, 85.4, 88.5, 91.5, 94.8, 97.4, 100.0,
               103.5, 107.2, 110.9, 114.8, 118.8, 123.0, 127.3, 131.8, 136.5, 141.3, 146.2,
               151.4, 156.7, 159.8, 162.2, 165.5, 167.9, 171.3, 173.8, 177.3, 179.9, 183.5,
               186.2, 189.9, 192.8, 196.6, 199.5, 203.5, 206.5, 210.7, 218.1, 225.7, 229.1,
               233.6, 241.8, 250.3, 254.1]
CTCSS_MODES = {0: "OFF", 1: "ENC/DEC", 2: "ENC"}     # MW's mode list has no D
FM_MODES = {"FM", "FM-N", "PKT-FM"}                 # CTCSS only applies here

# IF/OI field P7.
MEM_MODES = {"0": "VFO", "1": "MEM", "2": "MT", "3": "QMB", "4": "QMB-MT"}

# AGC: the set range (0-4) differs from what the radio reports (0-6).
AGC_SET = {"OFF": 0, "FAST": 1, "MID": 2, "SLOW": 3, "AUTO": 4}
AGC_READ = {0: "OFF", 1: "FAST", 2: "MID", 3: "SLOW",
            4: "AUTO-FAST", 5: "AUTO-MID", 6: "AUTO-SLOW"}

# Roofing filter: same asymmetry.
ROOF_SET = {"AUTO": 0, "15k": 1, "6k": 2, "3k": 3}
ROOF_READ = {1: "15k", 2: "6k", 3: "3k",
             4: "AUTO-15k", 5: "AUTO-6k", 6: "AUTO-3k"}

PREAMP = {0: "IPO", 1: "AMP1", 2: "AMP2"}
ATT = {0: "OFF", 1: "6dB", 2: "12dB", 3: "18dB"}
METERS = {1: "S", 3: "COMP", 4: "ALC", 5: "PO", 6: "SWR", 7: "ID", 8: "VDD"}

# BS band codes. 02 is missing from the printed table but menu 075 defines a
# 5 MHz band, so it is offered (a set that the radio drops is harmless).
BANDS = {"1.8": 0, "3.5": 1, "5": 2, "7": 3, "10": 4, "14": 5, "18": 6,
         "21": 7, "24.5": 8, "28": 9, "50": 10, "GEN": 11}

# SH width in Hz per (column, index); index 00 is the radio's default width.
# Which column applies depends on the mode and on NA (narrow). Measured on the
# radio: DATA-U uses the PKT column, so 2400 Hz (13) is its maximum.
_W = {
    "SSB-N": [1800, 200, 400, 600, 850, 1100, 1350, 1500, 1650, 1800],
    "SSB-W": {0: 2400, 9: 1800, 10: 1950, 11: 2100, 12: 2250, 13: 2400,
              14: 2450, 15: 2500, 16: 2600, 17: 2700, 18: 2800, 19: 2900,
              20: 3000},
    "CW-N": {0: 500, 3: 100, 4: 200, 5: 300, 6: 400, 7: 500},
    "CW-W": {0: 2400, 7: 500, 8: 800, 9: 1200, 10: 1400, 11: 1700,
             12: 2000, 13: 2400},
    "PKT-N": {0: 300, 3: 100, 4: 200, 5: 300, 6: 400, 7: 500},
    "PKT-W": {0: 500, 7: 500, 8: 800, 9: 1200, 10: 1400, 11: 1700,
              12: 2000, 13: 2400},
}
WIDTHS: dict[str, dict[int, int]] = {
    k: (dict(enumerate(v)) if isinstance(v, list) else v) for k, v in _W.items()
}
_MODE_FAMILY = {"LSB": "SSB", "USB": "SSB", "CW": "CW", "CW-R": "CW",
                "RTTY-L": "PKT", "RTTY-U": "PKT", "DATA-L": "PKT",
                "DATA-U": "PKT"}


def width_column(mode: str, narrow: bool) -> Optional[str]:
    """SH table column for a mode, or None for modes without a width control."""
    fam = _MODE_FAMILY.get(mode)
    return f"{fam}-{'N' if narrow else 'W'}" if fam else None


def width_hz(mode: str, narrow: bool, index: int) -> Optional[int]:
    col = width_column(mode, narrow)
    return WIDTHS[col].get(index) if col else None


def width_index_for(mode: str, narrow: bool, hz: int) -> Optional[int]:
    """Smallest SH index (excluding the 00 default) with the closest width."""
    col = width_column(mode, narrow)
    if not col:
        return None
    opts = [(abs(w - hz), i) for i, w in WIDTHS[col].items() if i != 0]
    return min(opts)[1] if opts else None


class FT950Error(Exception):
    """Raised on timeout, malformed replies, or a set the radio ignored."""


class FT950Rejected(FT950Error):
    """The radio answered a *read* with ``?;`` (e.g. an empty memory channel).

    Set commands are never answered — this only happens to reads."""


@dataclass
class ChannelInfo:
    """Decoded 24-character payload shared by IF, OI, MR (and MW).

    P1 channel(3) P2 freq(8) P3 clar(±4) P4 rx_clar P5 tx_clar P6 mode
    P7 mem_mode P8 ctcss P9 tone(2) P10 shift
    """
    channel: int
    freq_hz: int
    clar_hz: int          # signed offset
    rx_clar: bool
    tx_clar: bool
    mode: str             # human name, e.g. "DATA-U" (raw code if unknown)
    mem_mode: str         # VFO / MEM / MT / QMB / QMB-MT
    ctcss: int            # 0 off, 1 ENC/DEC, 2 ENC
    tone: int             # CTCSS table index 00-49
    shift: int            # 0 simplex, 1 plus, 2 minus

    def to_dict(self) -> dict:
        return asdict(self)


def parse_channel(payload: str) -> ChannelInfo:
    """Parse the payload after the two-letter prefix (without ``;``)."""
    if len(payload) != 24:
        raise FT950Error(f"channel object must be 24 chars, got {payload!r}")
    p = payload
    if p[16] not in "01" or p[17] not in "01" or p[11] not in "+-":
        raise FT950Error(f"malformed channel object {payload!r}")
    try:
        return ChannelInfo(
            channel=int(p[0:3]),
            freq_hz=int(p[3:11]),
            clar_hz=int(p[11:16]),
            rx_clar=p[16] == "1",
            tx_clar=p[17] == "1",
            # Unknown codes are kept, not fatal: the IF mode list in the
            # manual already disagrees with the MD list (AM-N).
            mode=MODES.get(p[18], p[18]),
            mem_mode=MEM_MODES.get(p[19], p[19]),
            ctcss=int(p[20]),
            tone=int(p[21:23]),
            shift=int(p[23]),
        )
    except ValueError as e:
        raise FT950Error(f"malformed channel object {payload!r}: {e}") from e


def format_channel(c: ChannelInfo) -> str:
    """Inverse of :func:`parse_channel` (P7 is fixed 0 for MW)."""
    code = MODE_CODES.get(c.mode, c.mode)
    sign = "-" if c.clar_hz < 0 else "+"
    return (f"{c.channel:03d}{c.freq_hz:08d}{sign}{abs(c.clar_hz):04d}"
            f"{int(c.rx_clar)}{int(c.tx_clar)}{code}0{c.ctcss}{c.tone:02d}"
            f"{c.shift}")


class FT950:
    """Thread-safe synchronous driver for the Yaesu FT-950."""

    def __init__(self, port: str = "/dev/ttyUSB0", baudrate: int = 38400,
                 timeout: float = 0.5):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self._serial: Optional[serial.Serial] = None
        self._lock = threading.RLock()

    # -- connection --------------------------------------------------------
    def open(self) -> None:
        with self._lock:
            if self._serial and self._serial.is_open:
                return
            # Configure the control lines *before* opening: pyserial asserts
            # RTS/DTR on open by default, and on the SCU-17 those lines key
            # PTT/CW (menu 028 decides whether RTS also gates CAT).
            s = serial.Serial()
            s.port, s.baudrate = self.port, self.baudrate
            s.timeout = s.write_timeout = self.timeout
            s.rtscts = s.dsrdtr = False
            s.rts = s.dtr = False
            s.open()
            self._serial = s
            s.reset_input_buffer()
            self.send("AI0")      # no unsolicited frames between our replies
            time.sleep(0.05)
            s.reset_input_buffer()

    def close(self) -> None:
        with self._lock:
            if self._serial and self._serial.is_open:
                self._serial.close()
            self._serial = None

    @property
    def is_open(self) -> bool:
        return bool(self._serial and self._serial.is_open)

    # -- raw transactions --------------------------------------------------
    def send(self, command: str) -> None:
        """Send a set command. The radio never acknowledges it."""
        with self._lock:
            if not self.is_open:
                self.open()
            assert self._serial is not None
            self._serial.write(command.encode("ascii") + b";")
            self._serial.flush()

    def query(self, command: str) -> str:
        """Send a read command and return the answer without the ``;``.

        The answer must start with the command's two letters; anything else
        (a stray frame, line noise) is skipped until the deadline.
        """
        prefix = command[:2].upper()
        with self._lock:
            if not self.is_open:
                self.open()
            ser = self._serial
            assert ser is not None
            ser.reset_input_buffer()
            ser.write(command.encode("ascii") + b";")
            ser.flush()
            deadline = time.monotonic() + self.timeout * 2
            buf = bytearray()
            while time.monotonic() < deadline:
                b = ser.read(1)
                if not b:
                    continue
                if b != b";":
                    buf += b
                    continue
                reply = buf.decode("ascii", errors="replace")
                if reply.upper().startswith(prefix):
                    return reply
                if reply == "?":            # measured: empty memory read → "?;"
                    raise FT950Rejected(f"radio rejected {command!r}")
                buf.clear()       # not ours — keep reading
            raise FT950Error(f"no reply to {command!r} (radio off or link down?)")

    def set_verified(self, set_cmd: str, read_cmd: str,
                     expect: Optional[str] = None, settle: float = 0.08) -> str:
        """Fire & verify: send ``set_cmd``, read back, require a match.

        The read-back of most FT-950 commands is the set command itself
        (``SH013`` → ``SH013``), so that is the default ``expect``. Raises
        :class:`FT950Error` when the radio dropped the value.
        """
        expect = set_cmd if expect is None else expect
        with self._lock:
            self.send(set_cmd)
            time.sleep(settle)
            got = self.query(read_cmd)
            if got.upper() != expect.upper():
                raise FT950Error(
                    f"radio ignored {set_cmd!r} (reads back {got!r})")
            return got

    # -- identity / status -------------------------------------------------
    def identify(self) -> str:
        return self.query("ID")[2:]

    def info(self) -> ChannelInfo:
        """Active VFO / memory (``IF``)."""
        return parse_channel(self.query("IF")[2:])

    def opposite(self) -> ChannelInfo:
        """The other VFO (``OI``) — the only way to read VFO-B's mode."""
        return parse_channel(self.query("OI")[2:])

    def _num(self, cmd: str, skip: int) -> int:
        return int(self.query(cmd)[skip:])

    # -- frequency / mode --------------------------------------------------
    def get_freq(self, vfo: str = "A") -> int:
        return self._num("F" + vfo.upper(), 2)

    def set_freq(self, hz: int, vfo: str = "A") -> None:
        if not 30_000 <= hz <= 56_000_000:
            raise FT950Error(f"frequency {hz} Hz outside 30 kHz – 56 MHz")
        cmd = f"F{vfo.upper()}{hz:08d}"
        self.set_verified(cmd, cmd[:2])

    def tune_steps(self, steps: int, knob: int = 0) -> None:
        """Turn a knob by ±steps detents (EU/ED); the radio applies its step."""
        while steps:
            n = max(-99, min(99, steps))
            self.send(f"{'EU' if n > 0 else 'ED'}{knob}{abs(n):02d}")
            steps -= n

    def get_mode(self) -> str:
        code = self.query("MD0")[3:]
        return MODES.get(code, code)

    def set_mode(self, mode: str) -> None:
        code = MODE_CODES.get(mode)
        if code is None:
            raise FT950Error(f"unknown mode {mode!r}")
        self.set_verified(f"MD0{code}", "MD0")

    def set_split(self, on: bool) -> None:
        """Split = receive on A, transmit on B (FT3); off = transmit on A (FT2).
        The read-back reports the TX VFO as FT0 (A) / FT1 (B)."""
        self.set_verified("FT3" if on else "FT2", "FT", expect="FT1" if on else "FT0")

    # -- clarifier ---------------------------------------------------------------
    def set_rx_clar(self, on: bool) -> None:
        self.set_verified(f"RT{int(on)}", "RT")

    def clear_clar(self) -> None:
        self.send("RC")

    def set_clar(self, offset_hz: int) -> int:
        """Clarifier offset in Hz, -9999 ... +9999, and the value the radio holds.

        There is no absolute set: RU/RD move the offset up or down and carry
        no sign (the direction is the command), RC clears it. So clear, move
        once, and read the result back from IF - the same order hamlib's
        newcat uses, and it is right whether RU/RD add to the offset or replace
        it. The read-back is the proof; a radio that dropped the step raises.
        """
        hz = max(-9999, min(9999, int(offset_hz)))
        with self._lock:
            self.send("RC")
            if hz:
                self.send(f"{'RU' if hz > 0 else 'RD'}{abs(hz):04d}")
            time.sleep(0.08)
            got = self.info().clar_hz
        if got != hz:
            raise FT950Error(f"clarifier reads back {got:+d} Hz instead of {hz:+d} Hz")
        return got

    # -- CW keyer (the radio keys: exact timing, no audio path involved) ----
    def get_cw(self) -> dict:
        return {"wpm": self._num("KS", 2), "pitch": 300 + 50 * self._num("KP", 2),
                "breakin": self._flag("BI")}

    def set_cw_speed(self, wpm: int) -> None:
        self.set_verified(f"KS{max(4, min(60, int(wpm))):03d}", "KS")

    def set_breakin(self, on: bool) -> None:
        self.set_verified(f"BI{int(on)}", "BI")

    def cw_play(self, text: str, slot: int = 5) -> None:
        """Write ``text`` (≤ 50 chars) into keyer memory ``slot`` and play it.
        Memory 5 is used as the scratch slot for remote CW."""
        with self._lock:
            self.send(f"KM{slot}{text[:50]}")
            time.sleep(0.05)
            self.send(f"KY{slot}")

    # -- VRF (the LCD's FLT: VRF / THRU) -------------------------------------
    def set_vrf(self, on: bool) -> None:
        """VF0 + 1/0 + "+0": switch VRF without moving its tuning; the answer
        (VF0 + P2 + P5 P5 P5 + P6) is checked on the on/off digit only."""
        with self._lock:
            self.send(f"VF0{int(on)}+0")
            time.sleep(0.08)
            got = self.query("VF0")
            if got[3:4] != str(int(on)):
                raise FT950Error(f"radio ignored VRF {'on' if on else 'off'} (reads back {got!r})")

    # -- CTCSS ---------------------------------------------------------------
    def set_ctcss(self, mode: Optional[int] = None, tone: Optional[int] = None) -> None:
        """CT0 + 0 off / 1 ENC/DEC / 2 ENC, CN0 + tone number 00-49 (verified)."""
        if tone is not None:
            if not 0 <= tone < len(CTCSS_TONES):
                raise ValueError("tone number must be 0-49")
            self.set_verified(f"CN0{tone:02d}", "CN0")
        if mode is not None:
            if mode not in CTCSS_MODES:
                raise ValueError("CTCSS mode must be 0, 1 or 2")
            self.set_verified(f"CT0{mode}", "CT0")

    # -- antenna and antenna tuner -----------------------------------------
    def set_antenna(self, ant: int) -> None:
        """ANT 1 / ANT 2 (AN0 + n); the read-back is AN0 + n + 0."""
        if ant not in (1, 2):
            raise ValueError("antenna must be 1 or 2")
        self.set_verified(f"AN0{ant}", "AN0", expect=f"AN0{ant}0")

    def set_tuner(self, on: bool) -> None:
        """Internal ATU in line (AC001) or bypassed (AC000)."""
        self.set_verified(f"AC00{int(on)}", "AC")

    def tuner_start(self) -> None:
        """AC002: start an ATU tuning cycle. The radio keys up and sends a
        carrier on its own until the tuner has matched."""
        with self._lock:
            self.send("AC002")

    # -- receive chain -----------------------------------------------------
    def get_narrow(self) -> bool:
        return self.query("NA0")[3:] == "1"

    def set_narrow(self, on: bool) -> None:
        self.set_verified(f"NA0{int(on)}", "NA0")

    def get_width(self) -> dict:
        idx = self._num("SH0", 3)
        mode, narrow = self.get_mode(), self.get_narrow()
        return {"index": idx, "hz": width_hz(mode, narrow, idx),
                "column": width_column(mode, narrow)}

    def set_width_hz(self, hz: int) -> dict:
        mode, narrow = self.get_mode(), self.get_narrow()
        idx = width_index_for(mode, narrow, hz)
        if idx is None:
            raise FT950Error(f"mode {mode} has no width control")
        self.set_verified(f"SH0{idx:02d}", "SH0")
        return {"index": idx, "hz": width_hz(mode, narrow, idx)}

    def get_ifshift(self) -> int:
        return int(self.query("IS0")[3:])

    def set_ifshift(self, hz: int) -> None:
        hz = max(-1000, min(1000, hz))
        self.set_verified(f"IS0{'-' if hz < 0 else '+'}{abs(hz):04d}", "IS0")

    # -- manual notch / contour (BP / CO): on/off and frequency -----------------
    def set_notch(self, on: Optional[bool] = None, hz: Optional[int] = None) -> None:
        """BP00 on/off (000/001), BP01 notch frequency 001-300 × 10 Hz."""
        if hz is not None:
            v = max(1, min(300, round(hz / 10)))
            self.set_verified(f"BP01{v:03d}", "BP01")
        if on is not None:
            self.set_verified(f"BP00{int(on):03d}", "BP00")

    def _p3(self, read_cmd: str, value: int) -> str:
        """The set command with P3 as wide as the radio's own answer: the CAT book
        shows CO's P3 as two digits but lists 000/001, so ask the radio."""
        width = max(2, len(self.query(read_cmd)) - len(read_cmd))
        return f"{read_cmd}{value:0{width}d}"

    def set_contour(self, on: Optional[bool] = None, pos: Optional[int] = None) -> None:
        """CO00 on/off, CO01 contour frequency 01-30."""
        with self._lock:
            if pos is not None:
                self.set_verified(self._p3("CO01", max(1, min(30, pos))), "CO01")
            if on is not None:
                self.set_verified(self._p3("CO00", int(on)), "CO00")

    def get_agc(self) -> str:
        return AGC_READ.get(self._num("GT0", 3), "?")

    def set_agc(self, name: str) -> None:
        # No exact read-back: AUTO reads as AUTO-FAST/MID/SLOW.
        self.send(f"GT0{AGC_SET[name]}")

    def get_roofing(self) -> str:
        return ROOF_READ.get(self._num("RF0", 3), "?")

    def set_roofing(self, name: str) -> None:
        self.send(f"RF0{ROOF_SET[name]}")   # asymmetric read-back as for GT

    def get_preamp(self) -> str:
        return PREAMP.get(self._num("PA0", 3), "?")

    def set_preamp(self, level: int) -> None:
        self.set_verified(f"PA0{level}", "PA0")

    def get_att(self) -> str:
        return ATT.get(self._num("RA0", 3), "?")

    def set_att(self, level: int) -> None:
        self.set_verified(f"RA0{level}", "RA0")

    def _flag(self, cmd: str) -> bool:
        return self.query(cmd)[len(cmd):][:1] == "1"

    def set_flag(self, cmd: str, on: bool) -> None:
        """On/off commands with a fixed P1 0: NR0, NB0, BC0, NA0 …"""
        self.set_verified(f"{cmd}{int(on)}", cmd)

    def get_level(self, cmd: str) -> int:
        """3-digit levels after a fixed P1: AG0, RG0, SQ0, NL0 (000-255)."""
        return self._num(cmd, len(cmd))

    def set_level(self, cmd: str, value: int, hi: int = 255) -> None:
        self.set_verified(f"{cmd}{max(0, min(hi, value)):03d}", cmd)

    # -- meters ------------------------------------------------------------
    def smeter(self) -> int:
        return self._num("SM0", 3)

    def meter(self, which: int) -> int:
        """RM1 S · RM3 COMP · RM4 ALC · RM5 PO · RM6 SWR · RM7 ID · RM8 VDD."""
        return self._num(f"RM{which}", 3)

    def busy(self) -> bool:
        return self.query("BY")[2:3] == "1"

    def pll_unlocked(self) -> bool:
        return self.query("UL")[2:] == "1"

    # -- transmit ----------------------------------------------------------
    def get_power(self) -> int:
        return self._num("PC", 2)

    def set_power(self, watts: int) -> None:
        self.set_verified(f"PC{max(5, min(100, watts)):03d}", "PC")

    def transmitting(self) -> bool:
        return self.query("TX")[2:] != "0"

    def ptt(self, on: bool) -> None:
        """CAT PTT. Callers own the watchdog — this only keys/unkeys."""
        self.send(f"TX{1 if on else 0}")

    # -- memories ----------------------------------------------------------
    def read_memory(self, channel: int) -> Optional[ChannelInfo]:
        """Memory ``channel`` (1-99), or None when empty.

        Measured: MR's own P1 field reports the *current* memory channel, not
        the one asked for, so the requested number is put back into the result.
        """
        try:
            c = parse_channel(self.query(f"MR{channel:03d}")[2:])
        except FT950Rejected:
            return None
        c.channel = channel
        return c

    def write_memory(self, channel: int, freq_hz: int, mode: str,
                     ctcss: Optional[int] = None, tone: Optional[int] = None) -> ChannelInfo:
        """MW: store frequency + mode (and, if given, CTCSS mode + tone) in
        ``channel`` and verify with MR.

        Clarifier, repeater shift and — unless given — CTCSS of an existing
        channel are kept; a new channel gets them off. The radio cannot name
        or clear a memory over CAT, and has no split / VFO-B in a memory."""
        if not 1 <= channel <= 99:
            raise ValueError("channel must be 1-99")
        if mode not in MW_MODES:
            raise ValueError(f"mode {mode!r} cannot be stored in a memory")
        if not 30_000 <= freq_hz <= 56_000_000:
            raise ValueError("frequency out of range")
        if ctcss is not None and ctcss not in CTCSS_MODES:
            raise ValueError("CTCSS mode must be 0, 1 or 2")
        if tone is not None and not 0 <= tone < len(CTCSS_TONES):
            raise ValueError("tone number must be 0-49")
        with self._lock:
            old = self.read_memory(channel)
            c = old or ChannelInfo(channel, 0, 0, False, False, mode, "VFO", 0, 0, 0)
            c.channel, c.freq_hz, c.mode = channel, freq_hz, mode
            if ctcss is not None:
                c.ctcss = ctcss
            if tone is not None:
                c.tone = tone
            self.send("MW" + format_channel(c))
            time.sleep(0.1)
            got = self.read_memory(channel)
            if (got is None or got.freq_hz != freq_hz or got.mode != mode
                    or got.ctcss != c.ctcss or got.tone != c.tone):
                raise FT950Error(f"radio did not store memory {channel} (reads back {got})")
            return got

    def recall_to_vfo(self, channel: int, vfo_b_hz: Optional[int] = None,
                      split: bool = False) -> ChannelInfo:
        """Memory → VFO-A as frequency + mode (FA, MD), in FM also its CTCSS
        (CT, CN). ``vfo_b_hz``: VFO-B gets A's mode (AB) and this frequency;
        ``split`` sets TX on B or A. The radio stays in VFO mode and no memory
        is ever written."""
        with self._lock:
            c = self.read_memory(channel)
            if c is None:
                raise FT950Error(f"memory {channel} is empty")
            self.set_freq(c.freq_hz, "A")
            if c.mode in MODE_CODES:
                self.set_mode(c.mode)
            if c.mode in FM_MODES:
                self.set_ctcss(mode=c.ctcss, tone=c.tone)
            if vfo_b_hz:
                self.send("AB")                       # B = A (frequency + mode) …
                time.sleep(0.05)
                self.set_freq(vfo_b_hz, "B")          # … then B's own frequency
            self.set_split(split)
            return c

    # -- menu --------------------------------------------------------------
    def get_menu(self, number: int) -> str:
        return self.query(f"EX{number:03d}")[5:]

    def set_menu(self, number: int, value: str) -> None:
        self.set_verified(f"EX{number:03d}{value}", f"EX{number:03d}")

    # -- snapshots for the poll loop --------------------------------------
    # Measured: ~7 ms per query at 38400 Bd, so the full panel (~22 queries)
    # costs ~165 ms. Split into what must follow the dial (fast) and what
    # only changes when someone touches a control (slow).
    def read_fast(self) -> dict:
        """~4-7 queries: frequency/mode, S-meter, busy, TX (+ TX meters)."""
        with self._lock:
            tx = self.transmitting()
            d = {"vfo": self.info().to_dict(), "smeter": self.smeter(),
                 "busy": self.busy(), "tx": tx}
            if tx:
                d["meters"] = {"po": self.meter(5), "alc": self.meter(4),
                               "swr": self.meter(6), "id": self.meter(7),
                               "vdd": self.meter(8)}             # supply voltage: the sag under TX
            return d

    def read_slow(self, mode: str) -> dict:
        """The receive/transmit chain; ``mode`` picks the SH width column.

        A value the radio does not have in the current mode answers "?;" —
        measured: SH0 (no width control) in AM/FM. That value is reported as
        None instead of failing the whole read, which used to mark the radio
        as disconnected."""
        def opt(fn, *args):
            try:
                return fn(*args)
            except FT950Rejected:
                return None

        def q(cmd, sl):
            return opt(lambda: self.query(cmd)[sl])

        with self._lock:
            narrow = self.get_narrow()
            idx = opt(self._num, "SH0", 3)
            ml, vf, bp, co = q("ML0", slice(3, None)), q("VF0", slice(3, 4)), \
                q("BP00", slice(4, None)), q("CO00", slice(4, None))
            return {
                "other": self.opposite().to_dict(),
                "rx_vfo": "B" if self._num("FR", 2) in (4, 5) else "A",
                "tx_vfo": "B" if self._num("FT", 2) == 1 else "A",
                "narrow": narrow,
                "width": {"index": idx,
                          "hz": width_hz(mode, narrow, idx) if idx is not None else None,
                          "column": width_column(mode, narrow)},
                "ifshift": opt(self.get_ifshift),
                "agc": opt(self.get_agc),
                "roofing": opt(self.get_roofing),
                "preamp": opt(self.get_preamp),
                "att": opt(self.get_att),
                "nb": q("NB0", slice(3, None)),
                "nr": opt(self._flag, "NR0"),
                "dnf": opt(self._flag, "BC0"),
                "rf_gain": opt(self.get_level, "RG0"),
                "squelch": opt(self.get_level, "SQ0"),
                "power": opt(self.get_power),
                "proc": q("PR", slice(2, 3)),              # 0 off, 1 on, 2 mic EQ
                "monitor": ml is not None and ml != "000",
                "vdd_raw": opt(self.meter, 8),             # VDD meter needle 0-255
                # for the LCD signal path and DSP bars (as the radio's own LCD)
                "ant": q("AN0", slice(3, 4)),              # AN0 → "AN0" + ant + "0"
                "vrf": vf == "1",                          # FLT: VRF on, else THRU
                "tuner": q("AC", slice(4, 5)),             # 0 off, 1 on, 2 tuning
                "ctcss": opt(self._num, "CT0", 3),         # 0 off, 1 ENC/DEC, 2 ENC
                "ctcss_tone": opt(self._num, "CN0", 3),    # index into CTCSS_TONES
                "notch": bp is not None and bp != "000",
                "notch_hz": (opt(self._num, "BP01", 4) or 0) * 10,
                "contour": co is not None and co not in ("00", "000"),
                "contour_pos": opt(self._num, "CO01", 4),  # 01-30
                "lock": self._flag("LK"),
                "pll_unlock": self.pll_unlocked(),
            }

    def read_status(self) -> dict:
        """Everything the VFO panel shows, in one locked pass."""
        with self._lock:
            fast = self.read_fast()
            return {**fast, **self.read_slow(fast["vfo"]["mode"])}
