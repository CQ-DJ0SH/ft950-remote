"""Compare two decode lists by (period, message) - SNR/DT/frequency estimates differ between decoders."""
import sys
def load(p):
    s = set()
    for l in open(p):
        if "~" not in l: continue
        per = l.split()[0]; msg = " ".join(l.split("~", 1)[1].split())
        s.add((per, msg))
    return s
a, b = load(sys.argv[1]), load(sys.argv[2])
print(f"{sys.argv[2].split('/')[-1]:>22}: {len(b)} msgs vs {len(a)} | both {len(a & b)} | only new {len(b - a)} | lost {len(a - b)}")
