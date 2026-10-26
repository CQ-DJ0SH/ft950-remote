"""Shadow run: decode every live FT8 period with Debian's jt9 and with jt9-fast,
compare the decodes line for line, and log both CPU times. The live service is
not touched - this only reads the WAVs it leaves in /run/ft950/FT8.

- depth 2 on every period (the service's depth), with OSD_CHECK=1 on jt9-fast
  so every OSD call is also compared with the Fortran original;
- depth 3 on every 4th period (costs Debian's jt9 ~4 s);
- waits until a WAV is a few seconds old, so the live decode is done first.

Writes shadow/log.csv (one row per period and depth) and keeps the WAV and both
outputs of every mismatch in shadow/mismatch/. `python3 shadow.py report`
prints the totals.
"""
import csv, datetime as dt, os, pathlib, re, resource, shutil, subprocess, sys, time

# SHADOW_SRC: the service's FT8 slices; SHADOW_OUT: results; SHADOW_FAST: the build under test
SRC = pathlib.Path(os.environ.get("SHADOW_SRC", "/run/ft950/FT8"))
OUT = pathlib.Path(os.environ.get("SHADOW_OUT", "/opt/jt9-fast/shadow"))
LOG = OUT / "log.csv"
BIN = {"debian": "/usr/bin/jt9", "fast": os.environ.get("SHADOW_FAST", "/usr/local/bin/jt9-fast")}
ARGS = ["-8", "-p", "15", "-L", "200", "-H", "3000",
        "-c", os.environ.get("MY_CALL", "DJ0SH"), "-G", os.environ.get("MY_GRID", "JO62")]
SETTLE_S = 6          # a WAV must be this old: the live decode (≈1–2 s) is over
WAV_RE = re.compile(r"^\d{6}_\d{6}\.wav$")


def run(binary: str, wav: pathlib.Path, depth: int, work: pathlib.Path, check: bool):
    work.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, OSD_CHECK="1") if check else None
    r0 = resource.getrusage(resource.RUSAGE_CHILDREN)
    p = subprocess.run([binary, *ARGS, "-d", str(depth), "-a", str(work), "-t", str(work), str(wav)],
                       cwd=work, capture_output=True, text=True, env=env, timeout=60)
    r1 = resource.getrusage(resource.RUSAGE_CHILDREN)
    cpu = (r1.ru_utime - r0.ru_utime) + (r1.ru_stime - r0.ru_stime)
    lines = [l for l in p.stdout.splitlines() if "~" in l]
    m = re.search(r"OSD_CHECK: (\d+) calls, (\d+) mismatches", p.stderr)
    osd = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
    return lines, cpu, osd


def compare(wav: pathlib.Path, depth: int, writer, fh) -> None:
    tmp = OUT / "work"
    a, cpu_a, _ = run(BIN["debian"], wav, depth, tmp / "debian", False)
    b, cpu_b, (calls, bad) = run(BIN["fast"], wav, depth, tmp / "fast", depth == 2)
    same = a == b
    writer.writerow([wav.stem, depth, len(a), len(b), int(same), f"{cpu_a:.3f}", f"{cpu_b:.3f}", calls, bad])
    fh.flush()
    if not same or bad:
        d = OUT / "mismatch" / f"{wav.stem}-d{depth}"
        d.mkdir(parents=True, exist_ok=True)
        shutil.copy2(wav, d / wav.name)
        (d / "debian.txt").write_text("\n".join(a) + "\n")
        (d / "fast.txt").write_text("\n".join(b) + "\n")


def watch() -> None:
    os.nice(19)
    OUT.mkdir(exist_ok=True)
    new = not LOG.exists()
    fh = LOG.open("a", newline="")
    w = csv.writer(fh)
    if new:
        w.writerow(["period", "depth", "n_debian", "n_fast", "same", "cpu_debian", "cpu_fast",
                    "osd_calls", "osd_mismatch"])
    done = {r.split(",")[0] for r in LOG.read_text().splitlines()[1:]}
    n = 0
    while True:
        for wav in sorted(SRC.glob("*.wav")):
            if wav.stem in done or not WAV_RE.match(wav.name):
                continue
            if time.time() - wav.stat().st_mtime < SETTLE_S:
                continue
            local = OUT / "work" / wav.name          # the service prunes its slices
            local.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(wav, local)
            except FileNotFoundError:
                continue
            done.add(wav.stem)
            compare(local, 2, w, fh)
            if n % 4 == 0:
                compare(local, 3, w, fh)
            local.unlink(missing_ok=True)
            n += 1
        time.sleep(2)


def report() -> None:
    rows = list(csv.DictReader(LOG.open()))
    for depth in ("2", "3"):
        r = [x for x in rows if x["depth"] == depth]
        if not r:
            continue
        diff = [x["period"] for x in r if x["same"] != "1"]
        ca = sum(float(x["cpu_debian"]) for x in r) / len(r)
        cb = sum(float(x["cpu_fast"]) for x in r) / len(r)
        calls = sum(int(x["osd_calls"]) for x in r)
        bad = sum(int(x["osd_mismatch"]) for x in r)
        print(f"depth {depth}: {len(r)} periods ({r[0]['period']} … {r[-1]['period']} UTC), "
              f"decodes {sum(int(x['n_debian']) for x in r)} vs {sum(int(x['n_fast']) for x in r)}, "
              f"differing periods {len(diff)}{' ' + str(diff[:5]) if diff else ''} | "
              f"CPU {ca:.2f} s → {cb:.2f} s ({ca / cb if cb else 0:.2f}×)"
              + (f" | OSD calls checked {calls}, mismatches {bad}" if calls else ""))


if __name__ == "__main__":
    report() if sys.argv[1:] == ["report"] else watch()
