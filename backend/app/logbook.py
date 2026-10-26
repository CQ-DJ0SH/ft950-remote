"""Logbook: Wavelog upload, QRZ.com lookup, and the "worked before" index.

Adapted from TM-V71 Remote's logbook.py (Wavelog JSON API + QRZ XML API, stdlib
urllib only, run via asyncio.to_thread). Added for FT8/FT4:

* ADIF as WSJT-X writes it: FT8 is ``MODE=FT8``, FT4 is ``MODE=MFSK`` with
  ``SUBMODE=FT4`` (ADIF 3.1); reports are dB values.
* QRZ.com lookup *before* logging fills name/QTH/grid/country when enabled
  (XML Data API: username/password); with a QRZ.com logbook API key every
  QSO is also uploaded to the QRZ.com logbook (``ACTION=INSERT``).
* A worked-before index downloaded from Wavelog (``get_contacts_adif``,
  incremental via ``lastfetchedid``), so decodes can be marked like iDigi
  does: worked call → red, new call → green, new DXCC → flagged.
* ``auto_log``: QSOs completed by the auto-sequencer are logged without a
  click — the hook is :meth:`LogBook.log_qso`; until TX exists, nothing calls it.

Not yet verified against a live Wavelog instance (none reachable while this was
written): the endpoint names follow Wavelog's API controller.
"""
from __future__ import annotations

import json
import logging
import os
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import runtime
from .config import APP_VERSION
from .dxcc import Dxcc

log = logging.getLogger("ft950.log")

DATA = Path(__file__).resolve().parents[1]
RECENT_FILE = DATA / "logbook.json"
WORKED_FILE = DATA / "worked.json"
RECENT_MAX = 100

CONFIG_KEYS = ("wavelog_url", "wavelog_key", "wavelog_station_id",
               "qrz_username", "qrz_password", "qrz_api_key", "auto_log", "qrz_before_log")
SECRETS = ("wavelog_key", "qrz_password", "qrz_api_key")


class LogError(Exception):
    """A logbook provider request failed (network / API error)."""


# --- helpers ----------------------------------------------------------------
_BANDS = [
    (1_800_000, 2_000_000, "160m"), (3_500_000, 4_000_000, "80m"),
    (5_250_000, 5_450_000, "60m"), (7_000_000, 7_300_000, "40m"),
    (10_100_000, 10_150_000, "30m"), (14_000_000, 14_350_000, "20m"),
    (18_068_000, 18_168_000, "17m"), (21_000_000, 21_450_000, "15m"),
    (24_890_000, 24_990_000, "12m"), (28_000_000, 29_700_000, "10m"),
    (50_000_000, 54_000_000, "6m"),
]


def band_for_hz(hz: Optional[int]) -> str:
    for lo, hi, name in _BANDS:
        if hz and lo <= hz <= hi:
            return name
    return ""


def adif_mode(mode: str) -> tuple[str, str]:
    """(MODE, SUBMODE) per ADIF 3.1 — FT4 is a submode of MFSK."""
    mode = (mode or "").upper()
    return ("MFSK", "FT4") if mode == "FT4" else (mode, "")


def _ssl_ctx() -> ssl.SSLContext:
    # Wavelog usually runs on the LAN with a self-signed/internal certificate.
    return ssl._create_unverified_context()  # noqa: SLF001


def _net_reason(e: Exception) -> str:
    """Operator-readable reason for a failed request."""
    r = getattr(e, "reason", e)
    if isinstance(r, ConnectionRefusedError):
        return "Connection refused — is Wavelog running at this address and port?"
    if isinstance(r, (TimeoutError,)) or "timed out" in str(r):
        return "no answer (timeout) — check address/network"
    if "Name or service not known" in str(r) or "nodename" in str(r):
        return "unknown hostname"
    if isinstance(e, urllib.error.HTTPError) and e.code == 404:
        return "API not found (404) — is this the Wavelog base URL?"
    return str(r)


def _adif_field(name: str, value) -> str:
    if value is None:
        return ""
    s = str(value).strip()
    if not s:
        return ""
    return f"<{name.upper()}:{len(s.encode('utf-8'))}>{s}"   # length in bytes


