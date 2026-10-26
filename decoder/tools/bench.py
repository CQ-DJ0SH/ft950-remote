"""Decode every WAV in wav/ with one jt9 binary at one depth (full band 200-3000 Hz,
one process, as the reference measurement). Prints CPU time and writes the decode
lines to out/<label>-d<depth>.txt for an exact comparison between binaries.

  python3 bench.py <jt9 binary> <label> <depth> [repeats] [-- extra jt9 args]

Wall time is reported too: a multithreaded decoder spends more CPU than wall.
"""
import os, pathlib, resource, statistics as st, subprocess, sys, time
argv = sys.argv[1:]
extra = argv[argv.index("--") + 1:] if "--" in argv else []
argv = argv[:argv.index("--")] if "--" in argv else argv
jt9, label, depth = argv[0], argv[1], argv[2]
reps = int(argv[3]) if len(argv) > 3 else 1
here = pathlib.Path(os.environ.get("BENCH_DIR", ".")).resolve()   # holds wav/, gets work/ and out/
wavs = sorted((here / "wav").glob("*.wav"))
work = here / "work" / label; work.mkdir(parents=True, exist_ok=True)
(here / "out").mkdir(exist_ok=True)
cpu, wall, lines = [], [], []
for r in range(reps):
    for w in wavs:
        r0 = resource.getrusage(resource.RUSAGE_CHILDREN); t0 = time.perf_counter()
        out = subprocess.run([jt9, "-8", "-p", "15", "-d", depth, "-L", "200", "-H", "3000",
                              "-a", str(work), "-t", str(work), "-c", "DJ0SH", "-G", "JO62", *extra, str(w)],
                             cwd=work, capture_output=True, text=True).stdout
        wall.append(time.perf_counter() - t0)
        r1 = resource.getrusage(resource.RUSAGE_CHILDREN)
        cpu.append(r1.ru_utime - r0.ru_utime + r1.ru_stime - r0.ru_stime)
        if r == 0:
            lines += [f"{w.stem} {l}" for l in out.splitlines() if "~" in l]
(here / "out" / f"{label}-d{depth}.txt").write_text("\n".join(lines) + "\n")
print(f"{label:>10} d{depth}: {len(lines):4d} decodes | CPU per period mean {st.mean(cpu):.3f} s | "
      f"wall mean {st.mean(wall):.3f} s, max {max(wall):.3f} s")
