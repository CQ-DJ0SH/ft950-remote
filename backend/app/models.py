"""Pydantic request bodies for the REST API."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, Field


def zero_call(v):
    """Callsigns are often written with a slashed zero (DJØSH); the radio
    protocols and logbooks need a plain digit 0."""
    return v.replace("Ø", "0").replace("ø", "0").strip() if isinstance(v, str) else v


Callsign = Annotated[str, BeforeValidator(zero_call)]


class FrequencyReq(BaseModel):
    hz: int = Field(ge=30_000, le=56_000_000)
    vfo: Literal["A", "B"] = "A"


class TuneReq(BaseModel):
    steps: int = Field(ge=-990, le=990, description="±detents, radio applies its step")
    knob: Literal[0, 1, 2] = 0       # 0 main dial, 1 CLAR/VFO-B, 2 SELECT


class ModeReq(BaseModel):
    mode: str


class WidthReq(BaseModel):
    hz: int = Field(ge=100, le=3000)


class OnOffReq(BaseModel):
    on: bool


class IfShiftReq(BaseModel):
    hz: int = Field(ge=-1000, le=1000)


class ClarReq(BaseModel):
    hz: int = Field(ge=-9999, le=9999)          # the radio's own range (RU/RD 0000-9999)
    rx_on: bool | None = None                   # also switch RX CLAR (RT); None = leave it


class NameReq(BaseModel):
    name: str


class LevelReq(BaseModel):
    level: int = Field(ge=0, le=3)


class PowerReq(BaseModel):
    watts: int = Field(ge=5, le=100)


class BandReq(BaseModel):
    band: str


class DigiColors(BaseModel):
    """Row colours of the DIGI lists, "#rrggbb"; "" = theme default."""
    cq: str | None = Field(default=None, pattern=r"^$|^#[0-9a-fA-F]{6}$")
    cq_worked: str | None = Field(default=None, pattern=r"^$|^#[0-9a-fA-F]{6}$")
    me: str | None = Field(default=None, pattern=r"^$|^#[0-9a-fA-F]{6}$")
    tx: str | None = Field(default=None, pattern=r"^$|^#[0-9a-fA-F]{6}$")
    newdx: str | None = Field(default=None, pattern=r"^$|^#[0-9a-fA-F]{6}$")


class SettingsReq(BaseModel):
    my_call: Callsign | None = Field(default=None, pattern=r"^$|^[A-Za-z0-9/]{3,12}$")
    my_grid: str | None = Field(default=None, pattern=r"^$|^[A-Ra-r]{2}\d{2}([A-Xa-x]{2})?$")
    serial_port: str | None = None
    serial_baud: Literal[4800, 9600, 19200, 38400] | None = None   # radio menu 026
    digi_depth: Literal["AUTO", 1, 2, 3] | None = None
    digi_decoder: Literal["jt9", "jt9-fast", "jt9-33mt"] | None = None     # an id of digi.DECODERS
    digi_low_hz: int | None = Field(default=None, ge=0, le=3000)
    digi_high_hz: int | None = Field(default=None, ge=500, le=5000)
    ft_closing: Literal["RR73", "RRR"] | None = None     # sequencer's 4th message (WSJT-X "RR73" box)
    hunt_target: Literal["band", "call", "dxcc", "dxcc_band"] | None = None   # whom Hunt calls
    hunt_min_snr: int | None = Field(default=None, ge=-30, le=20)
    hunt_minutes: int | None = Field(default=None, ge=0, le=1440)      # Hunt switches itself off; 0 = never
    digi_colors: DigiColors | None = None
    hackrf_url: str | None = Field(default=None, pattern=r"^$|^https?://[A-Za-z0-9.\-]+(:\d+)?/?$")
    # radio power via Shelly (title-bar switch); password "" = keep the stored one
    shelly_host: str | None = Field(default=None, pattern=r"^$|^(https?://)?[A-Za-z0-9.\-]+(:\d+)?/?$")
    shelly_channel: int | None = Field(default=None, ge=0, le=3)
    shelly_user: str | None = Field(default=None, max_length=64)
    shelly_password: str | None = Field(default=None, max_length=128)
    shelly_password_clear: bool | None = None
    auto_power_off: bool | None = None          # switch the radio off when no browser is connected …
    auto_power_off_s: int | None = Field(default=None, ge=60, le=7200)   # … for this long


class PowerSwitchReq(BaseModel):
    on: bool


class HackRFConfig(BaseModel):
    """HackRF waterfall settings (as TM-V71 Remote; only sent fields apply).
    ``follow`` = follow the FT-950's VFO A (done by this server)."""
    mode: Literal["pan", "sweep"] | None = None
    follow: bool | None = None
    center: int | None = Field(default=None, gt=0)
    sweep_start: int | None = Field(default=None, gt=0)
    sweep_stop: int | None = Field(default=None, gt=0)
    lna: int | None = Field(default=None, ge=0, le=40)
    vga: int | None = Field(default=None, ge=0, le=62)
    amp: bool | None = None


class DeviceReq(BaseModel):
    device: str


class PercentReq(BaseModel):
    percent: int = Field(ge=0, le=100)