def build_adif(q: dict) -> str:
    mode, sub = adif_mode(q.get("mode", ""))
    fields = [
        ("CALL", q.get("call")), ("QSO_DATE", q.get("qso_date")),
        ("TIME_ON", q.get("time_on")), ("TIME_OFF", q.get("time_off")),
        ("FREQ", q.get("freq_mhz")), ("BAND", q.get("band")),
        ("MODE", mode), ("SUBMODE", sub),
        ("RST_SENT", q.get("rst_sent")), ("RST_RCVD", q.get("rst_rcvd")),
        ("NAME", q.get("name")), ("GRIDSQUARE", q.get("gridsquare")),
        ("QTH", q.get("qth")), ("COUNTRY", q.get("country")),
        ("TX_PWR", q.get("tx_pwr")), ("COMMENT", q.get("comment")),
        ("STATION_CALLSIGN", q.get("station_callsign")),
        ("MY_GRIDSQUARE", q.get("my_grid")),
    ]
    return "".join(_adif_field(n, v) for n, v in fields) + "<EOR>"


_ADIF_TAG = re.compile(rb"<([A-Za-z0-9_]+):(\d+)(?::[A-Za-z])?>")
_EOR = re.compile(rb"<eor>", re.I)


def parse_adif(text: str | bytes) -> list[dict]:
    """Minimal ADIF reader: records as {FIELD: value}, header skipped.

    Works on bytes: an ADIF length counts bytes, so a UTF-8 name like "Jörg"
    is <NAME:5> — slicing characters would eat into the next tag.
    """
    data = text.encode("utf-8") if isinstance(text, str) else text
    eoh = re.search(rb"<eoh>", data, re.I)
    pos = eoh.end() if eoh else 0
    records, rec = [], {}
    while True:
        m = _ADIF_TAG.search(data, pos)
        e = _EOR.search(data, pos)
        if e and (not m or e.start() < m.start()):
            if rec:
                records.append(rec)
            rec, pos = {}, e.end()
            continue
        if not m:
            break
        n = int(m.group(2))
        rec[m.group(1).decode().upper()] = data[m.end():m.end() + n].decode("utf-8", "replace")
        pos = m.end() + n
    if rec:
        records.append(rec)
    return records


# --- worked-before index ----------------------------------------------------
class WorkedIndex:
    """Calls and DXCC entities in the log, overall and per band."""

    def __init__(self, dxcc: Dxcc) -> None:
        self.dxcc = dxcc
        self.calls: set[str] = set()
        self.call_band: set[str] = set()      # "CALL|20m"
        self.entities: set[str] = set()
        self.entity_band: set[str] = set()    # "Germany|20m"
        self.last_id = 0
        self.synced_at: Optional[float] = None
        self.count = 0
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        try:
            d = json.loads(WORKED_FILE.read_text())
        except (OSError, ValueError):
            return
        self.calls, self.call_band = set(d.get("calls", [])), set(d.get("call_band", []))
        self.entities, self.entity_band = set(d.get("entities", [])), set(d.get("entity_band", []))
        self.last_id, self.synced_at = d.get("last_id", 0), d.get("synced_at")
        self.count = d.get("count", 0)

    def save(self) -> None:
        with self._lock:
            data = {"calls": sorted(self.calls), "call_band": sorted(self.call_band),
                    "entities": sorted(self.entities), "entity_band": sorted(self.entity_band),
                    "last_id": self.last_id, "synced_at": self.synced_at, "count": self.count}
        tmp = WORKED_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        os.replace(tmp, WORKED_FILE)

    def reset(self) -> None:
        with self._lock:
            self.calls, self.call_band, self.entities, self.entity_band = set(), set(), set(), set()
            # also "not synced": an empty index that claims to be current would
            # mark every station as new
            self.last_id, self.count, self.synced_at = 0, 0, None

    def add(self, call: str, band: str) -> None:
        call = (call or "").upper().strip()
        if not call:
            return
        ent = self.dxcc.lookup(call)
        with self._lock:
            self.calls.add(call)
            self.count += 1
            if band:
                self.call_band.add(f"{call}|{band}")
            if ent:
                self.entities.add(ent)
                if band:
                    self.entity_band.add(f"{ent}|{band}")

    def status(self, call: Optional[str], band: str, entity: Optional[str]) -> dict:
        call = (call or "").upper()
        with self._lock:
            return {
                "call": call in self.calls,
                "call_band": f"{call}|{band}" in self.call_band,
                "dxcc": bool(entity) and entity in self.entities,
                "dxcc_band": bool(entity) and f"{entity}|{band}" in self.entity_band,
            }

    def summary(self) -> dict:
        with self._lock:
            return {"qsos": self.count, "calls": len(self.calls),
                    "entities": len(self.entities), "synced_at": self.synced_at,
                    "last_id": self.last_id}


