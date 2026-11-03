#!/usr/bin/env python3
"""Line a SLAM map up with a building floor plan, and pull the plan's room numbers out as places.

A floor plan gives what the LIDAR cannot: room numbers, the whole floor rather than what has been
driven, and one fixed frame per floor. This tool reads a vector floor-plan PDF (walls as strokes,
room numbers as text, a scale bar) and a saved SLAM map, and finds the rotation and offset that lay
the map's walls onto the plan's. It writes, for floor_frames.py on the robot:

  <frame>.plan.yaml   the floor frame and its rooms: {label: [x, y]} in metres
  site.yaml           which saved map SLAM localizes on, its start pose, and where it sits in the
                      floor frame (map_in_floor: [x, y, yaw])
  <map>.overlay.png   the plan in grey and the map's walls in red, to check the fit by eye

Run it where the PDF is (a laptop); copy the outputs to the robot's /var/lib/opendubs/maps/. Floor
plans and maps are site data: they stay on the robot and off the repo.

  uv run --with pymupdf --with numpy --with scipy python3 floorplan.py fit PLAN.pdf MAP.yaml \\
      --frame bldg_f2 --seed-room 204 --start X Y YAW -o OUT_DIR

`plan` writes the same floor as an open plan file for other tools (ENCORE's encore.plan/1, its repo's
docs/venue-format.md): the walls as two-point polylines (the strokes fit reads, clipped to the same
drawing area), the rooms, the scale, and where it came from. It is NOT the robot's <frame>.plan.yaml,
and it refuses to overwrite a file that is not already an encore.plan/1 file:

  uv run --with pymupdf --with pyyaml python3 floorplan.py plan PLAN.pdf --frame bldg_f2 -o OUT.plan.yaml

--start is where the mapping drive began, in the map frame (the drive starts in --seed-room): it
seeds the search and becomes SLAM's start pose when localizing. The search covers every heading
and +/-3 m around the seed room's label, then refines.

THE FLOOR FRAME is the plan's page in metres: x right, y up, origin the page's top-left corner, so
rooms have negative y. It is fixed by the plan, not by any map, and a CAD export will want its own.

Assumes the plan's scale bar is a horizontal stroke just under a "Feet" label and spans 100 feet
(the layout of UW MAPS "Basic Architectural Floorplans"); --bar-feet and --drawing-bottom change that.
"""
import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys

ROOM = re.compile(r"^[0-9G][0-9A-Z]{2,4}(-\d+)?$")    # 269, 200K, 2ST1, 250-1, G00A


def map_in_floor(theta, tx, ty, sx, sy):
    """Pose (x, y, yaw) of the map frame in the floor frame, given the fit p_floor = R(theta)(p_map - s) + t."""
    c, s = math.cos(theta), math.sin(theta)
    return (tx - (c * sx - s * sy), ty - (s * sx + c * sy), math.atan2(s, c))


def is_room(label):
    return bool(ROOM.match(label)) and any(ch.isdigit() for ch in label)


def strokes(items):
    """The page's strokes as segments ((x, y), (x, y)) in page points: lines, curves as their chord
    (p1 to p4), rectangles as their four sides. Both `fit` (sampled) and `plan` (clipped) use these."""
    out = []
    for it in items:
        if it[0] == "l":
            out.append(((it[1].x, it[1].y), (it[2].x, it[2].y)))
        elif it[0] == "c":
            out.append(((it[1].x, it[1].y), (it[4].x, it[4].y)))
        elif it[0] == "re":
            r = it[1]; c = [r.tl, r.tr, r.br, r.bl]
            out += [((c[k].x, c[k].y), (c[(k + 1) % 4].x, c[(k + 1) % 4].y)) for k in range(4)]
    return out


def drawing_area(width, bottom):
    """The part of the page that is the drawing, in page points (x0, x1, y0, y1), y down: inside the
    margins, below the header, above the scale bar and title block."""
    return 10, width - 10, 40, bottom - 10


