"""Render the web UI in headless Chromium and report JS errors + a screenshot.

Needs chromium-headless-shell running with remote debugging, e.g.:
  chromium-headless-shell --no-sandbox --disable-gpu --ignore-certificate-errors \\
      --remote-debugging-address=127.0.0.1 --remote-debugging-port=9333 about:blank &
  backend/.venv/bin/python -I tools/ui_check.py https://192.168.201.156:8444/ shot.png
Run it from another machine when timing decodes: a browser on the Pi slows jt9.
"""
from __future__ import annotations

import argparse
import base64
import json
import time
import urllib.request

from websockets.sync.client import connect

PROBE = """(() => {
  const txt = (id) => document.getElementById(id)?.textContent ?? null;
  const cls = (id) => document.getElementById(id)?.className ?? null;
  return JSON.stringify({
    cat: cls('stat-cat'), audio: cls('stat-audio'), callsign: txt('callsign'),
    logo: !document.getElementById('brand-logo')?.hidden,
    freq: txt('freq'), rx_rows: document.querySelectorAll('#rx-body tr').length,
    level: txt('lvl-val'), depth: document.querySelector('#digi-depth .sg.on')?.textContent ?? null, ntp: cls('l-ntp'),
    screws: document.querySelectorAll('.panel .screw').length,
  });
})()"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("out", help="PNG screenshot path")
    ap.add_argument("--wait", type=float, default=12)
    ap.add_argument("--size", default="1500x1100")
    ap.add_argument("--port", type=int, default=9333)
    a = ap.parse_args()
    w, h = (int(x) for x in a.size.split("x"))
    tab = json.load(urllib.request.urlopen(f"http://127.0.0.1:{a.port}/json"))[0]
    errors: list[str] = []
    n = 0

    def note(m: dict) -> None:
        meth, p = m.get("method", ""), m.get("params", {})
        if meth == "Runtime.exceptionThrown":
            d = p["exceptionDetails"]
            errors.append("EXC " + d.get("exception", {}).get("description", d.get("text", ""))[:300])
        elif meth == "Runtime.consoleAPICalled" and p["type"] in ("error", "warning"):
            errors.append("CON " + " ".join(str(x.get("value", x.get("description", ""))) for x in p["args"])[:300])
        elif meth == "Log.entryAdded" and p["entry"]["level"] in ("error", "warning"):
            errors.append("LOG " + p["entry"]["text"][:300])

    with connect(tab["webSocketDebuggerUrl"], max_size=50_000_000) as ws:
        def call(method: str, **params):
            nonlocal n
            n += 1
            ws.send(json.dumps({"id": n, "method": method, "params": params}))
            while True:
                m = json.loads(ws.recv(timeout=60))
                if m.get("id") == n:
                    return m.get("result", {})
                note(m)

        for dom in ("Runtime", "Log", "Page"):
            call(f"{dom}.enable")
        call("Emulation.setDeviceMetricsOverride", width=w, height=h,
             deviceScaleFactor=1, mobile=w < 600)
        call("Page.navigate", url=a.url)
        end = time.time() + a.wait
        while time.time() < end:
            try:
                note(json.loads(ws.recv(timeout=0.5)))
            except TimeoutError:
                pass
        shot = call("Page.captureScreenshot", format="png", captureBeyondViewport=True)
        with open(a.out, "wb") as f:
            f.write(base64.b64decode(shot["data"]))
        state = call("Runtime.evaluate", expression=PROBE, returnByValue=True)
        if "exceptionDetails" in state:
            errors.append("PROBE " + state["exceptionDetails"].get("text", "failed"))
        print("DOM:", state["result"].get("value"))
    print(f"Errors/warnings: {len(errors)}")
    for e in errors[:20]:
        print("  ", e)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
