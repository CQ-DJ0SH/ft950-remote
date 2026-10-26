"""FastAPI app: REST control, WebSocket live status, static SPA."""
from __future__ import annotations

import asyncio
import time
import logging
import urllib.request
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import menus as exmenus
from . import mixer, runtime, sys_history, system_info
from .logbook import LogBook, LogError, band_for_hz
from .tx import Transmitter
from .sequencer import Sequencer
from .recorder import Recorder
from .cw import CWSkimmer, cw_duration_s
from .shelly import ShellyError, ShellySwitch
from .propagation import Propagation
from .hackrf_proxy import DEFAULT_URL as HACKRF_DEFAULT, HackRFError, HackRFProxy
from .digi import analyse
from . import opus_mono
from .voice import RadioRxTrack, VoiceHub, consume_mic, force_opus_mono
from aiortc import RTCPeerConnection, RTCSessionDescription
from .audio import AudioCapture
from .config import APP_VERSION, settings
from .digi import DECODERS as DIGI_DECODERS, MODES as DIGI_MODES, DigiService, decoder_available, decoder_config, decoder_tag
from .ft950 import (AGC_SET, ATT, BANDS, MODES, PREAMP, ROOF_SET, WIDTHS,
                    FT950Error, width_column)
from .models import (zero_call, CwSendReq, CwSpeedReq, CtcssReq, SeqAutoReq, PowerSwitchReq, HackRFConfig, RxLevelReq, MemoryWriteReq, AntennaReq, TunerReq, BandReq, DeviceReq, DigiConfigReq, FrequencyReq,
                     ClarReq, IfShiftReq, LevelReq, LogConfigReq, MemoryReq, MenuResetReq,
                     MenuSetReq, ModeReq,
                     NameReq, OnOffReq, PercentReq, PowerReq, PttReq, QsoReq,
                     RadioAudioReq, HuntBlockReq, SeqAnswerReq, SeqCqReq, SettingsReq,
                     ToggleReq, TuneReq, TuneReq2,
                     ValueReq, VoiceCfgReq, WebRTCOffer, WidthReq)
from .radio_service import RadioService
from .state import ConnectionManager
from .waterfall import Waterfall

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
CERTS = Path(__file__).resolve().parents[1] / "certs"
CA_CERT = CERTS / "ca.crt"
log = logging.getLogger("ft950")

# Saved in Settings (runtime.json) wins over code/.env defaults.
_rt = runtime.load()
for _k in ("my_call", "my_grid", "serial_port", "serial_baud", "audio_device"):
    if _rt.get(_k) is not None:
        setattr(settings, _k, _rt[_k])

manager = ConnectionManager()
digi_manager = ConnectionManager()
wf_manager = ConnectionManager()
service = RadioService(manager)
capture = AudioCapture(settings.audio_device)
capture.gain_db = float(_rt.get("rx_level_db") or 0)
recorder = Recorder()
capture.subscribe(recorder.feed)
skimmer = CWSkimmer()
capture.subscribe(skimmer.feed)
cw_manager = ConnectionManager()
digi = DigiService(capture, digi_manager, lambda: service.status,
                   settings.digi_work_dir, settings.digi_keep_slices,
                   settings.digi_data_dir)
digi.config.update(my_call=settings.my_call, my_grid=settings.my_grid,
                   **decoder_config(_rt.get("digi_decoder") or "jt9"))
for _k in ("depth", "low_hz", "high_hz"):
    if _rt.get("digi_" + _k) is not None:
        digi.config[_k] = _rt["digi_" + _k]

logbook = LogBook(digi.dxcc)
# Worked-before flags only once a log was downloaded: with an empty index every
# station would read "new" and every DXCC "never worked" — noise, not news.
digi.worked_lookup = lambda call, dial, ent: (
    logbook.worked.status(call, band_for_hz(dial), ent)
    if logbook.worked.synced_at else None)
hub = VoiceHub(capture, lambda: settings.audio_device,
               **{k: _rt[k] for k in ("rx_gain", "tx_gain", "rx_buffer_ms",
                                      "tx_buffer_ms", "ptt_tail_ms", "roger", "tx_auto_gain",
                                      "tx_comp", "tx_preemph") if k in _rt})
tx = Transmitter(service, lambda: settings.audio_device,
                 level=(_rt.get("tx_level", 50) or 0) / 100, hub=hub)
sequencer = Sequencer(digi, tx, logbook, digi_manager, settings)
sequencer.closing = _rt.get("ft_closing") or "RR73"
# older lists kept only the time: {"K1ABC": 1761480000.0}
sequencer.hunt_blocked = {c: (v if isinstance(v, dict) else {"t": float(v)})
                          for c, v in (_rt.get("hunt_blocked") or {}).items()}
for _k in ("hunt_target", "hunt_min_snr", "hunt_minutes"):
    if _rt.get(_k) is not None:
        setattr(sequencer, _k, _rt[_k])
def _save_hunt_blocked(blocked: dict) -> None:
    _rt["hunt_blocked"] = dict(blocked)
    runtime.save({"hunt_blocked": _rt["hunt_blocked"]})
sequencer.on_hunt_block = _save_hunt_blocked
power = ShellySwitch()
power.configure(_rt.get("shelly_host", ""), _rt.get("shelly_channel", 0),
                _rt.get("shelly_user", ""), _rt.get("shelly_password", ""))
hackrf = HackRFProxy(lambda: _rt.get("hackrf_url") or HACKRF_DEFAULT, CA_CERT,
                     lambda: ((service.status.get("vfo") or {}).get("freq_hz")))
digi.on_decodes = sequencer.on_decodes
digi.early_range = sequencer.early_range
pcs: set = set()                            # WebRTC peers
audio_manager = ConnectionManager()         # /ws/audio level stream


async def _audio_level_loop() -> None:
    """RX/MIC levels at 10 Hz for the VU bars and the level graph."""
    while True:
        await asyncio.sleep(0.1)
        if audio_manager.count:
            await audio_manager.broadcast({
                "rx": hub.levels["rx"], "tx": hub.levels["tx"], "peers": hub.peers,
                "mics": hub.mic_levels, "agc_gain": round(hub.agc_gain, 2) if hub.tx_auto_gain else None,
                "codec": capture.digi_level()["rms_dbfs"], "codec_peak": capture.digi_level()["peak_dbfs"],
                "mic": hub.mic_tracks > 0, "ptt": tx.state["active"],
                "mic_test": hub.mic_test, "echo": hub.echo_busy, "tone_1750": hub.tone_1750,
                "backlog_ms": hub.tx_backlog_ms()})