def read_page(pdf, bar_feet, bottom):
    """(m_per_pt, page width, strokes, rooms) from a plan PDF's first page."""
    import pymupdf
    page = pymupdf.open(pdf)[0]
    lines = [it for d in page.get_drawings() for it in d["items"]]
    # The scale bar: the longest horizontal stroke just under its "Feet" label (not the title
    # block's border, which is longer and lower).
    feet = [w for w in page.get_text("words") if w[4].lower() == "feet" and w[1] > bottom]
    if not feet:
        raise SystemExit("no 'Feet' scale-bar label below the drawing (see --drawing-bottom)")
    fy = feet[0][3]
    bars = [(it[1], it[2]) for it in lines if it[0] == "l" and abs(it[1].y - it[2].y) < 0.5
            and fy <= it[1].y <= fy + 25]
    if not bars:
        raise SystemExit("no scale-bar stroke under the 'Feet' label")
    a, b = max(bars, key=lambda ab: abs(ab[1].x - ab[0].x))
    m_per_pt = bar_feet * 0.3048 / abs(b.x - a.x)
    x0, x1, y0, y1 = drawing_area(page.rect.width, bottom)
    rooms = {}
    for wx0, wy0, wx1, wy1, word, *_ in page.get_text("words"):
        if y0 < wy0 < y1 and is_room(word):
            rooms.setdefault(word, [round((wx0 + wx1) / 2 * m_per_pt, 3), round(-(wy0 + wy1) / 2 * m_per_pt, 3)])
    return m_per_pt, page.rect.width, strokes(lines), rooms


def read_plan(pdf, bar_feet, bottom):
    """For `fit`: the plan's walls as points every 2.5 cm along its strokes, inside the drawing, in metres."""
    import numpy as np
    m_per_pt, width, segs, rooms = read_page(pdf, bar_feet, bottom)
    x0, x1, y0, y1 = drawing_area(width, bottom)
    pts = []
    for (px, py), (qx, qy) in segs:
        n = max(2, int(math.hypot(qx - px, qy - py) * m_per_pt / 0.025))
        for t in np.linspace(0, 1, n):
            pts.append((px + (qx - px) * t, py + (qy - py) * t))
    P = np.array([(x, y) for x, y in pts if y0 < y < y1 and x0 < x < x1])
    return m_per_pt, P[:, 0] * m_per_pt, -P[:, 1] * m_per_pt, rooms


def clip(p, q, box):
    """The part of segment p-q inside box (x0, x1, y0, y1), or None (Liang-Barsky)."""
    (px, py), (qx, qy) = p, q
    x0, x1, y0, y1 = box
    dx, dy = qx - px, qy - py
    t0, t1 = 0.0, 1.0
    for pp, qq in ((-dx, px - x0), (dx, x1 - px), (-dy, py - y0), (dy, y1 - py)):
        if pp == 0:
            if qq < 0:
                return None
            continue
        r = qq / pp
        if pp < 0:
            t0 = max(t0, r)
        else:
            t1 = min(t1, r)
        if t0 > t1:
            return None
    return (px + t0 * dx, py + t0 * dy), (px + t1 * dx, py + t1 * dy)


PLAN_FORMAT = "encore.plan/1"


def plan_doc(segs, m_per_pt, rooms, box, frame, source, converter, bar_feet):
    """An encore.plan/1 document: each stroke clipped to the drawing area, as a two-point polyline in
    metres in the floor frame (x right, y up: page y is negated). Pure: no PDF library needed."""
    walls = []
    for p, q in segs:
        got = clip(p, q, box)
        if got and got[0] != got[1]:
            (ax, ay), (bx, by) = got
            walls.append([[round(ax * m_per_pt, 4), round(-ay * m_per_pt, 4)],
                          [round(bx * m_per_pt, 4), round(-by * m_per_pt, 4)]])
    return {"format": PLAN_FORMAT, "frame": frame, "units": "m",
            "axes": "x right, y up; origin the source page's top-left corner, so rooms have negative y",
            "source": source, "converter": converter,
            "scale": {"m_per_unit": round(float(m_per_pt), 6), "from": f"the {bar_feet:g}-foot scale bar under 'Feet'"},
            "walls": walls, "rooms": rooms}


