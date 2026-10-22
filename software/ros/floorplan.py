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

--start is where the mapping drive began, in the map frame (the drive starts in --seed-room): it
seeds the search and becomes SLAM's start pose when localizing. The search covers every heading
and +/-3 m around the seed room's label, then refines.

THE FLOOR FRAME is the plan's page in metres: x right, y up, origin the page's top-left corner, so
rooms have negative y. It is fixed by the plan, not by any map, and a CAD export will want its own.

Assumes the plan's scale bar is a horizontal stroke just under a "Feet" label and spans 100 feet
(the layout of UW MAPS "Basic Architectural Floorplans"); --bar-feet and --drawing-bottom change that.
"""
import argparse
import math
import os
import re

ROOM = re.compile(r"^[0-9G][0-9A-Z]{2,4}(-\d+)?$")    # 269, 200K, 2ST1, 250-1, G00A


def map_in_floor(theta, tx, ty, sx, sy):
    """Pose (x, y, yaw) of the map frame in the floor frame, given the fit p_floor = R(theta)(p_map - s) + t."""
    c, s = math.cos(theta), math.sin(theta)
    return (tx - (c * sx - s * sy), ty - (s * sx + c * sy), math.atan2(s, c))


def is_room(label):
    return bool(ROOM.match(label)) and any(ch.isdigit() for ch in label)


def read_plan(pdf, bar_feet, bottom):
    import numpy as np
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
    pts = []

    def seg(p, q):
        n = max(2, int(math.hypot(q.x - p.x, q.y - p.y) * m_per_pt / 0.025))
        for t in np.linspace(0, 1, n):
            pts.append((p.x + (q.x - p.x) * t, p.y + (q.y - p.y) * t))
    for it in lines:
        if it[0] == "l":
            seg(it[1], it[2])
        elif it[0] == "c":
            seg(it[1], it[4])
        elif it[0] == "re":
            r = it[1]; c = [r.tl, r.tr, r.br, r.bl]
            for k in range(4):
                seg(c[k], c[(k + 1) % 4])
    w, h = page.rect.width, page.rect.height
    P = np.array([(x, y) for x, y in pts if 40 < y < bottom - 10 and 10 < x < w - 10])
    rooms = {}
    for x0, y0, x1, y1, word, *_ in page.get_text("words"):
        if 40 < y0 < bottom - 10 and is_room(word):
            rooms.setdefault(word, [round((x0 + x1) / 2 * m_per_pt, 3), round(-(y0 + y1) / 2 * m_per_pt, 3)])
    return m_per_pt, P[:, 0] * m_per_pt, -P[:, 1] * m_per_pt, rooms


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
    args = ap.parse_args()
    fit(args)


if __name__ == "__main__":
    main()