async def _worked_sync_loop() -> None:
    """Keep the worked-before index current: at start, then every 10 minutes."""
    while True:
        if logbook.config()["wavelog_ready"]:
            try:
                await asyncio.to_thread(logbook.sync)
            except LogError as exc:
                log.warning("worked index sync failed: %s", exc)
        await asyncio.sleep(600)


async def _sys_history_loop() -> None:
    """Sample the host metrics once a minute for the 24 h graph."""
    sys_history.load()
    while True:
        try:
            sys_history.add(await asyncio.to_thread(system_info.collect))
        except Exception as exc:  # noqa: BLE001
            log.debug("system history sample failed: %s", exc)
        await asyncio.sleep(sys_history.STEP_S)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await service.start()
    Waterfall(capture, wf_manager, asyncio.get_running_loop())
    capture.start()
    await digi.start()
    hist = asyncio.create_task(_sys_history_loop())
    wsync = asyncio.create_task(_worked_sync_loop())
    levels = asyncio.create_task(_audio_level_loop())
    hfollow = asyncio.create_task(hackrf.follow_loop())
    autooff = asyncio.create_task(_auto_power_off_loop())
    cwloop = asyncio.create_task(_cw_broadcast_loop())
    opus_mono.install()                     # before the first peer builds an encoder
    if _rt.get("codec_playback") is not None:
        # ALSA restores its own (old) mixer state at boot — measured: the codec
        # came up at -20 dB and TUNE was far too weak. The saved level wins.
        try:
            await asyncio.to_thread(mixer.set_playback, settings.audio_device, _rt["codec_playback"])
        except Exception as exc:  # noqa: BLE001
            log.warning("codec playback level not applied: %s", exc)
    yield
    await sequencer.halt()                  # no new transmission …
    await tx.stop()                         # … and never leave the radio keyed
    levels.cancel()
    hfollow.cancel()
    autooff.cancel()
    cwloop.cancel()
    for pc in list(pcs):
        await pc.close()
    wsync.cancel()
    hist.cancel()
    await digi.stop()
    capture.stop()
    await service.stop()


app = FastAPI(title="FT-950 Remote", version=APP_VERSION, lifespan=lifespan)


@app.exception_handler(FT950Error)
async def ft950_error(_: Request, exc: FT950Error) -> JSONResponse:
    # A dropped set or a dead link is a plain-text 400, never a 500.
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.middleware("http")
async def no_cache_statics(request: Request, call_next):
    # Statics must never go stale after a deploy (TM-V71 Remote lesson);
    # /api answers are live anyway.
    response = await call_next(request)
    if not request.url.path.startswith(("/api", "/ws")):
        response.headers["Cache-Control"] = "no-cache"
    return response


def _bad(msg: str) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": msg})


# -- status ------------------------------------------------------------------
@app.get("/api/status")
async def get_status() -> dict:
    return service.status


@app.websocket("/ws")
async def ws_status(ws: WebSocket) -> None:
    await manager.connect(ws)
    try:
        await ws.send_json(service.status)
        while True:
            await ws.receive_text()          # keep-alive; clients send nothing
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(ws)


@app.get("/api/meta")
async def meta() -> dict:
    """Tables the UI needs, from the driver — one source of truth."""
    modes = [m for m in MODES.values() if m != "AM-N"]
    return {"modes": modes, "bands": list(BANDS),
            "agc": list(AGC_SET), "roofing": list(ROOF_SET),
            "preamp": PREAMP, "att": ATT,
            "widths": WIDTHS,
            "width_column": {m: {"W": width_column(m, False),
                                 "N": width_column(m, True)} for m in modes}}


# -- VFO ---------------------------------------------------------------------
@app.post("/api/frequency")
async def set_frequency(req: FrequencyReq) -> dict:
    await service.run(service.radio.set_freq, req.hz, req.vfo)
    return {"ok": True}


@app.post("/api/tune")
async def tune(req: TuneReq) -> dict:
    await service.run(service.radio.tune_steps, req.steps, req.knob)
    return {"ok": True}


@app.post("/api/band")
async def band(req: BandReq):
    if req.band not in BANDS:
        return _bad(f"unknown band {req.band!r}; one of {', '.join(BANDS)}")
    await service.run(service.radio.send, f"BS{BANDS[req.band]:02d}")
    return {"ok": True}


@app.post("/api/updown")
async def updown(req: OnOffReq) -> dict:
    """Microphone UP / DN keys: one step in the radio's own mic step grid
    (menus 087/088), so the UI needs no step table. ``on`` true = up."""
    await service.run(service.radio.send, "UP" if req.on else "DN")
    return {"ok": True}


@app.post("/api/split")
async def split(req: OnOffReq) -> dict:
    await service.run(service.radio.set_split, req.on)
    return {"ok": True}


@app.get("/api/ctcss/tones")
async def ctcss_tones() -> list:
    from .ft950 import CTCSS_TONES
    return CTCSS_TONES


@app.post("/api/ctcss")
async def ctcss(req: CtcssReq) -> dict:
    await service.run(service.radio.set_ctcss, req.mode, req.tone)
    return {"ok": True}


@app.post("/api/antenna")
async def antenna(req: AntennaReq) -> dict:
    await service.run(service.radio.set_antenna, req.ant)
    return {"ok": True}


@app.post("/api/tuner")
async def tuner(req: TunerReq) -> dict:
    """Tuner in/out; ``start`` runs a tuning cycle, which transmits a carrier."""
    if req.start:
        await service.run(service.radio.tuner_start)
    elif req.on is not None:
        await service.run(service.radio.set_tuner, req.on)
    return {"ok": True}


# -- memories (quick keys 1-32) ------------------------------------------------
@app.get("/api/memories")
async def memories(start: int = 1, end: int = 32) -> list:
    """Read-only (MR); empty channels come back as null. Name, split and
    VFO-B come from the Pi (the radio's memories have none of them)."""
    rt = runtime.load()
    labels, extra = rt.get("mem_labels") or {}, rt.get("mem_extra") or {}
    def read():
        out = []
        for ch in range(max(1, start), min(99, end) + 1):
            m = service.radio.read_memory(ch)
            d = m.to_dict() if m else {"channel": ch, "empty": True}
            x = extra.get(str(ch)) or {}
            d.update(label=labels.get(str(ch), ""), split=bool(x.get("split")),
                     vfo_b_hz=int(x.get("vfo_b_hz") or 0))
            out.append(d)
        return out
    return await asyncio.to_thread(read)


@app.get("/api/memory/modes")
async def memory_modes() -> list:
    from .ft950 import MW_MODES
    return MW_MODES


