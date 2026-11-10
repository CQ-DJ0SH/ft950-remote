"""Land data for the DIGI globe (frontend/globe.html), made once from
Natural Earth 110m land (world-atlas@2.0.2, land-110m.json, public domain):

  python3 tools/make_globe_data.py land-110m.json frontend/vendor/globe/land.json

Output (compact, ~60 KB):
  "coast": rings of [lon, lat] * 10 as flat int lists (0.1 degree)
  "dots":  land points on a geodesic grid (a subdivided icosahedron: a near
           even hexagonal pattern over the whole sphere, about DOT_DEG apart),
           flat [lat, lon] * 10 - the hologram's dot matrix. A grid along the
           parallels showed its rows as rings round the pole when the globe was
           seen from above, a Fibonacci lattice its spiral arms.
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


def unwrap(ring):
    """A ring that crosses the antimeridian jumps from +180 to -180: on the flat
    lon/lat plane that edge ran round the whole world, and every point at its
    latitude counted as land (a row of dots across the South Atlantic from
    Fiji, dots in the Norwegian Sea from Chukotka). Unrolled, the ring stays
    continuous; one that goes round a pole (Antarctica) is closed over it."""
    out, off = [ring[0]], 0.0
    for (x0, _), (x1, y1) in zip(ring, ring[1:]):
        if x1 - x0 > 180:
            off -= 360
        elif x1 - x0 < -180:
            off += 360
        out.append((x1 + off, y1))
    if abs(out[-1][0] - out[0][0]) > 180:                      # goes round a pole
        pole = -90.0 if sum(y for _, y in out) < 0 else 90.0
        out += [(out[-1][0], pole), (out[0][0], pole)]
    return out


def inside_any(lon, lat, rings, bbox):
    # unrolled rings may reach past +-180: the point is tried a turn either side
    return any(inside(x, lat, rings, bbox) for x in (lon, lon + 360, lon - 360))


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


def geodesic(spacing_deg):
    """Vertices of an icosahedron with every face split into f² triangles,
    pushed out to the sphere: points about spacing_deg apart (edge 63.4°/f)."""
    f = max(1, round(63.43 / spacing_deg))
    t = (1 + math.sqrt(5)) / 2
    v = [(-1, t, 0), (1, t, 0), (-1, -t, 0), (1, -t, 0), (0, -1, t), (0, 1, t),
         (0, -1, -t), (0, 1, -t), (t, 0, -1), (t, 0, 1), (-t, 0, -1), (-t, 0, 1)]
    faces = [(0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11), (1, 5, 9), (5, 11, 4),
             (11, 10, 2), (10, 7, 6), (7, 1, 8), (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8),
             (3, 8, 9), (4, 9, 5), (2, 4, 11), (6, 2, 10), (8, 6, 7), (9, 8, 1)]
    seen, out = set(), []
    for a, b, c in faces:
        A, B, C = v[a], v[b], v[c]
        for i in range(f + 1):
            for j in range(f + 1 - i):
                k = f - i - j
                x, y, z = ((i * A[n] + j * B[n] + k * C[n]) / f for n in range(3))
                r = math.sqrt(x * x + y * y + z * z)
                lat = math.degrees(math.asin(z / r))
                lon = math.degrees(math.atan2(y, x))
                key = (round(lat, 4), round(lon, 4) if abs(lat) < 89.999 else 0.0)
                if key not in seen:
                    seen.add(key)
                    out.append((lat, lon))
    return out


def main(src, dst):
    polys = decode(json.load(open(src)))
    unrolled = [[unwrap(r) for r in p] for p in polys]         # for the land test only
    boxes = []
    for p in unrolled:
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
    for lat, lon in geodesic(DOT_DEG):
        if any(inside_any(lon, lat, p, b) for p, b in zip(unrolled, boxes)):
            dots += [round(lat * 10), round(lon * 10)]
    json.dump({"source": "Natural Earth 110m land via world-atlas@2.0.2 (public domain)",
               "coast": coast, "dots": dots}, open(dst, "w"), separators=(",", ":"))
    print(f"{len(coast)} coast rings, {len(dots) // 2} land dots -> {dst}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
