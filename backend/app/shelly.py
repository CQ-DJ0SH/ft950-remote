"""Radio DC power via a Shelly relay (the title-bar power switch).

The FT-950 cannot be switched on or off over CAT, so its 13.8 V line goes
through a Shelly. Both API generations are supported and told apart by
``GET /shelly`` (Gen2+ answers with ``"gen": 2|3|4``):

* Gen1 (Shelly 1, 1PM, 2.5 …): ``/relay/<ch>?turn=on|off`` → ``{"ison": …}``,
  optional HTTP basic auth.
* Gen2+ (Plus/Pro/Gen3): ``/rpc/Switch.Set?id=<ch>&on=true|false`` and
  ``/rpc/Switch.GetStatus?id=<ch>`` → ``{"output": …}``, optional digest auth
  (user is always ``admin``).

Like TM-V71 Remote's GPIO switch: after "off" the line stays off for at least
MIN_OFF_S — power re-applied a moment after it was cut can leave a rig's
controller hung.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Optional

MIN_OFF_S = 3.0
TIMEOUT_S = 3.0


class ShellyError(RuntimeError):
    pass


class ShellySwitch:
    def __init__(self) -> None:
        self.host = ""
        self.channel = 0
        self.user = ""
        self.password = ""
        self.gen: Optional[int] = None
        self._off_at: Optional[float] = None

    def configure(self, host: str = "", channel: int = 0, user: str = "", password: str = "") -> None:
        host = (host or "").strip().removeprefix("http://").removeprefix("https://").rstrip("/")
        if (host, channel, user, password) != (self.host, self.channel, self.user, self.password):
            self.gen = None                       # re-detect after a change
        self.host, self.channel, self.user, self.password = host, int(channel or 0), user or "", password or ""

    @property
    def configured(self) -> bool:
        return bool(self.host)

    # -- HTTP ----------------------------------------------------------------
    def _get(self, path: str) -> dict:
        url = f"http://{self.host}{path}"
        handlers = []
        if self.password:
            pm = urllib.request.HTTPPasswordMgrWithDefaultRealm()
            pm.add_password(None, f"http://{self.host}/", self.user or "admin", self.password)
            handlers = [urllib.request.HTTPDigestAuthHandler(pm), urllib.request.HTTPBasicAuthHandler(pm)]
        opener = urllib.request.build_opener(*handlers)
        try:
            with opener.open(url, timeout=TIMEOUT_S) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise ShellyError("Shelly: wrong user or password") from e
            raise ShellyError(f"Shelly answered {e.code}") from e
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise ShellyError(f"Shelly {self.host} unreachable: {getattr(e, 'reason', e)}") from e

    def _detect(self) -> int:
        if self.gen is None:
            info = self._get("/shelly")
            self.gen = int(info.get("gen") or 1)
        return self.gen

    # -- API -------------------------------------------------------------------
    def get(self) -> bool:
        if self._detect() >= 2:
            return bool(self._get(f"/rpc/Switch.GetStatus?id={self.channel}").get("output"))
        return bool(self._get(f"/relay/{self.channel}").get("ison"))

    def set(self, on: bool) -> bool:
        if self._detect() >= 2:
            self._get(f"/rpc/Switch.Set?id={self.channel}&on={'true' if on else 'false'}")
        else:
            self._get(f"/relay/{self.channel}?turn={'on' if on else 'off'}")
        if not on:
            self._off_at = time.monotonic()
        state = self.get()
        if state != on:
            raise ShellyError(f"Shelly did not switch {'on' if on else 'off'}")
        return state

    def off_remaining(self) -> float:
        if self._off_at is None:
            return 0.0
        return max(0.0, MIN_OFF_S - (time.monotonic() - self._off_at))

    def status(self) -> dict:
        st = {"available": False, "configured": self.configured, "on": None,
              "host": self.host, "channel": self.channel, "gen": self.gen, "error": None}
        if not self.configured:
            st["error"] = "no Shelly configured (Settings ▸ General)"
            return st
        try:
            st["on"] = self.get()
            st["available"], st["gen"] = True, self.gen
        except ShellyError as e:
            st["error"] = str(e)
        return st