@app.post("/api/memory/write")
async def memory_write(req: MemoryWriteReq) -> dict:
    """Store frequency + mode in a radio memory (MW, verified by MR) and/or
    set the key's local name. Either part may be left out."""
    out: dict = {"channel": req.channel}
    if req.freq_hz is not None or req.mode is not None:
        if req.freq_hz is None or req.mode is None:
            raise HTTPException(400, "frequency and mode are both needed")
        try:
            out.update((await service.run(service.radio.write_memory, req.channel,
                                          req.freq_hz, req.mode, req.ctcss, req.tone)).to_dict())
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    if req.label is not None:
        labels = dict(runtime.load().get("mem_labels") or {})
        if req.label.strip():
            labels[str(req.channel)] = req.label.strip()
        else:
            labels.pop(str(req.channel), None)
        runtime.save({"mem_labels": labels})
    if req.split is not None or req.vfo_b_hz is not None:
        if req.vfo_b_hz and req.vfo_b_hz < 30_000:
            raise HTTPException(400, "VFO-B frequency out of range")
        extra = dict(runtime.load().get("mem_extra") or {})
        x = dict(extra.get(str(req.channel)) or {})
        if req.split is not None:
            x["split"] = req.split
        if req.vfo_b_hz is not None:
            x["vfo_b_hz"] = req.vfo_b_hz
        if x.get("split") or x.get("vfo_b_hz"):
            extra[str(req.channel)] = x
        else:
            extra.pop(str(req.channel), None)
        runtime.save({"mem_extra": extra})
    return out


@app.post("/api/memory/recall")
async def memory_recall(req: MemoryReq) -> dict:
    """Memory → VFO-A (frequency + mode, CTCSS in FM) plus the key's VFO-B
    and split from the Pi; the radio stays in VFO mode."""
    x = (runtime.load().get("mem_extra") or {}).get(str(req.channel)) or {}
    c = await service.run(service.radio.recall_to_vfo, req.channel,
                          int(x.get("vfo_b_hz") or 0) or None, bool(x.get("split")))
    return c.to_dict() | {"split": bool(x.get("split")), "vfo_b_hz": int(x.get("vfo_b_hz") or 0)}


@app.get("/api/serial/ports")
async def serial_ports() -> dict:
    from serial.tools import list_ports
    by_id = Path("/dev/serial/by-id")
    links = {str(p.resolve()): str(p) for p in by_id.iterdir()} if by_id.is_dir() else {}
    ports = [{"device": links.get(p.device, p.device), "tty": p.device,
              "description": p.description, "interface": p.interface or ""}
             for p in list_ports.comports()]
    return {"port": settings.serial_port, "baud": settings.serial_baud, "ports": ports,
            "bauds": [4800, 9600, 19200, 38400]}


@app.post("/api/vfo/swap")
async def vfo_swap() -> dict:
    await service.run(service.radio.send, "SV")
    return {"ok": True}


@app.post("/api/vfo/a-to-b")
async def vfo_a_to_b() -> dict:
    await service.run(service.radio.send, "AB")
    return {"ok": True}


# -- mode / receive chain ----------------------------------------------------
@app.post("/api/mode")
async def set_mode(req: ModeReq) -> dict:
    await service.run(service.radio.set_mode, req.mode)
    return {"ok": True}


@app.post("/api/width")
async def set_width(req: WidthReq) -> dict:
    # Returns the width actually applied: the radio caps it per mode
    # (DATA-U tops out at 2400 Hz), so the caller learns the real value.
    return await service.run(service.radio.set_width_hz, req.hz)


@app.post("/api/narrow")
async def set_narrow(req: OnOffReq) -> dict:
    await service.run(service.radio.set_narrow, req.on)
    return {"ok": True}


@app.post("/api/ifshift")
async def set_ifshift(req: IfShiftReq) -> dict:
    await service.run(service.radio.set_ifshift, req.hz)
    return {"ok": True}


@app.post("/api/clar")
async def clar(req: OnOffReq) -> dict:
    """RX clarifier on/off (RT), like the radio's CLAR key."""
    await service.run(service.radio.set_rx_clar, req.on)
    return {"ok": True}


@app.post("/api/clar/clear")
async def clar_clear() -> dict:
    """Clarifier offset back to 0 (RC)."""
    await service.run(service.radio.clear_clar)
    return {"ok": True}


@app.post("/api/clar/offset")
async def clar_offset(req: ClarReq) -> dict:
    """Clarifier offset in Hz (RC + RU/RD, read back from IF). With rx_on the
    RX clarifier is switched as well - moving the CLAR slider means listening
    off the dial, as turning the radio's CLAR knob does after pressing CLAR."""
    hz = await service.run(service.radio.set_clar, req.hz)
    if req.rx_on is not None:
        await service.run(service.radio.set_rx_clar, req.rx_on)
    return {"ok": True, "hz": hz}


@app.post("/api/vrf")
async def set_vrf(req: OnOffReq):
    """FLT on the LCD: VRF in line (on) or THRU (off)."""
    await service.run(service.radio.set_vrf, req.on)
    return {"ok": True}


@app.post("/api/agc")
async def set_agc(req: NameReq):
    if req.name not in AGC_SET:
        return _bad(f"AGC one of {', '.join(AGC_SET)}")
    await service.run(service.radio.set_agc, req.name)
    return {"ok": True}


@app.post("/api/roofing")
async def set_roofing(req: NameReq):
    if req.name not in ROOF_SET:
        return _bad(f"roofing one of {', '.join(ROOF_SET)}")
    await service.run(service.radio.set_roofing, req.name)
    return {"ok": True}


@app.post("/api/preamp")
async def set_preamp(req: LevelReq):
    if req.level not in PREAMP:
        return _bad("preamp 0 IPO, 1 AMP1, 2 AMP2")
    await service.run(service.radio.set_preamp, req.level)
    return {"ok": True}


@app.post("/api/att")
async def set_att(req: LevelReq) -> dict:
    await service.run(service.radio.set_att, req.level)
    return {"ok": True}


# On/off and 000-255 controls, whitelisted (the radio never rejects a set,
# so nothing else may reach it from the browser).
TOGGLES = {"NR0": "noise reduction", "NB0": "noise blanker",
           "BC0": "auto notch (DNF)", "LK": "dial lock", "PR": "speech processor"}
LEVELS = {"RG0": "RF gain", "SQ0": "squelch", "AG0": "AF gain",
          "NL0": "NB level"}