def revision():
    """This file's git short hash, '+dirty' if it has uncommitted changes; 'unknown' outside a checkout."""
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        rev = subprocess.run(["git", "-C", here, "log", "-1", "--format=%h", "--", "floorplan.py"],
                             capture_output=True, text=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", here, "diff", "--quiet", "HEAD", "--", "floorplan.py"]).returncode
    except OSError:
        return "unknown"
    return (rev + ("+dirty" if dirty else "")) if rev else "unknown"


def plan(args):
    if os.path.exists(args.out):
        try:
            with open(args.out) as f:
                old = f.read()
            ok = old.lstrip().startswith("{") and json.loads(old).get("format") == PLAN_FORMAT \
                or re.search(r"^format:\s*['\"]?" + re.escape(PLAN_FORMAT), old, re.M)
        except (OSError, ValueError):
            ok = False
        if not ok:
            raise SystemExit(f"{args.out} exists and is not an {PLAN_FORMAT} file (the robot's own "
                             f"<frame>.plan.yaml?); not overwritten")
    m_per_pt, width, segs, rooms = read_page(args.plan, args.bar_feet, args.drawing_bottom)
    raw = open(args.plan, "rb").read()
    source = {"file": os.path.basename(args.plan), "sha256": hashlib.sha256(raw).hexdigest(), "kind": "pdf", "page": 0}
    converter = {"name": "opendubs software/ros/floorplan.py plan", "revision": revision(),
                 "command": " ".join(["python3 floorplan.py"] + [os.path.basename(a) if a in (args.plan, args.out) else a
                                                                 for a in sys.argv[1:]])}   # file names, not local paths
    doc = plan_doc(segs, m_per_pt, rooms, drawing_area(width, args.drawing_bottom), args.frame, source,
                   converter, args.bar_feet)
    with open(args.out, "w") as f:
        if args.out.endswith(".json"):
            json.dump(doc, f)
        else:
            import yaml
            yaml.safe_dump(doc, f, sort_keys=False, default_flow_style=None, width=200)
    print(f"wrote {args.out}: {len(doc['walls'])} walls, {len(rooms)} rooms, {m_per_pt:.6f} m per point")


def read_map(yaml_path):
    import numpy as np
    import yaml
    y = yaml.safe_load(open(yaml_path))
    pgm = open(os.path.join(os.path.dirname(os.path.abspath(yaml_path)), y["image"]), "rb").read()
    hdr = pgm.split(b"\n", 3); w, h = map(int, hdr[1].split())
    a = np.frombuffer(hdr[3], np.uint8).reshape(h, w)
    r, c = np.nonzero(a == 0)
    ox, oy = y["origin"][:2]
    return ox + (c + 0.5) * y["resolution"], oy + (h - 1 - r + 0.5) * y["resolution"]


def fit(args):
    import numpy as np
    import yaml
    from scipy.ndimage import distance_transform_edt
    RES = 0.05
    m_per_pt, X, Y, rooms = read_plan(args.plan, args.bar_feet, args.drawing_bottom)
    if args.seed_room not in rooms:
        raise SystemExit(f"room {args.seed_room!r} is not on this plan")
    x0, y0 = X.min() - 2, Y.min() - 2
    W, H = int((X.max() + 2 - x0) / RES) + 1, int((Y.max() + 2 - y0) / RES) + 1
    occ = np.zeros((H, W), bool)
    occ[((Y - y0) / RES).astype(int), ((X - x0) / RES).astype(int)] = True
    dt = np.minimum(distance_transform_edt(~occ) * RES, 0.5)
    sx, sy, syaw = args.start
    mx, my = read_map(args.map)
    mx, my = mx - sx, my - sy

    def place(th, tx, ty, xs, ys):
        c, s = math.cos(th), math.sin(th)
        return c * xs - s * ys + tx, s * xs + c * ys + ty

    def score(th, tx, ty, xs=mx, ys=my):
        px, py = place(th, tx, ty, xs, ys)
        i, j = ((py - y0) / RES).astype(int), ((px - x0) / RES).astype(int)
        ok = (i >= 0) & (i < H) & (j >= 0) & (j < W)
        return np.where(ok, dt[np.clip(i, 0, H - 1), np.clip(j, 0, W - 1)], 0.5).mean()
    rx, ry = rooms[args.seed_room]
    sub = np.random.default_rng(0).choice(len(mx), min(800, len(mx)), replace=False)
    best = (9.0, 0.0, 0.0, 0.0)
    for th in np.radians(np.arange(0, 360, 2)):
        for tx in np.arange(rx - 3, rx + 3.01, 0.25):
            for ty in np.arange(ry - 3, ry + 3.01, 0.25):
                v = score(th, tx, ty, mx[sub], my[sub])
                if v < best[0]:
                    best = (v, th, tx, ty)
    s, th, tx, ty = best
    for da, dl in ((0.5, 0.05), (0.1, 0.02)):
        for _ in range(3):
            for dth in np.radians(np.arange(-4, 5) * da):
                for dx in np.arange(-4, 5) * dl:
                    for dy in np.arange(-4, 5) * dl:
                        v = score(th + dth, tx + dx, ty + dy)
                        if v < s:
                            s, th, tx, ty = v, th + dth, tx + dx, ty + dy
    px, py = place(th, tx, ty, mx, my)
    i, j = ((py - y0) / RES).astype(int), ((px - x0) / RES).astype(int)
    ok = (i >= 0) & (i < H) & (j >= 0) & (j < W)
    d = dt[i[ok], j[ok]]
    within10, within25 = float((d < 0.10).mean()), float((d < 0.25).mean())
    s, th, tx, ty = float(s), float(th), float(tx), float(ty)
    pose = map_in_floor(th, tx, ty, sx, sy)
    map_name = args.map.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    out = args.out.rstrip("/")
    yaml.safe_dump({"frame": args.frame, "source": args.plan.rsplit("/", 1)[-1], "m_per_pt": round(m_per_pt, 6),
                    "origin": "the plan page's top-left corner; x right, y up (rooms have negative y)",
                    "rooms": dict(sorted(rooms.items()))}, open(f"{out}/{args.frame}.plan.yaml", "w"), sort_keys=False)
    yaml.safe_dump({"map": map_name, "start_pose": [round(sx, 3), round(sy, 3), round(syaw, 4)],
                    "floor": {"frame": args.frame, "plan": f"{args.frame}.plan.yaml",
                              "map_in_floor": [round(v, 4) for v in pose],
                              "fit": {"seed_room": args.seed_room, "mean_wall_distance_m": round(float(s), 4),
                                      "walls_within_10cm": round(within10, 3), "walls_within_25cm": round(within25, 3)}}},
                   open(f"{out}/site.yaml", "w"), sort_keys=False)
    # overlay, cropped round the map
    pad = 4
    xs_, ys_ = px[ok], py[ok]
    j0, j1 = int((xs_.min() - pad - x0) / RES), int((xs_.max() + pad - x0) / RES)
    i0, i1 = int((ys_.min() - pad - y0) / RES), int((ys_.max() + pad - y0) / RES)
    j0, i0 = max(j0, 0), max(i0, 0)
    img = np.full((i1 - i0, j1 - j0, 3), 255, np.uint8)
    img[occ[i0:i1, j0:j1]] = (150, 150, 150)
    ii, jj = ((ys_ - y0) / RES).astype(int) - i0, ((xs_ - x0) / RES).astype(int) - j0
    keep = (ii >= 0) & (ii < img.shape[0]) & (jj >= 0) & (jj < img.shape[1])
    img[ii[keep], jj[keep]] = (220, 30, 30)
    import pymupdf
    img = np.ascontiguousarray(np.repeat(np.repeat(img[::-1], 2, 0), 2, 1))
    pymupdf.Pixmap(pymupdf.csRGB, img.shape[1], img.shape[0], img.tobytes(), False).save(f"{out}/{map_name}.overlay.png")
    print(f"{len(rooms)} rooms; scale {m_per_pt * 1000:.2f} mm/pt")
    print(f"map {map_name} in {args.frame}: x={pose[0]:.2f} y={pose[1]:.2f} yaw={math.degrees(pose[2]):.1f} deg; "
          f"mean wall distance {s * 100:.1f} cm, {within10 * 100:.0f}% within 10 cm, {within25 * 100:.0f}% within 25 cm")
    print(f"wrote {out}/{args.frame}.plan.yaml, {out}/site.yaml, {out}/{map_name}.overlay.png")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sp = ap.add_subparsers(dest="cmd", required=True)
    f = sp.add_parser("fit", help="fit a saved SLAM map to a floor-plan PDF")
    f.add_argument("plan"); f.add_argument("map", help="the map's .yaml (map_server format)")
    f.add_argument("--frame", required=True, help="the floor frame's name, e.g. bldg_f2")
    f.add_argument("--seed-room", required=True, help="the room the mapping drive started in")
    f.add_argument("--start", type=float, nargs=3, required=True, metavar=("X", "Y", "YAW"),
                   help="the drive's start pose in the map frame (m, m, rad)")
    f.add_argument("-o", "--out", default=".")
    f.add_argument("--bar-feet", type=float, default=100.0)
    f.add_argument("--drawing-bottom", type=float, default=650.0, help="page y (pt) below which the scale bar's label sits")
    p = sp.add_parser("plan", help=f"write a floor-plan PDF as an open plan file ({PLAN_FORMAT})")
    p.add_argument("plan"); p.add_argument("--frame", required=True, help="the floor frame's name, e.g. bldg_f2")
    p.add_argument("-o", "--out", required=True, help="the plan file to write (.yaml or .json)")
    p.add_argument("--bar-feet", type=float, default=100.0)
    p.add_argument("--drawing-bottom", type=float, default=650.0, help="page y (pt) below which the scale bar's label sits")
    args = ap.parse_args()
    (plan if args.cmd == "plan" else fit)(args)


if __name__ == "__main__":
    main()