# --- providers --------------------------------------------------------------
class Wavelog:
    def __init__(self, url: str = "", key: str = "", station_id: str = "") -> None:
        self.url, self.key, self.station_id = (url or "").rstrip("/"), key or "", str(station_id or "")

    def configured(self) -> bool:
        return bool(self.url and self.key)

    def _request(self, ep: str, payload: Optional[dict] = None,
                 key_in_url: bool = False, timeout: float = 10.0):
        # /index.php/api/<ep> always works; /api/<ep> needs URL rewriting.
        last_err: Optional[Exception] = None
        for root in (f"{self.url}/index.php/api", f"{self.url}/api"):
            url = f"{root}/{ep}" + (f"/{self.key}" if key_in_url else "")
            req = urllib.request.Request(
                url, data=json.dumps(payload or {}).encode(), method="POST",
                headers={"Content-Type": "application/json", "Accept": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx()) as r:
                    return self._parse(r.read().decode("utf-8", "replace")), getattr(r, "status", 200)
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    last_err = e
                    continue
                raw = e.read().decode("utf-8", "replace") if e.fp else ""
                body = self._parse(raw)
                if isinstance(body, dict):
                    return body, e.code
                raise LogError(f"HTTP {e.code}: {raw[:160] or e.reason}") from e
            except Exception as e:  # noqa: BLE001
                last_err = e
        raise LogError(_net_reason(last_err) if last_err else "request failed")

    @staticmethod
    def _parse(raw: str):
        raw = (raw or "").strip()
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except ValueError:
            return {"message": raw}

    def test(self) -> dict:
        if not self.configured():
            return {"ok": False, "message": "URL and API key missing"}
        try:
            body, _ = self._request("version", {"key": self.key})
            if isinstance(body, dict) and body.get("version"):
                return {"ok": True, "message": f"Wavelog {body['version']}"}
            st, code = self._request("station_info", key_in_url=True)
        except LogError as e:
            return {"ok": False, "message": str(e)}
        if isinstance(st, list):
            return {"ok": True, "message": f"{len(st)} Stationsprofil(e)"}
        return {"ok": False, "message": f"unexpected response ({code})"}

    def stations(self) -> list[dict]:
        if not self.configured():
            return []
        try:
            body, _ = self._request("station_info", key_in_url=True)
        except LogError:
            return []
        return [{"id": str(s.get("station_id", "")), "name": s.get("station_profile_name", ""),
                 "callsign": s.get("station_callsign", ""), "grid": s.get("station_gridsquare", "")}
                for s in body] if isinstance(body, list) else []

    def log_qso(self, adif: str, power_w: Optional[float] = None) -> dict:
        payload = {"key": self.key, "station_profile_id": self.station_id,
                   "type": "adif", "string": adif}
        if power_w:
            payload["power"] = power_w
        try:
            body, code = self._request("qso", payload)
        except LogError as e:
            return {"ok": False, "message": str(e)}
        msg = ""
        if isinstance(body, dict):
            msg = body.get("messages") or body.get("message") or body.get("status") or ""
            if isinstance(msg, list):
                msg = "; ".join(str(m) for m in msg)
        ok = code in (200, 201) and "error" not in str(msg).lower()
        return {"ok": ok, "message": str(msg) or ("geloggt" if ok else "fehlgeschlagen")}

    def fetch_adif(self, from_id: int) -> tuple[list[dict], int]:
        """QSOs of the station profile with id > from_id, plus the new last id."""
        if not (self.configured() and self.station_id):
            raise LogError("Wavelog URL, API key and station profile required")
        body, code = self._request("get_contacts_adif", {
            "key": self.key, "station_id": self.station_id, "fetchfromid": from_id},
            timeout=60)
        if not isinstance(body, dict) or "adif" not in body:
            msg = body.get("message") if isinstance(body, dict) else body
            raise LogError(f"Download fehlgeschlagen ({code}): {msg}")
        return parse_adif(body.get("adif") or ""), int(body.get("lastfetchedid") or from_id)


class Qrz:
    """QRZ.com: XML Data API for lookups (username/password → session key) and
    the logbook API for uploads (per-logbook API key)."""
    XML_URL = "https://xmldata.qrz.com/xml/current/"
    LOGBOOK_URL = "https://logbook.qrz.com/api"
    AGENT = f"ft950-remote {APP_VERSION}"   # one definition, in config.py

    def __init__(self, username: str = "", password: str = "", api_key: str = "") -> None:
        self.username, self.password, self.api_key = username or "", password or "", api_key or ""
        self._session = ""

    def configured(self) -> bool:
        return bool(self.username and self.password)

    def upload_ready(self) -> bool:
        return bool(self.api_key)

    def _logbook(self, params: dict) -> dict:
        """POST to the logbook API; the answer is URL-encoded (RESULT=OK&…)."""
        data = urllib.parse.urlencode({"KEY": self.api_key, **params}).encode()
        req = urllib.request.Request(self.LOGBOOK_URL, data=data, method="POST",
                                     headers={"User-Agent": self.AGENT})
        try:
            with urllib.request.urlopen(req, timeout=10, context=_ssl_ctx()) as r:
                raw = r.read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            raise LogError(f"QRZ-Logbuch: {e}") from e
        return {k: v[0] for k, v in urllib.parse.parse_qs(raw, keep_blank_values=True).items()}

    def log_qso(self, adif: str) -> dict:
        try:
            r = self._logbook({"ACTION": "INSERT", "ADIF": adif})
        except LogError as e:
            return {"ok": False, "message": str(e)}
        ok = r.get("RESULT") in ("OK", "REPLACE")
        return {"ok": ok, "message": f"QRZ-Log #{r.get('LOGID')}" if ok
                else "QRZ logbook: " + (r.get("REASON") or r.get("RESULT") or "rejected")}

    def test_logbook(self) -> dict:
        if not self.api_key:
            return {"ok": False, "message": "no logbook API key"}
        try:
            r = self._logbook({"ACTION": "STATUS"})
        except LogError as e:
            return {"ok": False, "message": str(e)}
        if r.get("RESULT") == "OK":
            return {"ok": True, "message": f"Logbuch {r.get('CALLSIGN', '')} · {r.get('COUNT', '?')} QSOs".strip()}
        return {"ok": False, "message": r.get("REASON") or r.get("RESULT") or "unknown response"}

    def _xml(self, params: dict) -> dict:
        url = self.XML_URL + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": self.AGENT})
        try:
            with urllib.request.urlopen(req, timeout=8, context=_ssl_ctx()) as r:
                raw = r.read().decode("utf-8", "replace")
            root = ET.fromstring(raw)
        except ET.ParseError as e:
            raise LogError("QRZ: unreadable XML response") from e
        except Exception as e:  # noqa: BLE001
            raise LogError(f"QRZ: {e}") from e
        out: dict[str, dict] = {}
        for sect in root:
            out.setdefault(sect.tag.rsplit("}", 1)[-1], {}).update(
                {c.tag.rsplit("}", 1)[-1]: (c.text or "").strip() for c in sect})
        return out

    def _ensure_session(self) -> str:
        if not self._session:
            d = self._xml({"username": self.username, "password": self.password, "agent": self.AGENT})
            key = d.get("Session", {}).get("Key")
            if not key:
                raise LogError("QRZ: " + (d.get("Session", {}).get("Error") or "Login fehlgeschlagen"))
            self._session = key
        return self._session

    def lookup(self, callsign: str) -> dict:
        call = callsign.strip().upper()
        for attempt in (1, 2):
            d = self._xml({"s": self._ensure_session(), "callsign": call})
            cs, err = d.get("Callsign", {}), d.get("Session", {}).get("Error", "")
            if cs:
                return {"callsign": cs.get("call", call),
                        "name": f"{cs.get('fname', '')} {cs.get('name', '')}".strip(),
                        "gridsquare": cs.get("grid", ""), "qth": cs.get("addr2", ""),
                        "country": cs.get("country", "")}
            if attempt == 1 and "session" in err.lower():
                self._session = ""          # expired: log in again once
                continue
            if "not found" in err.lower():
                return {}
            raise LogError("QRZ: " + (err or "not found"))
        return {}

    def test(self) -> dict:
        if not self.configured():
            return {"ok": False, "message": "username and password missing"}
        try:
            self._session = ""
            self._ensure_session()
            return {"ok": True, "message": "angemeldet"}
        except LogError as e:
            return {"ok": False, "message": str(e)}


# --- manager ----------------------------------------------------------------
class LogBook:
    def __init__(self, dxcc: Dxcc) -> None:
        rt = runtime.load()
        self.wavelog = Wavelog(rt.get("wavelog_url", ""), rt.get("wavelog_key", ""),
                               rt.get("wavelog_station_id", ""))
        self.qrz = Qrz(rt.get("qrz_username", ""), rt.get("qrz_password", ""),
                       rt.get("qrz_api_key", ""))
        self.auto_log = bool(rt.get("auto_log", True))      # default on (Sessions ▸ Auto-log)
        self.qrz_before_log = bool(rt.get("qrz_before_log", True))
        self.worked = WorkedIndex(dxcc)
        self.dxcc = dxcc
        self._sync_lock = threading.Lock()
        self.sync_error: Optional[str] = None
        try:
            self._recent = json.loads(RECENT_FILE.read_text())
        except (OSError, ValueError):
            self._recent = []

    # -- config (secrets are never sent back to the browser) --------------
    def config(self) -> dict:
        return {"wavelog_url": self.wavelog.url, "wavelog_station_id": self.wavelog.station_id,
                "wavelog_key_set": bool(self.wavelog.key),
                "qrz_username": self.qrz.username, "qrz_password_set": bool(self.qrz.password),
                "qrz_api_key_set": bool(self.qrz.api_key), "qrz_upload_ready": self.qrz.upload_ready(),
                "auto_log": self.auto_log, "qrz_before_log": self.qrz_before_log,
                "wavelog_ready": self.wavelog.configured() and bool(self.wavelog.station_id),
                "qrz_ready": self.qrz.configured()}

    def configure(self, cfg: dict) -> dict:
        upd = {k: v for k, v in cfg.items() if k in CONFIG_KEYS and v is not None}
        for k in SECRETS:                   # empty secret field = keep the stored one
            if k in upd and upd[k] == "":
                del upd[k]
        if upd.get("wavelog_url") and "://" not in upd["wavelog_url"]:
            upd["wavelog_url"] = "http://" + upd["wavelog_url"]   # "192.168.x.y:8086" is the usual entry
        station_changed = ("wavelog_station_id" in upd and str(upd["wavelog_station_id"])
                           != self.wavelog.station_id) or (
                           "wavelog_url" in upd and upd["wavelog_url"].rstrip("/") != self.wavelog.url)
        if "wavelog_url" in upd:
            self.wavelog.url = upd["wavelog_url"].rstrip("/")
        if "wavelog_key" in upd:
            self.wavelog.key = upd["wavelog_key"]
        if "wavelog_station_id" in upd:
            self.wavelog.station_id = str(upd["wavelog_station_id"])
        if "qrz_username" in upd:
            self.qrz.username, self.qrz._session = upd["qrz_username"], ""   # noqa: SLF001
        if "qrz_password" in upd:
            self.qrz.password, self.qrz._session = upd["qrz_password"], ""   # noqa: SLF001
        if "qrz_api_key" in upd:
            self.qrz.api_key = upd["qrz_api_key"]
        if "auto_log" in upd:
            self.auto_log = bool(upd["auto_log"])
        if "qrz_before_log" in upd:
            self.qrz_before_log = bool(upd["qrz_before_log"])
        runtime.save(upd)
        if station_changed:                 # a different log: rebuild the index
            self.worked.reset()
            self.worked.save()
        return self.config()

    # -- worked-before --------------------------------------------------------
    def sync(self, full: bool = False) -> dict:
        """Download QSOs from Wavelog into the worked index (incremental)."""
        with self._sync_lock:
            if full:
                self.worked.reset()
            try:
                recs, last = self.wavelog.fetch_adif(self.worked.last_id)
            except LogError as e:
                self.sync_error = str(e)
                raise
            for r in recs:
                band = (r.get("BAND") or "").lower() or band_for_hz(
                    int(float(r.get("FREQ") or 0) * 1e6))
                self.worked.add(r.get("CALL", ""), band)
            self.worked.last_id = last
            self.worked.synced_at = time.time()
            self.worked.save()
            self.sync_error = None
            log.info("worked index: +%d QSOs (last id %d)", len(recs), last)
            return {"new": len(recs), **self.worked.summary()}

    def status(self) -> dict:
        return {**self.config(), "worked": self.worked.summary(), "sync_error": self.sync_error}

    # -- lookup / logging --------------------------------------------------------
    def lookup(self, callsign: str, band: str = "") -> dict:
        call = callsign.strip().upper()
        ent = self.dxcc.lookup(call)
        res = {"callsign": call, "name": "", "gridsquare": "", "qth": "", "country": ent or "",
               "dxcc": ent or "", "sources": [], "worked": self.worked.status(call, band, ent)}
        if self.qrz.configured():
            q = self.qrz.lookup(call)
            for k in ("name", "gridsquare", "qth", "country"):
                if q.get(k):
                    res[k] = q[k]
            if q:
                res["sources"].append("qrz")
        return res

    def recent(self) -> list:
        return self._recent[:RECENT_MAX]

    def delete_recent(self, ts: str) -> list:
        self._recent = [e for e in self._recent if e.get("ts") != ts]
        self._save_recent()
        return self.recent()

    def _save_recent(self) -> None:
        tmp = RECENT_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._recent[:RECENT_MAX], indent=1))
        os.replace(tmp, RECENT_FILE)

    def log_qso(self, q: dict, *, station_callsign: str, my_grid: str,
                automatic: bool = False, to: tuple = ("wavelog", "qrz")) -> dict:
        """Log one QSO: QRZ lookup first (fills gaps), then the targets in
        ``to`` (Wavelog and/or the QRZ.com logbook), then the local recent
        list and the worked index. A target left out is None in the entry."""
        call = (q.get("call") or "").strip().upper()
        if not call:
            raise LogError("callsign missing")
        now = datetime.now(timezone.utc)
        freq = q.get("freq_hz")
        qso = {
            "call": call, "qso_date": q.get("qso_date") or now.strftime("%Y%m%d"),
            "time_on": q.get("time_on") or now.strftime("%H%M%S"),
            "time_off": q.get("time_off") or now.strftime("%H%M%S"),
            "freq_mhz": f"{freq / 1e6:.6f}" if freq else "",
            "band": band_for_hz(freq), "mode": (q.get("mode") or "FT8").upper(),
            "rst_sent": q.get("rst_sent") or "", "rst_rcvd": q.get("rst_rcvd") or "",
            "name": (q.get("name") or "").strip(), "gridsquare": (q.get("gridsquare") or "").strip(),
            "qth": (q.get("qth") or "").strip(), "country": (q.get("country") or "").strip(),
            "tx_pwr": q.get("tx_pwr"), "comment": (q.get("comment") or "").strip(),
            "station_callsign": station_callsign, "my_grid": my_grid,
        }
        lookup_note = ""
        if self.qrz_before_log and self.qrz.configured():
            try:
                info = self.qrz.lookup(call)
                for k in ("name", "gridsquare", "qth", "country"):
                    if not qso[k] and info.get(k):
                        qso[k] = info[k]
            except LogError as e:          # a failed lookup must not lose the QSO
                lookup_note = str(e)
        if not qso["country"]:
            qso["country"] = self.dxcc.lookup(call) or ""
        adif = build_adif(qso)
        result = {"ok": None, "message": "not sent to Wavelog"}
        if "wavelog" in to:
            result = {"ok": False, "message": "no Wavelog configured (Settings ▸ Log)"}
            if self.wavelog.configured() and self.wavelog.station_id:
                result = self.wavelog.log_qso(adif, power_w=qso["tx_pwr"])
        qrz = self.qrz.log_qso(adif) if "qrz" in to and self.qrz.upload_ready() else None
        entry = {"ts": now.isoformat(timespec="seconds"), **{k: qso[k] for k in (
            "call", "band", "freq_mhz", "mode", "rst_sent", "rst_rcvd", "name",
            "gridsquare", "qth", "country")}, "wavelog": result["ok"],
            "message": result["message"], "qrz": qrz["ok"] if qrz else None,
            "qrz_message": qrz["message"] if qrz else "",
            "auto": automatic, "lookup_error": lookup_note}
        self._recent.insert(0, entry)
        self._save_recent()
        if result["ok"] or (qrz and qrz["ok"]):         # worked, wherever it went
            self.worked.add(call, qso["band"])
            self.worked.save()
        return {**result, "qrz": qrz, "entry": entry}