@app.post("/api/toggle")
async def toggle(req: ToggleReq):
    if req.cmd not in TOGGLES:
        return _bad(f"cmd one of {', '.join(TOGGLES)}")
    await service.run(service.radio.set_flag, req.cmd, req.on)
    return {"ok": True}


@app.post("/api/monitor")
async def monitor(req: OnOffReq) -> dict:
    """TX monitor on/off (ML0 + 000/001; the level stays as set on the radio)."""
    await service.run(service.radio.set_verified, f"ML0{int(req.on):03d}", "ML0")
    return {"ok": True}


@app.post("/api/level")
async def level(req: ValueReq):
    if req.cmd not in LEVELS:
        return _bad(f"cmd one of {', '.join(LEVELS)}")
    await service.run(service.radio.set_level, req.cmd, req.value)
    return {"ok": True}


@app.post("/api/power")
async def set_power(req: PowerReq) -> dict:
    await service.run(service.radio.set_power, req.watts)
    return {"ok": True}


# -- profiles ----------------------------------------------------------------
@app.post("/api/profile/voice")
async def profile_voice() -> dict:
    return {"report": await service.apply_voice_profile()}


@app.post("/api/profile/ft8")
async def profile_ft8() -> dict:
    return {"report": await service.apply_ft8_profile()}


# -- FT8 / FT4 ---------------------------------------------------------------
@app.get("/api/digi")
async def digi_status() -> dict:
    return {**digi.status(), "decodes": list(digi.decodes)[-100:]}


@app.get("/api/dxcc/locate")
async def dxcc_locate(call: str):
    """Entity centre for the session map when the station sent no locator."""
    hit = digi.dxcc.position(call)
    if not hit:
        return _bad(f"no DXCC entity for {call}")
    return {"call": call.upper(), "entity": hit[0], "lat": hit[1], "lon": hit[2]}


@app.post("/api/digi/config")
async def digi_config(req: DigiConfigReq):
    upd = req.model_dump(exclude_none=True)
    if "mode" in upd and upd["mode"] not in ("AUTO", *DIGI_MODES):
        return _bad(f"mode one of AUTO, {', '.join(DIGI_MODES)}")
    digi.config.update(upd)
    if "depth" in upd:                   # panel switch: survives a restart,
        runtime.save({"digi_depth": upd["depth"]})   # same key as Settings
    return digi.config


async def _stream(ws: WebSocket, mgr: ConnectionManager, first=None) -> None:
    await mgr.connect(ws)
    try:
        if first is not None:
            await ws.send_json(first)
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await mgr.disconnect(ws)


@app.websocket("/ws/digi")
async def ws_digi(ws: WebSocket) -> None:
    await _stream(ws, digi_manager, {"type": "hello", **digi.status(), "seq": sequencer.status()})


@app.websocket("/ws/waterfall")
async def ws_waterfall(ws: WebSocket) -> None:
    await _stream(ws, wf_manager)


# -- settings ----------------------------------------------------------------
def _cert_info() -> dict | None:
    try:
        import ssl
        c = ssl._ssl._test_decode_cert(str(CERTS / "cert.pem"))   # noqa: SLF001
    except Exception:  # noqa: BLE001
        return None
    return {"subject": dict(x[0] for x in c.get("subject", ())).get("commonName"),
            "issuer": dict(x[0] for x in c.get("issuer", ())).get("commonName"),
            "not_after": c.get("notAfter"),
            "names": [v for _, v in c.get("subjectAltName", ())]}


@app.get("/api/settings")
async def get_settings() -> dict:
    return {"version": APP_VERSION,
            "my_call": settings.my_call, "my_grid": settings.my_grid,
            "serial_port": settings.serial_port, "serial_baud": settings.serial_baud,
            "serial_bauds": [4800, 9600, 19200, 38400],
            "digi_depth": digi.config["depth"], "digi_low_hz": digi.config["low_hz"],
            "digi_high_hz": digi.config["high_hz"],
            "digi_decoder": _rt.get("digi_decoder") or "jt9",
            "digi_decoder_path": digi.config["jt9"],          # what actually runs (fallback: jt9)
            "digi_decoder_tag": decoder_tag(digi.config),     # "2.7" / "2.7 fast" / "3.3 MT"
            "digi_decoders": [{"id": d["id"], "label": d["label"], "available": decoder_available(d),
                               "mt": bool(d.get("mt"))}
                              for d in DIGI_DECODERS],
            "ft_closing": sequencer.closing,
            "hunt_target": sequencer.hunt_target, "hunt_min_snr": sequencer.hunt_min_snr,
            "hunt_minutes": sequencer.hunt_minutes,
            "hunt_blocked": sorted(({"call": c, **v} for c, v in sequencer.hunt_blocked.items()),
                                   key=lambda r: -r.get("t", 0)),         # newest first "digi_colors": _rt.get("digi_colors") or {},
            "cert": _cert_info(), "ca_available": CA_CERT.is_file(),
            "hackrf_url": _rt.get("hackrf_url") or HACKRF_DEFAULT,
            "shelly_host": power.host, "shelly_channel": power.channel, "shelly_user": power.user,
            "shelly_password_set": bool(power.password),
            "auto_power_off": bool(_rt.get("auto_power_off", False)),
            "auto_power_off_s": int(_rt.get("auto_power_off_s") or AUTO_OFF_DEFAULT_S)}           # the password never leaves the Pi


