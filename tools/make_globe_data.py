"""Land data for the DIGI globe (frontend/globe.html), made once from
Natural Earth 110m land (world-atlas@2.0.2, land-110m.json, public domain):

  python3 tools/make_globe_data.py land-110m.json frontend/vendor/globe/land.json

Output (compact, ~60 KB):
  "coast": rings of [lon, lat] * 10 as flat int lists (0.1 degree)
  "dots":  land points on an even grid over the sphere (about DOT_DEG apart),
           flat [lat, lon] * 10 - the hologram's dot matrix
"""
import json
import math
import sys

DOT_DEG = 1.6


def decode(topo):
    sx, sy = topo["transform"]["scale"]
    tx, ty = topo["transform"]["translate"]
    arcs = []
    for arc in topo["arcs"]:
        x = y = 0
        pts = []
        for dx, dy in arc:
            x += dx; y += dy
            pts.append((x * sx + tx, y * sy + ty))
        arcs.append(pts)

    def ring(idx):
        out = []
        for i in idx:
            a = arcs[i] if i >= 0 else arcs[~i][::-1]
            out.extend(a if not out else a[1:])
        return out

    polys = []
    for g in topo["objects"]["land"]["geometries"]:
        coords = g["arcs"] if g["type"] == "MultiPolygon" else [g["arcs"]]
        for poly in coords:
            polys.append([ring(r) for r in poly])
    return polys


def inside(lon, lat, rings, bbox):
    if not (bbox[0] <= lon <= bbox[2] and bbox[1] <= lat <= bbox[3]):
        return False
    hit = False
    for r in rings:                       # even-odd over outer ring and holes
        n = len(r)
        j = n - 1
        for i in range(n):
            xi, yi = r[i]; xj, yj = r[j]
            if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
                hit = not hit
            j = i
    return hit


def main(src, dst):
    polys = decode(json.load(open(src)))
    boxes = []
    for p in polys:
        xs = [x for x, _ in p[0]]; ys = [y for _, y in p[0]]
        boxes.append((min(xs), min(ys), max(xs), max(ys)))
    coast = []
    for p in polys:
        for r in p:
            flat = []
            for x, y in r:
                flat += [round(x * 10), round(y * 10)]
            coast.append(flat)
    dots = []
    lat = -88.0
    while lat <= 88.0:
        step = DOT_DEG / max(0.05, math.cos(math.radians(lat)))
        n = max(1, int(360 / step))
        for k in range(n):
            lon = -180 + (k + 0.5) * 360 / n
            if any(inside(lon, lat, p, b) for p, b in zip(polys, boxes)):
                dots += [round(lat * 10), round(lon * 10)]
        lat += DOT_DEG
    json.dump({"source": "Natural Earth 110m land via world-atlas@2.0.2 (public domain)",
               "coast": coast, "dots": dots}, open(dst, "w"), separators=(",", ":"))
    print(f"{len(coast)} coast rings, {len(dots) // 2} land dots -> {dst}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