class RxLevelReq(BaseModel):
    db: float = Field(ge=-20, le=20)


class RadioAudioReq(BaseModel):
    menu: Literal[50, 51]            # 050 DATA TX GAIN · 051 DATA OUTPUT LEVEL
    value: int = Field(ge=0, le=100)


class LogConfigReq(BaseModel):
    wavelog_url: str | None = None
    wavelog_key: str | None = None          # "" = keep the stored key
    wavelog_station_id: str | None = None
    qrz_username: str | None = None
    qrz_password: str | None = None         # "" = keep the stored password
    qrz_api_key: str | None = None          # QRZ.com logbook upload; "" = keep
    auto_log: bool | None = None
    qrz_before_log: bool | None = None


class QsoReq(BaseModel):
    call: Callsign = Field(min_length=3, max_length=16)
    gridsquare: str = ""
    rst_sent: str = ""
    rst_rcvd: str = ""
    name: str = ""
    qth: str = ""
    country: str = ""
    comment: str = ""
    mode: str | None = None                 # default: current digi mode
    freq_hz: int | None = None              # default: dial + TX offset
    tx_offset_hz: int | None = None
    to: list[Literal["wavelog", "qrz"]] = Field(default_factory=lambda: ["wavelog", "qrz"], min_length=1)


class TuneReq2(BaseModel):
    on: bool
    hz: float = Field(default=1500, ge=200, le=2900)


class PttReq(BaseModel):
    on: bool
    peer: str | None = None                 # from the WebRTC answer


class VoiceCfgReq(BaseModel):
    rx_gain: float | None = Field(default=None, ge=0, le=12)
    tx_gain: float | None = Field(default=None, ge=0, le=12)
    rx_buffer_ms: int | None = Field(default=None, ge=20, le=400)
    tx_buffer_ms: int | None = Field(default=None, ge=40, le=600)
    ptt_tail_ms: int | None = Field(default=None, ge=0, le=1000)
    roger: bool | None = None
    tx_auto_gain: bool | None = None
    tx_comp: bool | None = None            # voice TX compressor
    tx_preemph: bool | None = None         # voice TX pre-emphasis


class MemoryReq(BaseModel):
    channel: int = Field(ge=1, le=99)


class MemoryWriteReq(BaseModel):
    channel: int = Field(ge=1, le=99)
    freq_hz: int | None = Field(default=None, ge=30_000, le=56_000_000)
    mode: str | None = None
    label: str | None = Field(default=None, max_length=12)   # None = keep
    ctcss: int | None = Field(default=None, ge=0, le=2)       # 0 off, 1 TSQ (ENC/DEC), 2 ENC
    tone: int | None = Field(default=None, ge=0, le=49)       # CTCSS table index
    split: bool | None = None                                 # kept on the Pi (radio memory has none)
    vfo_b_hz: int | None = Field(default=None, ge=0, le=56_000_000)   # 0 = no VFO-B


class WebRTCOffer(BaseModel):
    sdp: str
    type: str


class MenuSetReq(BaseModel):
    no: int = Field(ge=1, le=118)
    value: int


class MenuResetReq(BaseModel):
    nos: list[int] | None = None            # None = every menu that differs


class SeqCqReq(BaseModel):
    tx_hz: float = Field(ge=200, le=2900)


class SeqAutoReq(BaseModel):
    on: bool
    tx_hz: float | None = Field(default=None, ge=200, le=2900)
    adopt: bool | None = None               # AF: answer on the partner's frequency


class HuntBlockReq(BaseModel):
    call: Callsign = Field(pattern=r"^[A-Za-z0-9/]{3,12}$")
    blocked: bool


class SeqAnswerReq(BaseModel):
    decode: dict                            # a decode as /ws/digi delivered it
    tx_hz: float = Field(ge=200, le=2900)


class DigiConfigReq(BaseModel):
    enabled: bool | None = None
    mode: str | None = None          # AUTO / FT8 / FT4
    depth: Literal["AUTO", 1, 2, 3] | None = None
    low_hz: int | None = Field(default=None, ge=0, le=3000)
    high_hz: int | None = Field(default=None, ge=500, le=5000)
    my_call: Callsign | None = None
    my_grid: str | None = None


class CwSpeedReq(BaseModel):
    wpm: int = Field(ge=4, le=60)


class CwSendReq(BaseModel):
    text: str = Field(min_length=1, max_length=500, pattern=r"^[A-Za-z0-9 .,?/=+\-@]+$")


class CtcssReq(BaseModel):
    mode: int | None = Field(default=None, ge=0, le=2)     # 0 off, 1 ENC/DEC, 2 ENC
    tone: int | None = Field(default=None, ge=0, le=49)    # CN tone number


class AntennaReq(BaseModel):
    ant: Literal[1, 2]


class TunerReq(BaseModel):
    on: bool | None = None
    start: bool = False                       # True: start a tuning cycle (transmits)


class ToggleReq(BaseModel):
    cmd: str
    on: bool


class ValueReq(BaseModel):
    cmd: str
    value: int = Field(ge=0, le=255)