@app.post("/api/settings")
async def save_settings(req: SettingsReq) -> dict:
    upd = req.model_dump(exclude_none=True)
    if upd.pop("shelly_password_clear", False):
        upd["shelly_password"] = ""
    elif not upd.get("shelly_password"):
        upd.pop("shelly_password", None)       # empty field = keep the stored password
    if "my_call" in upd:
        upd["my_call"] = upd["my_call"].upper()
    if "my_grid" in upd:                     # canonical: JO62qk
        g = upd["my_grid"]
        upd["my_grid"] = g[:4].upper() + g[4:].lower()
    if "digi_decoder" in upd:
        d = next((x for x in DIGI_DECODERS if x["id"] == upd["digi_decoder"]), None)
        if not d or not decoder_available(d):
            raise HTTPException(400, f"Decoder {upd['digi_decoder']} is not installed")
        _rt["digi_decoder"] = upd["digi_decoder"]
        digi.config.update(decoder_config(upd["digi_decoder"]))   # from the next period on
    if "digi_colors" in upd:                 # merge: "" resets one colour to the theme
        cols = {**(_rt.get("digi_colors") or {}), **upd["digi_colors"]}
        upd["digi_colors"] = _rt["digi_colors"] = {k: v for k, v in cols.items() if v}
    runtime.save(upd)
    for k in ("hunt_target", "hunt_min_snr", "hunt_minutes"):
        if k in upd:
            _rt[k] = upd[k]
            setattr(sequencer, k, upd[k])
    if "ft_closing" in upd:
        _rt["ft_closing"] = sequencer.closing = upd["ft_closing"]
    if "hackrf_url" in upd:
        _rt["hackrf_url"] = upd["hackrf_url"].rstrip("/")
    for k in ("auto_power_off", "auto_power_off_s"):
        if k in upd:
            _rt[k] = upd[k]
    if any(k.startswith("shelly_") for k in upd):
        _rt.update({k: v for k, v in upd.items() if k.startswith("shelly_")})
        power.configure(_rt.get("shelly_host", ""), _rt.get("shelly_channel", 0),
                        _rt.get("shelly_user", ""), _rt.get("shelly_password", ""))
    for k in ("my_call", "my_grid"):
        if k in upd:
            setattr(settings, k, upd[k])
            digi.config[k] = upd[k]
    for k in ("depth", "low_hz", "high_hz"):
        if "digi_" + k in upd:
            digi.config[k] = upd["digi_" + k]
    port = upd.get("serial_port") or settings.serial_port
    baud = upd.get("serial_baud") or settings.serial_baud
    if port != settings.serial_port or baud != settings.serial_baud:
        settings.serial_port, settings.serial_baud = port, baud
        await asyncio.to_thread(service.set_port, port, baud)
    return await get_settings()


# -- audio settings ----------------------------------------------------------
def _radio_menu(n: int):
    try:
        return int(service.radio.get_menu(n)) if service.status.get("connected") else None
    except (FT950Error, ValueError):
        return None


@app.get("/api/audio")
async def get_audio() -> dict:
    devices, playback = await asyncio.gather(
        asyncio.to_thread(mixer.capture_devices),
        asyncio.to_thread(mixer.get_playback, capture.device))
    ex050, ex051 = await asyncio.to_thread(lambda: (_radio_menu(50), _radio_menu(51)))
    return {"device": capture.device, "devices": devices,
            "running": capture.running, "error": capture.error, **capture.level,
            "playback_percent": playback,
            "radio": {"tx_gain_050": ex050, "out_level_051": ex051}}


@app.post("/api/audio/device")
async def audio_device(req: DeviceReq) -> dict:
    runtime.save({"audio_device": req.device})
    settings.audio_device = req.device
    capture.set_device(req.device)
    return {"device": req.device}


@app.post("/api/audio/playback")
async def audio_playback(req: PercentReq) -> dict:
    runtime.save({"codec_playback": req.percent})
    return {"playback_percent": await asyncio.to_thread(
        mixer.set_playback, capture.device, req.percent)}


@app.get("/api/audio/rxlevel")
async def rx_level_get() -> dict:
    return {"db": capture.gain_db}


@app.post("/api/audio/rxlevel")
async def rx_level_set(req: RxLevelReq) -> dict:
    """DIGI RX level: software gain for decoder, waterfall and DIGI meter only
    (−20 … +20 dB). Browser audio, recorder and the radio are not affected."""
    capture.gain_db = req.db
    runtime.save({"rx_level_db": req.db})
    return {"db": capture.gain_db}


@app.post("/api/audio/radio")
async def audio_radio(req: RadioAudioReq) -> dict:
    """Radio-side data levels over CAT (fire & verify): 051 is the only way to
    set the RX level into the Pi, 050 the DATA-mode TX gain."""
    await service.run(service.radio.set_menu, req.menu, f"{req.value:03d}")
    return {"menu": req.menu, "value": req.value}


# -- FT-950 audio menus (EX) -----------------------------------------------------
_menu_raw: dict[int, str] = {}              # last read answer per menu: the wire format


def _read_menus() -> dict[int, str | None]:
    out: dict[int, str | None] = {}
    for m in exmenus.MENUS:
        try:
            out[m.no] = service.radio.get_menu(m.no)
        except FT950Error:
            out[m.no] = None
    _menu_raw.update({k: v for k, v in out.items() if v})
    return out


def _require_radio() -> None:
    if not service.status.get("connected"):
        raise HTTPException(400, "Radio not connected")


@app.get("/api/radio/menus")
async def radio_menus() -> dict:
    """All audio menus with their current value and the saved default. The
    first complete read becomes the default — the configuration that works."""
    _require_radio()
    raw = await asyncio.to_thread(_read_menus)
    defaults, created = exmenus.load_defaults(), False
    if not defaults and all(raw.values()):
        defaults, created = exmenus.save_defaults(raw), True
    return {"items": [exmenus.item(m, raw[m.no], defaults.get(f"{m.no:03d}")) for m in exmenus.MENUS],
            "has_defaults": bool(defaults), "defaults_created": created}


@app.post("/api/radio/menu")
async def radio_menu_set(req: MenuSetReq) -> dict:
    _require_radio()
    m = exmenus.BY_NO.get(req.no)
    if not m:
        raise HTTPException(400, f"EX{req.no:03d} is not one of the listed audio menus")
    try:
        exmenus.validate(m, req.value)
        like = _menu_raw.get(m.no) or await asyncio.to_thread(service.radio.get_menu, m.no)
        raw = exmenus.fmt(req.value, like)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    await service.run(service.radio.set_menu, m.no, raw)       # verified by read-back
    _menu_raw[m.no] = raw
    return exmenus.item(m, raw, exmenus.load_defaults().get(f"{m.no:03d}"))


@app.post("/api/radio/menus/reset")
async def radio_menus_reset(req: MenuResetReq) -> dict:
    """Write the default back — only where the radio differs from it."""
    _require_radio()
    defaults = exmenus.load_defaults()
    if not defaults:
        raise HTTPException(400, "No default saved yet")
    raw = await asyncio.to_thread(_read_menus)
    done, failed = [], []
    for m in exmenus.MENUS:
        d = defaults.get(f"{m.no:03d}")
        if (req.nos and m.no not in req.nos) or not d or not raw[m.no]:
            continue
        if exmenus.parse(raw[m.no]) == exmenus.parse(d):
            continue
        try:
            await service.run(service.radio.set_menu, m.no, exmenus.fmt(exmenus.parse(d), raw[m.no]))
            done.append(m.no)
        except FT950Error as exc:
            failed.append({"no": m.no, "error": str(exc)})
    return {"reset": done, "failed": failed, **(await radio_menus())}


@app.post("/api/radio/menus/defaults")
async def radio_menus_snapshot() -> dict:
    """Take the radio's current values as the new default."""
    _require_radio()
    raw = await asyncio.to_thread(_read_menus)
    if not all(raw.values()):
        raise HTTPException(400, "Not all menus readable — default unchanged")
    exmenus.save_defaults(raw)
    return await radio_menus()


# -- Pi hardware -------------------------------------------------------------
@app.get("/api/system")
async def get_system() -> dict:
    return await asyncio.to_thread(system_info.collect)


@app.get("/api/system/history")
async def get_system_history(hours: float = 24.0) -> dict:
    return sys_history.series(min(max(hours, 0.0), 24.0))


# -- branding (title-bar logo) ------------------------------------------------
# The logo is a trademark: it lives only on this Pi (branding/ is gitignored)
# and is fetched or uploaded from Settings, never shipped with the code — the
# same rule as the Kenwood logo in TM-V71 Remote.
BRANDING = Path(__file__).resolve().parents[1] / "branding"
LOGO_TYPES = {"svg": "image/svg+xml", "png": "image/png", "jpg": "image/jpeg",
              "jpeg": "image/jpeg", "webp": "image/webp", "gif": "image/gif"}
YAESU_LOGO_URL = "https://upload.wikimedia.org/wikipedia/commons/3/3f/Yaesu_logo.svg"


def _logo() -> Path | None:
    return next((f for f in sorted(BRANDING.glob("logo.*"))
                 if f.suffix[1:] in LOGO_TYPES), None)


def _save_logo(data: bytes, ext: str) -> None:
    if ext not in LOGO_TYPES:
        raise HTTPException(400, f"image type one of {', '.join(LOGO_TYPES)}")
    if not data or len(data) > 2_000_000:
        raise HTTPException(400, "logo must be 1 byte … 2 MB")
    BRANDING.mkdir(exist_ok=True)
    for old in BRANDING.glob("logo.*"):
        old.unlink()
    (BRANDING / f"logo.{ext}").write_bytes(data)


@app.get("/api/branding")
async def branding() -> dict:
    f = _logo()
    return {"has_logo": f is not None, "version": int(f.stat().st_mtime) if f else None}


@app.get("/api/branding/logo")
async def branding_logo():
    f = _logo()
    if not f:
        raise HTTPException(404, "no logo")
    return FileResponse(f, media_type=LOGO_TYPES[f.suffix[1:]],
                        headers={"Cache-Control": "no-cache"})


@app.post("/api/branding/logo")
async def upload_logo(request: Request, ext: str) -> dict:
    """Raw image body (no multipart dependency): POST …/logo?ext=png."""
    _save_logo(await request.body(), ext.lower())
    return await branding()


@app.post("/api/branding/logo/yaesu")
async def fetch_yaesu_logo() -> dict:
    """Download the Yaesu wordmark from Wikimedia Commons, server-side."""
    req = urllib.request.Request(YAESU_LOGO_URL, headers={
        "User-Agent": f"ft950-remote/{APP_VERSION} (LAN ham radio remote)"})

    def _download() -> bytes:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read()
    try:
        data = await asyncio.to_thread(_download)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"download failed: {exc}") from exc
    _save_logo(data, "svg")
    return await branding()


@app.delete("/api/branding/logo")
async def delete_logo() -> dict:
    for f in BRANDING.glob("logo.*"):
        f.unlink()
    return await branding()


# -- browser audio (WebRTC / Opus) + voice PTT ----------------------------------
@app.post("/api/webrtc/offer")
async def webrtc_offer(req: WebRTCOffer) -> dict:
    """SDP offer → answer: radio RX track out, browser mic in (Opus, mono).
    Non-trickle ICE, as in TM-V71 Remote."""
    pc = RTCPeerConnection()
    pcs.add(pc)
    hub.peers += 1
    peer = uuid.uuid4().hex[:8]             # names this browser's mic

    @pc.on("connectionstatechange")
    async def _state() -> None:
        # "disconnected" is usually a transient ICE blip; only tear down on
        # the terminal states
        if pc.connectionState in ("failed", "closed") and pc in pcs:
            pcs.discard(pc)
            hub.peers = max(0, hub.peers - 1)
            await pc.close()

    @pc.on("track")
    def _track(track) -> None:
        if track.kind == "audio":
            asyncio.ensure_future(consume_mic(track, hub, peer))

    pc.addTrack(RadioRxTrack(hub, peer))
    await pc.setRemoteDescription(RTCSessionDescription(sdp=force_opus_mono(req.sdp), type=req.type))
    await pc.setLocalDescription(await pc.createAnswer())
    while pc.iceGatheringState != "complete":
        await asyncio.sleep(0.05)
    return {"sdp": force_opus_mono(pc.localDescription.sdp), "type": pc.localDescription.type,
            "peer": peer}


@app.get("/api/audio/record")
async def audio_record_status() -> dict:
    return recorder.status()


@app.post("/api/audio/record")
async def audio_record(req: OnOffReq) -> dict:
    """Start (fresh take) / stop the raw RX recorder."""
    recorder.set(req.on)
    return recorder.status()


@app.get("/api/audio/record.wav")
async def audio_record_wav() -> Response:
    """The take as 16-bit mono 16 kHz WAV."""
    data = await asyncio.to_thread(recorder.wav_bytes)
    name = time.strftime("ft950-rx-%Y-%m-%d_%H%M%S.wav", time.gmtime())
    return Response(data, media_type="audio/wav",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/api/audio/voice")
async def voice_status() -> dict:
    return {**hub.config(), "peers": hub.peers, "mic": hub.mic_tracks > 0, "levels": hub.levels}


@app.post("/api/audio/voice")
async def voice_config(req: VoiceCfgReq) -> dict:
    upd = req.model_dump(exclude_none=True)
    for k, v in upd.items():
        setattr(hub, k, v)
    runtime.save(upd)
    return hub.config()


@app.post("/api/audio/mictest")
async def mic_test(req: PttReq) -> dict:
    """Echo test: record the browser mic (RX muted), replay it on switch-off."""
    if req.on and tx.state["active"]:
        raise HTTPException(400, "No mic test while transmitting")
    if req.on:
        hub.claim_mic(req.peer)
    hub.set_mic_test(req.on)
    return {"mic_test": hub.mic_test, "echo": hub.echo_busy}


@app.post("/api/ptt")
async def ptt(req: PttReq) -> dict:
    """Voice PTT. While keyed the page repeats on=true every second (lease)."""
    return await tx.ptt(req.on, req.peer)


@app.websocket("/ws/audio")
async def ws_audio(ws: WebSocket) -> None:
    await _stream(ws, audio_manager)


# -- transmit: TUNE ----------------------------------------------------------
@app.exception_handler(RuntimeError)
async def runtime_error(_: Request, exc: RuntimeError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.get("/api/tx")
async def tx_status() -> dict:
    return tx.status()


@app.post("/api/tx/tune")
async def tx_tune(req: TuneReq2) -> dict:
    """Steady tone at the TX offset, max 10 s (backend watchdog)."""
    return await tx.tune(req.hz) if req.on else await tx.stop()


@app.post("/api/tx/tone1750")
async def tx_tone1750(req: OnOffReq) -> dict:
    """1750 Hz tone call, as TM-V71 Remote: the button arms it, PTT sends it
    (instead of the microphone), releasing PTT disarms it. FM modes only."""
    from .tx import TONE_MODES
    if req.on:
        mode = ((service.status or {}).get("vfo") or {}).get("mode")
        if mode not in TONE_MODES:
            raise HTTPException(400, f"1750 Hz is for FM repeaters — radio is in {mode}")
        hub.tone_level = tx.level
    hub.tone_1750 = req.on
    return {"tone_1750": hub.tone_1750}


@app.post("/api/tx/halt")
async def tx_halt() -> dict:
    """Halt TRX: the sequencer, Auto Hunt and any transmission, at once
    (Hunt would otherwise call the next station a period later)."""
    if sequencer.hunt:
        await sequencer.set_hunt(False)
    await sequencer.halt()
    return await tx.stop()


# -- FT8/FT4 auto-sequencer ----------------------------------------------------
@app.get("/api/seq")
async def seq_status() -> dict:
    return sequencer.status()


@app.post("/api/seq/cq")
async def seq_cq(req: SeqCqReq) -> dict:
    return await sequencer.start_cq(req.tx_hz)


@app.post("/api/seq/answer")
async def seq_answer(req: SeqAnswerReq) -> dict:
    d = dict(req.decode)
    missing = [k for k in ("msg", "t0", "snr", "df", "mode") if k not in d]
    if missing:
        raise HTTPException(400, f"Decode incomplete: {', '.join(missing)}")
    d.update(analyse(d["msg"]))             # never trust the browser's parse
    return await sequencer.answer(d, req.tx_hz)


@app.post("/api/seq/sessions/clear")
async def seq_sessions_clear() -> dict:
    return await sequencer.clear_sessions()


@app.post("/api/seq/auto")
async def seq_auto(req: SeqAutoReq) -> dict:
    """Auto answer on/off (the DIGI strip's Answer switch)."""
    return await sequencer.set_auto(req.on, req.tx_hz, req.adopt)


@app.post("/api/seq/hunt")
async def seq_hunt(req: SeqAutoReq) -> dict:
    """Hunt on/off (the DIGI strip's auto-clicker): calls new stations' CQs."""
    return await sequencer.set_hunt(req.on, req.tx_hz, req.adopt)


@app.post("/api/seq/hunt/block")
async def seq_hunt_block(req: HuntBlockReq) -> dict:
    """Add a call to Hunt's no-reply list, or take one off."""
    return await sequencer.set_hunt_blocked(req.call, req.blocked)


@app.post("/api/seq/hunt/clear")
async def seq_hunt_clear() -> dict:
    """Empty Hunt's no-reply list."""
    return await sequencer.clear_hunt_blocked()


@app.post("/api/seq/halt")
async def seq_halt() -> dict:
    return await sequencer.halt()


@app.post("/api/tx/level")
async def tx_level(req: PercentReq) -> dict:
    tx.level = req.percent / 100
    runtime.save({"tx_level": req.percent})
    return tx.status()


# -- logbook (Wavelog + QRZ.com) -----------------------------------------------
def _logged(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except LogError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/log/status")
async def log_status() -> dict:
    return logbook.status()


@app.post("/api/log/config")
async def log_config(req: LogConfigReq) -> dict:
    return logbook.configure(req.model_dump(exclude_none=True))


@app.post("/api/log/test")
async def log_test() -> dict:
    w, q, ql = await asyncio.gather(asyncio.to_thread(logbook.wavelog.test),
                                    asyncio.to_thread(logbook.qrz.test),
                                    asyncio.to_thread(logbook.qrz.test_logbook))
    return {"wavelog": w, "qrz": q, "qrz_logbook": ql}


@app.get("/api/log/stations")
async def log_stations() -> list:
    return await asyncio.to_thread(logbook.wavelog.stations)


@app.post("/api/log/sync")
async def log_sync(full: bool = False) -> dict:
    return await asyncio.to_thread(_logged, logbook.sync, full)


@app.get("/api/log/lookup")
async def log_lookup(call: str) -> dict:
    vfo = service.status.get("vfo") or {}
    return await asyncio.to_thread(_logged, logbook.lookup, zero_call(call),
                                   band_for_hz(vfo.get("freq_hz")))


@app.get("/api/log/recent")
async def log_recent() -> list:
    return logbook.recent()


@app.delete("/api/log/recent/{ts}")
async def log_delete(ts: str) -> list:
    return logbook.delete_recent(ts)


@app.post("/api/log/qso")
async def log_qso(req: QsoReq) -> dict:
    """Log a QSO by hand. Frequency/mode default to the live radio state."""
    q = req.model_dump()
    vfo = service.status.get("vfo") or {}
    if not q["freq_hz"] and vfo.get("freq_hz"):
        q["freq_hz"] = vfo["freq_hz"] + (q.pop("tx_offset_hz") or 0)
    q.pop("tx_offset_hz", None)
    q["mode"] = q["mode"] or digi.current_mode()
    q["tx_pwr"] = service.status.get("power")
    to = tuple(q.pop("to", None) or ("wavelog", "qrz"))
    return await asyncio.to_thread(_logged, logbook.log_qso, q, to=to,
                                   station_callsign=settings.my_call, my_grid=settings.my_grid)


# -- certificate -------------------------------------------------------------
# -- CW: skimmer (decode every carrier in the passband) + the radio's keyer ----
async def _cw_broadcast_loop() -> None:
    last = -1
    while True:
        await asyncio.sleep(0.25)
        if cw_manager.count and skimmer.enabled and skimmer.version != last:
            last = skimmer.version
            await cw_manager.broadcast({"t": "cw", **skimmer.snapshot()})


_cw_job: asyncio.Task | None = None


async def _cw_send(text: str, wpm: int) -> None:
    """Chunks of ≤ 50 characters (one keyer memory), each played to the end."""
    words, chunk = text.split(), ""
    chunks = []
    for w in words:
        if len(chunk) + len(w) + 1 > 50:
            chunks.append(chunk)
            chunk = ""
        chunk = (chunk + " " + w).strip()
    if chunk:
        chunks.append(chunk)
    for c in chunks:
        await service.run(service.radio.cw_play, c)
        log.info("CW TX %r at %d WPM", c, wpm)
        await asyncio.sleep(cw_duration_s(c + " ", wpm) + 0.2)


@app.get("/api/cw")
async def cw_status() -> dict:
    radio_cw = None
    if service.status.get("connected"):
        try:
            radio_cw = await asyncio.to_thread(service.radio.get_cw)
        except FT950Error:
            radio_cw = None
    return {**skimmer.snapshot(), "radio": radio_cw,
            "sending": bool(_cw_job and not _cw_job.done())}


@app.post("/api/cw/enable")
async def cw_enable(req: OnOffReq) -> dict:
    skimmer.set_enabled(req.on)
    return skimmer.snapshot()


@app.post("/api/cw/clear")
async def cw_clear() -> dict:
    skimmer.clear()
    return skimmer.snapshot()


@app.post("/api/cw/speed")
async def cw_speed(req: CwSpeedReq) -> dict:
    await service.run(service.radio.set_cw_speed, req.wpm)
    return {"ok": True}


@app.post("/api/cw/breakin")
async def cw_breakin(req: OnOffReq) -> dict:
    await service.run(service.radio.set_breakin, req.on)
    return {"ok": True}


@app.post("/api/cw/send")
async def cw_send(req: CwSendReq) -> dict:
    """Send text in CW with the radio's keyer (radio in CW / CW-R, break-in on)."""
    global _cw_job
    mode = (service.status.get("vfo") or {}).get("mode")
    if mode not in ("CW", "CW-R"):
        raise HTTPException(400, f"CW needs the radio in CW or CW-R — it is in {mode}")
    if _cw_job and not _cw_job.done():
        raise HTTPException(409, "still sending")
    cw = await asyncio.to_thread(service.radio.get_cw)
    if not cw["breakin"]:
        raise HTTPException(400, "Switch break-in (BK-IN) on first — the keyer only transmits with break-in")
    _cw_job = asyncio.create_task(_cw_send(req.text.upper(), cw["wpm"]))
    return {"ok": True, "seconds": round(cw_duration_s(req.text, cw["wpm"]), 1)}


@app.post("/api/cw/stop")
async def cw_stop() -> dict:
    """Drop the remaining chunks; the radio finishes the one it is playing."""
    if _cw_job and not _cw_job.done():
        _cw_job.cancel()
    return {"ok": True}


@app.websocket("/ws/cw")
async def ws_cw(ws: WebSocket) -> None:
    await _stream(ws, cw_manager)


# -- HF band conditions (VOACAP for the own locator; the strip under DIGI) -------
prop = Propagation()


@app.get("/api/propagation")
async def propagation_status() -> dict:
    """Four band pairs, green / yellow / red, for the current UTC hour - see
    propagation.py for how a place becomes a set of VOACAP paths."""
    return await asyncio.to_thread(prop.status, settings.my_grid)


# -- radio power (Shelly relay on the 13.8 V line) ------------------------------
AUTO_OFF_DEFAULT_S = 300


async def _power_off_safely(why: str) -> None:
    await sequencer.halt()                       # never cut the supply mid-transmission
    await tx.stop()
    await asyncio.to_thread(power.set, False)
    log.info("radio power OFF (%s)", why)


async def _auto_power_off_loop() -> None:
    """Auto power-off (as TM-V71 Remote): no browser on the page for
    ``auto_power_off_s`` → stop TX and switch the radio off via the Shelly."""
    since: float | None = None
    while True:
        await asyncio.sleep(5)
        if not (_rt.get("auto_power_off") and power.configured) or manager.count:
            since = None
            continue
        since = since or time.monotonic()
        limit = int(_rt.get("auto_power_off_s") or AUTO_OFF_DEFAULT_S)
        if time.monotonic() - since < limit:
            continue
        since = None
        try:
            if await asyncio.to_thread(power.get):
                await _power_off_safely(f"auto power-off: no browser for {limit} s")
        except ShellyError as exc:
            log.warning("auto power-off: %s", exc)

@app.get("/api/power-switch")
async def power_status() -> dict:
    return await asyncio.to_thread(power.status)


@app.post("/api/power-switch")
async def power_set(req: PowerSwitchReq) -> dict:
    if not power.configured:
        raise HTTPException(400, "No Shelly configured (Settings ▸ General)")
    try:
        if not req.on:
            await _power_off_safely("switch in the title bar")
        else:
            wait = power.off_remaining()         # let the DC line really drop first
            if wait > 0:
                await asyncio.sleep(wait)
            await asyncio.to_thread(power.set, True)
            log.info("radio power ON (Shelly %s)", power.host)
    except ShellyError as exc:
        raise HTTPException(502, str(exc)) from exc
    return await asyncio.to_thread(power.status)


# -- HackRF waterfall (relayed from TM-V71 Remote on the HackRF's Pi) -----------
@app.get("/api/hackrf")
async def hackrf_status() -> dict:
    return await hackrf.status()


async def _hackrf(coro) -> dict:
    try:
        return await coro
    except HackRFError as exc:
        raise HTTPException(502, str(exc)) from exc


@app.post("/api/hackrf/start")
async def hackrf_start(cfg: HackRFConfig) -> dict:
    return await _hackrf(hackrf.start(cfg.model_dump(exclude_none=True)))


@app.post("/api/hackrf/stop")
async def hackrf_stop() -> dict:
    return await _hackrf(hackrf.stop())


@app.post("/api/hackrf/config")
async def hackrf_config(cfg: HackRFConfig) -> dict:
    return await _hackrf(hackrf.configure(cfg.model_dump(exclude_none=True)))


@app.websocket("/ws/hackrf")
async def ws_hackrf(ws: WebSocket) -> None:
    await hackrf.relay(ws)


@app.get("/api/cert/ca.crt")
async def ca_cert():
    """The private root CA, for devices that do not trust it yet (the browser
    microphone and an installed PWA need a trusted HTTPS origin)."""
    if not CA_CERT.is_file():
        return JSONResponse(status_code=404, content={"detail": "no CA certificate on this Pi"})
    return FileResponse(CA_CERT, media_type="application/x-x509-ca-cert",
                        filename="ft950-remote-ca.crt")


# PTT is intentionally absent until the audio bridge and the backend
# watchdog exist (TM-V71 Remote rule: no PTT without connected audio).

if FRONTEND.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
