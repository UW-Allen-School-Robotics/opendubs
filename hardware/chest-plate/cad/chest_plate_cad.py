"""OpenDubs chest plate: parametric CAD (CadQuery). Design log: ../chest-plate.md.

    ~/.venvs/cad/bin/python cad/chest_plate_cad.py --logo <husky_trace.dxf> [--light <3118-0808-0002.step>] --out cad/out

`--logo` is the raster-to-vector trace of the logo (layers C1 white backing, C2 purple, C3 gold, C4 eye),
in pixels. `--light` is goBILDA's STEP model of the light: optional, and used only as a reference body in
the assembly and in the interference check. The dimensions the parts need from it are copied into `P`
and `LIGHT_COVER` below.

Writes, in --out:
  <part>.step + <part>.stl   one per part, in its print orientation (z up, bed at z = 0)
  parts.json                 volume, mass, material, quantity and placement of each part
  chest_plate_assembly.step  named, coloured components in the chest frame, with hardware and references
  chest_cutout.step          the volume the red chest piece must give up (pocket, insert holes, cable hole)
  face_layers.dxf            the face's 2D layers in mm, for the later laser-cut acrylic version
  logo_check.png             the 2D layers, the light footprint and the magnets, with thin features marked
  assembly/*.stl + colours.json   every component already in place, for render_check.py

Chest frame, in mm: X to the right as you face the robot's chest, Y up, Z out of the chest towards the
viewer. The origin is the centre of the 75 x 75 mm flat area, on the chest piece's front face (z = 0).
The badge's light box sits in a pocket in the chest piece, with its rim flush at z = 0. The face sits
on that rim and stands proud of the chest by the face's thickness.
"""
import argparse
import json
import math
import os

import cadquery as cq
import ezdxf
import numpy as np
from shapely import affinity
from shapely.geometry import MultiPolygon, Point, Polygon, box as sbox
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

P = dict(
    # step 0: envelope
    env=75.0,                       # flat chest area, square (team)
    # step 4: badge = rounded square; face over a white light box in a chest pocket
    face_w=75.0, face_r=8.0,        # face covers the box rim and overlaps the chest by (face_w - box_w) / 2
    base_t=2.0,                     # black base layer of the face (z 0 .. base_t)
    relief_t=0.8,                   # purple linework relief on top (filament change at z = base_t)
    gear_sink=1.0,                  # gold gear inlay sinks this far into the base; its top is flush with the relief
    logo_h=66.0,                    # the husky's larger bounding-box dimension
    logo_dx=0.0, logo_dy=0.0,       # logo offset on the badge
    # near ear (step 2): a glow window like the far ear's. The ear triangle in TRACE PIXELS (y up), read off the trace.
    ear_tri_px=[(111.0, 307.8), (113.5, 233.0), (159.0, 260.2)], ear_stroke=1.6,
    eye_d=2.2,                      # true-circle eye; its plug is relieved so it glows brighter than the almond
    # printability (FDM, 0.4 mm nozzle)
    purple_min=0.8,                 # thinnest purple line (printed relief)
    slot_min=0.9,                   # thinnest glow slot (the plug in it is slot - 2 * plug_clear)
    gap_min=0.8,                    # thinnest black gap between purple shapes
    widen=0.25,                     # how far thin features are grown on each side
    close_r=1.2,                    # gaps narrower than 2 x close_r in the outline are white cut lines: they glow
    island_max=12.0,                # mm^2: opaque islands floating in a glow window up to this size become glow
    sliver_area=0.15,               # mm^2: glow specks below this are dropped
    # step 3: diffuser (transparent PLA)
    dif_t=1.6,                      # diffuser plate, behind the face
    plug_clear=0.1,                 # per side, plug in its window
    mask=True, mask_t=1.2, mask_margin=3.0,   # clear "centre mask" glued behind the diffuser over the light
    # step 3: light box (white PLA)
    box_w=73.0, box_r=7.0, wall=2.0, floor_t=3.0,
    box_gap=15.0,                   # light's lens top -> diffuser back
    box_clear=0.3,                  # diffuser in the box, per side
    boss_d=9.0, mag_xy=30.5,        # magnet bosses in the box corners, centres at (+-mag_xy, +-mag_xy)
    mag_d=6.0, mag_t=2.0, mag_pocket_d=6.2, mag_face_depth=1.6,   # 6 x 2 mm disc magnets, one in the face, one in the box
    light_xy=None,                  # light centre; None = the centroid of the glowing area, rounded to 0.5 mm
    # the light (goBILDA 3118-0808-0002), read from its STEP model; light frame: PCB back z = 0, lens towards +z
    light_w=40.0, light_h=42.5, light_back=-8.0, light_ear_top=3.5, light_lens_top=9.8,
    light_hole_d=4.0, light_holes=16.0,           # 4 x dia 4.0 on a 32 mm square
    cover_clear=0.3,                # box floor opening around the light's cover, per side
    # fasteners: M4 socket head cap screws (ISO 4762: head 7.0 x 4.0) from the front, into heat-set inserts in the chest
    screw_len=20.0, screw_head_d=7.0, screw_head_h=4.0, screw_clear_d=4.5,
    insert_hole_d=5.6, insert_len=8.1, insert_hole_depth=9.0,     # M4 heat-set insert (typical 5.6 hole, 8.1 long: check yours)
    pocket_clear=0.3,               # chest pocket around the box and the light, per side
    cable_d=14.0, cable_len=12.0,   # cable hole behind the light's JST ports (vendor: reachable through a 14 mm hole)
)

# The light's front cover, just above its mounting ears (light frame, z = 3.6), from the vendor STEP model.
# The four corner notches clear the M4 screw heads (dia 7.0) at (+-16, +-16).
LIGHT_COVER = [
    (-20.0, 13.31), (-19.82, 13.79), (-19.04, 14.03), (-17.0, 12.29), (-13.43, 13.22), (-13.05, 13.69),
    (-12.25, 16.71), (-12.52, 17.39), (-13.96, 18.88), (-14.05, 19.39), (-13.5, 20.01), (-11.21, 21.21),
    (10.72, 21.25), (11.57, 21.11), (13.8, 19.81), (14.03, 19.03), (12.5, 17.38), (12.3, 16.8), (13.31, 13.26),
    (16.88, 12.31), (17.41, 12.54), (19.07, 14.04), (19.84, 13.77), (19.98, 13.47), (20.0, -13.31),
    (19.82, -13.79), (19.04, -14.03), (17.0, -12.29), (13.43, -13.22), (13.05, -13.69), (12.25, -16.71),
    (12.52, -17.39), (13.96, -18.88), (14.05, -19.39), (13.5, -20.01), (11.21, -21.21), (-10.72, -21.25),
    (-11.57, -21.11), (-13.8, -19.81), (-14.03, -19.03), (-12.5, -17.38), (-12.3, -16.8), (-13.31, -13.26),
    (-16.88, -12.31), (-17.41, -12.54), (-19.07, -14.04), (-19.84, -13.77), (-19.98, -13.47)]

PLA = 1.24e-3                       # g/mm^3
MAT = {"face": "PLA black, then purple from z = base_t", "gear": "PLA gold", "diffuser": "PLA transparent",
       "centre_mask": "PLA transparent", "box": "PLA white"}
RGBA = {"face_base": (0.08, 0.08, 0.09, 1), "face_relief": (0.18, 0.06, 0.38, 1), "gear": (0.8, 0.66, 0.4, 1),
        "diffuser": (0.75, 0.9, 1.0, 0.55), "centre_mask": (0.75, 0.9, 1.0, 0.55), "box": (0.95, 0.95, 0.95, 1),
        "screw": (0.25, 0.25, 0.27, 1), "magnet": (0.7, 0.7, 0.74, 1), "insert": (0.8, 0.65, 0.3, 1),
        "ref_light": (0.2, 0.2, 0.22, 1), "ref_chest": (0.6, 0.12, 0.12, 0.45)}


# ---------------------------------------------------------------- 2D: the logo, in mm, in the chest frame

def _rsquare(w, r):
    return sbox(-w / 2 + r, -w / 2 + r, w / 2 - r, w / 2 - r).buffer(r, quad_segs=16)


def _opening(g, r):
    return g.buffer(-r, join_style="mitre").buffer(r, join_style="mitre")


def _thin(g, w):
    """The parts of `g` narrower than `w` (what a morphological opening at w/2 removes)."""
    return g.difference(_opening(g, w / 2 - 1e-3)).buffer(0)


def _clean(g, min_area=1e-3):
    parts = [q for q in getattr(g, "geoms", [g]) if q.geom_type == "Polygon" and q.area > min_area]
    return unary_union(parts) if parts else Polygon()


def logo_regions(path, p=P):
    """Glow windows, purple linework, gold gear and the eye, in mm. Returns a dict of shapely geometries."""
    doc = ezdxf.readfile(path)
    raw = {}
    for e in doc.modelspace().query("LWPOLYLINE"):
        raw.setdefault(e.dxf.layer[:2], []).append(Polygon([tuple(q[:2]) for q in e.get_points("xy")]).buffer(0))
    allb = np.array([g.bounds for v in raw.values() for g in v])
    lo, hi = allb[:, :2].min(0), allb[:, 2:].max(0)
    s = p["logo_h"] / max(hi - lo)
    c = (lo + hi) / 2

    def mm(g):
        return affinity.affine_transform(g, [s, 0, 0, s, p["logo_dx"] - c[0] * s, p["logo_dy"] - c[1] * s])

    # Even-odd nesting of the purple polylines: depth 0 = the outer shapes.
    purp = raw["C2"]
    depth = [sum(1 for j, q in enumerate(purp) if j != i and q.contains(g.representative_point())) for i, g in enumerate(purp)]
    filled = unary_union([g for g, d in zip(purp, depth) if d == 0])
    evenodd = Polygon()
    for g in purp:
        evenodd = evenodd.symmetric_difference(g)
    gold = Polygon()
    for g in raw["C3"]:
        gold = gold.symmetric_difference(g)
    eye_c = unary_union(raw["C4"]).centroid

    filled, evenodd, gold = mm(filled), mm(evenodd), mm(gold)
    eye_c = mm(eye_c)
    purple = evenodd
    # Glow = the white areas of the artwork: the holes in the purple shapes, plus the white cut lines that
    # reach the outside, closed off where they're narrower than 2 x close_r. The traced white backing (C1)
    # is NOT used: its drop-shadow overshoot would put a glowing band round the whole outline. The gear
    # keeps a black gap around it.
    rc = p["close_r"]
    sil = filled.buffer(rc, join_style="mitre").buffer(-rc, join_style="mitre")
    lit = sil.difference(purple).difference(gold.buffer(p["gap_min"]))
    # near ear: the ear's triangle, inset by the far ear's stroke width
    tri = mm(Polygon(p["ear_tri_px"]))
    ear_win = tri.buffer(-p["ear_stroke"], join_style="mitre")
    sliver = lit.intersection(tri).difference(ear_win)     # the artwork's thin white sliver: now purple frame
    purple = purple.union(sliver).difference(ear_win)
    lit = lit.difference(sliver).union(ear_win)

    # printability: widen thin glow slots (into purple), then thin purple lines (into glow)
    w = p["widen"]
    lit = lit.union(_thin(lit, p["slot_min"]).buffer(w).intersection(sil))
    purple = purple.difference(lit)
    purple = purple.union(_thin(purple, p["purple_min"]).buffer(w).intersection(sil).difference(gold))
    lit = lit.difference(purple)
    lit = _clean(_opening(lit.buffer(0), 0.05), p["sliver_area"])
    purple = _clean(purple.buffer(0))
    face = _rsquare(p["face_w"], p["face_r"])
    # Opaque islands (pieces of the face that a glow window cuts loose) can't print: small ones become glow.
    opaque = face.difference(lit)
    parts = sorted(getattr(opaque, "geoms", [opaque]), key=lambda g: -g.area)
    islands = [g for g in parts[1:] if g.area > 1e-3]
    big = [round(g.area, 1) for g in islands if g.area > p["island_max"]]
    if big:
        raise SystemExit(f"opaque islands too big to drop: {big} mm^2; bridge them in the artwork")
    if islands:
        isl = unary_union([Polygon(g.exterior) for g in islands])
        lit = lit.union(isl)
        purple = purple.difference(isl)
    islands = unary_union(islands) if islands else Polygon()
    eye = eye_c.buffer(p["eye_d"] / 2, quad_segs=24)
    return dict(lit=lit, purple=purple, gold=_clean(gold), eye=eye, filled=sil, islands=islands,
                ear_win=ear_win, face=face, scale=s)


def light_xy(regions, p=P):
    if p["light_xy"] is not None:
        return tuple(p["light_xy"])
    c = regions["lit"].centroid
    return (round(c.x * 2) / 2, round(c.y * 2) / 2)


def widths_report(regions, p=P):
    """Area left narrower than each limit after the fixes (mm^2): should be ~0 apart from corner tips."""
    return {"glow slots < %.1f" % p["slot_min"]: round(_thin(regions["lit"], p["slot_min"]).area, 2),
            "purple < %.1f" % p["purple_min"]: round(_thin(regions["purple"], p["purple_min"]).area, 2)}


# ---------------------------------------------------------------- 2D -> 3D

def _faces(geom):
    out = []
    for pg in getattr(geom, "geoms", [geom]):
        if pg.geom_type != "Polygon" or pg.area < 1e-3:
            continue
        pg = orient(pg.simplify(0.01), 1.0)
        outer = cq.Wire.makePolygon([cq.Vector(x, y, 0) for x, y in pg.exterior.coords[:-1]], close=True)
        inner = [cq.Wire.makePolygon([cq.Vector(x, y, 0) for x, y in r.coords[:-1]], close=True) for r in pg.interiors]
        out.append(cq.Face.makeFromWires(outer, inner))
    return out


def prism(geom, z0, h):
    """Extrude a shapely (multi)polygon from z0 by h. Returns one Solid or a Compound."""
    solids = [cq.Solid.extrudeLinear(f, cq.Vector(0, 0, h)).translate(cq.Vector(0, 0, z0)) for f in _faces(geom)]
    if not solids:
        raise ValueError("empty geometry")
    return solids[0] if len(solids) == 1 else cq.Compound.makeCompound(solids)


def _fuse(a, *bs):
    out = a
    for b in bs:
        out = out.fuse(b)
    return out.clean()


def _cyl(d, z0, h, xy=(0, 0)):
    return cq.Solid.makeCylinder(d / 2, h, cq.Vector(xy[0], xy[1], z0))


def mag_xys(p=P):
    m = p["mag_xy"]
    return [(sx * m, sy * m) for sx in (1, -1) for sy in (1, -1)]


# ---------------------------------------------------------------- stack heights (chest frame)

def heights(p=P):
    z = {}
    z["face_back"] = 0.0
    z["face_top"] = p["base_t"] + p["relief_t"]
    z["dif_back"] = -p["dif_t"]
    z["lens_top"] = z["dif_back"] - p["box_gap"]
    z["light0"] = z["lens_top"] - p["light_lens_top"]          # light frame origin (PCB back)
    z["ear_top"] = z["light0"] + p["light_ear_top"]            # = the box floor's back face
    z["light_back"] = z["light0"] + p["light_back"]
    z["floor_top"] = z["ear_top"] + p["floor_t"]
    return z


# ---------------------------------------------------------------- parts, built in place (chest frame)

def face_part(R, p=P):
    z = heights(p)
    sink_z = p["base_t"] - p["gear_sink"]
    face = R["face"]
    lower = prism(face.difference(R["lit"]), 0, sink_z)
    upper = prism(face.difference(R["lit"]).difference(R["gold"].buffer(0.1)), sink_z, p["gear_sink"])
    relief = prism(R["purple"], p["base_t"], p["relief_t"])
    base = _fuse(lower, upper)
    for xy in mag_xys(p):
        base = base.cut(_cyl(p["mag_pocket_d"], -0.01, p["mag_face_depth"] + 0.01, xy))
    return base, relief


def gear_part(R, p=P):
    return prism(R["gold"], p["base_t"] - p["gear_sink"], p["gear_sink"] + p["relief_t"])


def diffuser_part(R, lxy, p=P):
    plate2d = _rsquare(p["box_w"] - 2 * p["wall"], p["box_r"] - p["wall"]).buffer(-p["box_clear"])
    for xy in mag_xys(p):
        plate2d = plate2d.difference(Point(xy).buffer(p["boss_d"] / 2 + p["box_clear"]))
    plate = prism(plate2d, -p["dif_t"], p["dif_t"])
    plugs2d = R["lit"].buffer(-p["plug_clear"], join_style="mitre").difference(R["eye"])
    plugs = prism(_clean(plugs2d, 0.02), 0, p["base_t"])
    dif = _fuse(plate, plugs)
    mask = None
    if p["mask"]:
        m = p["mask_margin"]
        mask2d = sbox(-p["light_w"] / 2 - m, -p["light_h"] / 2 - m, p["light_w"] / 2 + m, p["light_h"] / 2 + m)
        mask2d = affinity.translate(mask2d.buffer(2.0).buffer(-2.0), *lxy).intersection(plate2d.buffer(-1.0))
        mask = prism(mask2d, -p["dif_t"] - p["mask_t"], p["mask_t"])
    return dif, mask, plate2d


def box_part(lxy, p=P):
    z = heights(p)
    outer = _rsquare(p["box_w"], p["box_r"])
    inner = outer.buffer(-p["wall"])
    cover = affinity.translate(Polygon(LIGHT_COVER).buffer(p["cover_clear"], join_style="mitre"), *lxy)
    holes = [Point(lxy[0] + sx * p["light_holes"], lxy[1] + sy * p["light_holes"]) for sx in (1, -1) for sy in (1, -1)]
    floor2d = outer.difference(cover).difference(unary_union([h.buffer(p["screw_clear_d"] / 2) for h in holes]))
    floor = prism(floor2d, z["ear_top"], p["floor_t"])
    walls = prism(outer.difference(inner), z["floor_top"], -z["floor_top"])
    bosses = prism(unary_union([Point(xy).buffer(p["boss_d"] / 2) for xy in mag_xys(p)]).intersection(outer),
                   z["floor_top"], -z["floor_top"])
    b = _fuse(floor, walls, bosses)
    for xy in mag_xys(p):        # box magnet sits 0.4 deep so the face's proud magnet locates in the pocket
        depth = p["mag_t"] + (p["mag_t"] - p["mag_face_depth"])
        b = b.cut(_cyl(p["mag_pocket_d"], -depth, depth + 0.01, xy))
    return b, holes


def hardware(lxy, holes, p=P):
    z = heights(p)
    hw = {}
    for i, h in enumerate(holes, 1):
        head = _cyl(p["screw_head_d"], z["floor_top"], p["screw_head_h"], (h.x, h.y))
        shank = _cyl(3.8, z["floor_top"] - p["screw_len"], p["screw_len"], (h.x, h.y))   # 3.8: clear of the 4.0 holes
        hw[f"screw_M4x{p['screw_len']:.0f}_{i}"] = (head.fuse(shank), "screw")
        ins = _cyl(p["insert_hole_d"], z["light_back"] - p["insert_len"], p["insert_len"], (h.x, h.y)).cut(
            _cyl(4.0, z["light_back"] - p["insert_len"] - 1, p["insert_len"] + 2, (h.x, h.y)))
        hw[f"insert_M4_{i}"] = (ins, "insert")
    depth_box = p["mag_t"] + (p["mag_t"] - p["mag_face_depth"])
    for i, xy in enumerate(mag_xys(p), 1):
        hw[f"magnet_face_{i}"] = (_cyl(p["mag_d"], p["mag_face_depth"] - p["mag_t"], p["mag_t"], xy), "magnet")
        hw[f"magnet_box_{i}"] = (_cyl(p["mag_d"], -depth_box, p["mag_t"], xy), "magnet")
    return hw


def chest_cutout(lxy, holes, p=P):
    """What the red chest piece must give up: the box pocket, the light pocket, insert holes, cable hole."""
    z = heights(p)
    c = p["pocket_clear"]
    pocket_bottom = z["ear_top"] - c                 # the box hangs on the light; it never bottoms out
    top = 5.0                                        # above the chest face, so it subtracts cleanly
    boxp = prism(_rsquare(p["box_w"] + 2 * c, p["box_r"] + c), pocket_bottom, top - pocket_bottom)
    lp2d = affinity.translate(sbox(-p["light_w"] / 2 - c, -p["light_h"] / 2 - c, p["light_w"] / 2 + c, p["light_h"] / 2 + c), *lxy)
    lightp = prism(lp2d, z["light_back"], pocket_bottom - z["light_back"] + 0.5)
    cut = _fuse(boxp, lightp)
    for h in holes:
        cut = cut.fuse(_cyl(p["insert_hole_d"], z["light_back"] - p["insert_hole_depth"], p["insert_hole_depth"] + 0.5, (h.x, h.y)))
    cut = cut.fuse(_cyl(p["cable_d"], z["light_back"] - p["cable_len"], p["cable_len"] + 0.5, lxy))
    return cut.clean()


def light_reference(path, lxy, p=P):
    """The vendor model, placed: (PCB, back case, front cover) for the interference check, all of it for show."""
    z = heights(p)
    sol = cq.importers.importStep(path).solids().vals()
    loc = cq.Vector(lxy[0], lxy[1], z["light0"])
    main = [s.translate(loc) for s in (sol[0], sol[71], sol[72])]
    return main, cq.Compound.makeCompound([s.translate(loc) for s in sol])


# ---------------------------------------------------------------- checks and outputs

def interference(comps, min_vol=0.05):
    names = list(comps)
    hits = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if a.startswith("ref_") and b.startswith("ref_"):
                continue
            sa, sb = comps[a][0], comps[b][0]
            ba, bb = sa.BoundingBox(), sb.BoundingBox()
            if (ba.xmin > bb.xmax or bb.xmin > ba.xmax or ba.ymin > bb.ymax or bb.ymin > ba.ymax
                    or ba.zmin > bb.zmax or bb.zmin > ba.zmax):
                continue
            v = sa.intersect(sb).Volume()
            if v > min_vol:
                hits.append((a, b, round(v, 2)))
    return hits


def logo_png(R, lxy, plate2d, path, p=P):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import PathPatch
    from matplotlib.path import Path as MPath

    def patch(ax, g, **kw):
        for pg in getattr(g, "geoms", [g]):
            if pg.geom_type != "Polygon" or pg.is_empty:
                continue
            verts, codes = [], []
            for ring in [pg.exterior, *pg.interiors]:
                cs = list(ring.coords)
                verts += cs
                codes += [MPath.MOVETO] + [MPath.LINETO] * (len(cs) - 2) + [MPath.CLOSEPOLY]
            ax.add_patch(PathPatch(MPath(verts, codes), **kw))

    fig, axs = plt.subplots(1, 2, figsize=(16, 8))
    for ax, title in zip(axs, ("as printed (cyan = glow)", "checks")):
        patch(ax, R["face"], fc=(0.08, 0.08, 0.09), ec="none")
        patch(ax, R["lit"], fc=(0.25, 0.8, 1.0), ec="none")
        patch(ax, R["purple"], fc=(0.25, 0.08, 0.5), ec="none")
        patch(ax, R["gold"], fc=(0.8, 0.66, 0.4), ec="none")
        patch(ax, R["eye"], fc="white", ec="none")
        ax.set_title(title)
        ax.set_xlim(-40, 40); ax.set_ylim(-40, 40); ax.set_aspect("equal")
    ax = axs[1]
    patch(ax, _thin(R["lit"], p["slot_min"]), fc="red", ec="none")
    patch(ax, _thin(R["purple"], p["purple_min"]), fc="orange", ec="none")
    cover = affinity.translate(Polygon(LIGHT_COVER), *lxy)
    patch(ax, cover, fc="none", ec="yellow", lw=1.5)
    patch(ax, plate2d, fc="none", ec="white", lw=0.8, ls="--")
    for xy in mag_xys(p):
        patch(ax, Point(xy).buffer(p["mag_pocket_d"] / 2), fc="none", ec="lime", lw=1.2)
    ax.plot([lxy[0]], [lxy[1]], "y+", ms=14)
    ax.text(-39, -38.5, "red/orange: thinner than slot_min / purple_min; yellow: light cover; green: magnets;"
            " dashed: diffuser", color="white", fontsize=8, backgroundcolor="black")
    plt.tight_layout()
    plt.savefig(path, dpi=90)
    plt.close(fig)


def face_dxf(R, path):
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = 4                      # mm
    msp = doc.modelspace()
    for layer, g in (("OUTLINE", R["face"]), ("GLOW_WINDOWS", R["lit"]), ("PURPLE", R["purple"]),
                     ("GOLD", R["gold"]), ("EYE", R["eye"])):
        doc.layers.add(layer)
        for pg in getattr(g, "geoms", [g]):
            if pg.geom_type != "Polygon":
                continue
            for ring in [pg.exterior, *pg.interiors]:
                msp.add_lwpolyline(list(ring.coords)[:-1], close=True, dxfattribs={"layer": layer})
    doc.saveas(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--logo", required=True)
    ap.add_argument("--light", default=None)
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "out"))
    ap.add_argument("--2d", dest="only2d", action="store_true", help="logo_check.png and face_layers.dxf only")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    p = P
    z = heights(p)

    R = logo_regions(a.logo, p)
    lxy = light_xy(R, p)
    if a.only2d:
        plate2d = _rsquare(p["box_w"] - 2 * p["wall"], p["box_r"] - p["wall"]).buffer(-p["box_clear"])
        logo_png(R, lxy, plate2d, os.path.join(a.out, "logo_check.png"), p)
        face_dxf(R, os.path.join(a.out, "face_layers.dxf"))
        n_isl = len(getattr(R["islands"], "geoms", [R["islands"]])) if not R["islands"].is_empty else 0
        print(f"light centre {lxy}; islands dropped {n_isl}; thin left {widths_report(R, p)}")
        return
    base, relief = face_part(R, p)
    face = base.fuse(relief).clean()
    gear = gear_part(R, p)
    dif, mask, plate2d = diffuser_part(R, lxy, p)
    box, holes = box_part(lxy, p)
    hw = hardware(lxy, holes, p)
    cut = chest_cutout(lxy, holes, p)

    parts = {"face": (face, 1), "gear": (gear, 1), "diffuser": (dif, 1), "box": (box, 1)}
    if mask is not None:
        parts["centre_mask"] = (mask, 1)
    report = {"params": {k: v for k, v in p.items()}, "light_xy": lxy, "heights": z, "parts": {}}
    for name, (shape, qty) in parts.items():
        bb = shape.BoundingBox()
        printed = shape.translate(cq.Vector(0, 0, -bb.zmin))     # every part is built with its print face down
        cq.exporters.export(cq.Workplane(obj=printed), os.path.join(a.out, f"{name}.step"))
        cq.exporters.export(cq.Workplane(obj=printed), os.path.join(a.out, f"{name}.stl"), tolerance=0.02, angularTolerance=0.1)
        vol = shape.Volume()
        report["parts"][name] = {"qty": qty, "material": MAT[name], "volume_mm3": round(vol, 1),
                                 "mass_g": round(vol * PLA, 1), "print_bbox": [round(bb.xlen, 2), round(bb.ylen, 2), round(bb.zlen, 2)],
                                 "placement": [[0, 0, round(bb.zmin, 3)], None]}
    cq.exporters.export(cq.Workplane(obj=cut), os.path.join(a.out, "chest_cutout.step"))
    face_dxf(R, os.path.join(a.out, "face_layers.dxf"))
    logo_png(R, lxy, plate2d, os.path.join(a.out, "logo_check.png"), p)

    # assembly: components in the chest frame
    comps = {"face_base": (base, "face_base"), "face_relief": (relief, "face_relief"), "gear": (gear, "gear"),
             "diffuser": (dif, "diffuser"), "box": (box, "box")}
    if mask is not None:
        comps["centre_mask"] = (mask, "centre_mask")
    comps.update(hw)
    chest = prism(_rsquare(95, 10), z["light_back"] - p["insert_hole_depth"] - 4, -(z["light_back"] - p["insert_hole_depth"] - 4)).cut(cut)
    comps["ref_chest_piece"] = (chest, "ref_chest")
    light_show = None
    if a.light:
        main_l, light_show = light_reference(a.light, lxy, p)
        for i, s in enumerate(main_l):
            comps[f"ref_light_{('pcb', 'back_case', 'cover')[i]}"] = (s, "ref_light")

    assy = cq.Assembly(name="opendubs_chest_plate")
    pos_dir = os.path.join(a.out, "assembly")
    os.makedirs(pos_dir, exist_ok=True)
    colours = {}
    for n, (s, kind) in comps.items():
        if n.startswith("ref_light") and light_show is not None:
            continue
        assy.add(s, name=n, color=cq.Color(*RGBA[kind]))
        cq.exporters.export(cq.Workplane(obj=s), os.path.join(pos_dir, f"{n}.stl"), tolerance=0.05, angularTolerance=0.15)
        colours[n] = RGBA[kind]
    if light_show is not None:
        assy.add(light_show, name="ref_light_goBILDA_3118-0808-0002", color=cq.Color(*RGBA["ref_light"]))
        cq.exporters.export(cq.Workplane(obj=light_show), os.path.join(pos_dir, "ref_light.stl"), tolerance=0.3, angularTolerance=0.8)   # coarse: MuJoCo caps STLs at 200k faces
        colours["ref_light"] = RGBA["ref_light"]
    assy.save(os.path.join(a.out, "chest_plate_assembly.step"))
    with open(os.path.join(pos_dir, "colours.json"), "w") as f:
        json.dump(colours, f)
    with open(os.path.join(a.out, "parts.json"), "w") as f:
        json.dump(report, f, indent=1)

    # ---- report
    print(f"logo scale {R['scale']:.4f} mm/px; light centre {lxy}; islands dropped: "
          f"{len(getattr(R['islands'], 'geoms', [R['islands']])) if not R['islands'].is_empty else 0}")
    print("heights:", {k: round(v, 2) for k, v in z.items()})
    for name, d in report["parts"].items():
        print(f"  {name:12s} {d['mass_g']:6.1f} g  print bbox {d['print_bbox']}")
    print("total printed mass %.1f g" % sum(d["mass_g"] for d in report["parts"].values()))
    print("thin features left (mm^2):", widths_report(R, p))
    lit_out = R["lit"].difference(plate2d.buffer(-0.5))
    print("glow area outside the diffuser (should be 0): %.2f mm^2" % lit_out.area)
    mags = unary_union([Point(xy).buffer(p["mag_pocket_d"] / 2 + 0.8) for xy in mag_xys(p)])
    print("magnets over glow / gear (should be 0): %.2f / %.2f mm^2"
          % (mags.intersection(R["lit"]).area, mags.intersection(R["gold"]).area))
    cut_bb = cut.BoundingBox()
    print(f"chest pocket: {cut_bb.xlen:.1f} x {cut_bb.ylen:.1f} mm, deepest z = {cut_bb.zmin:.1f} mm "
          f"(face stands {z['face_top']:.1f} mm proud)")
    hits = interference({k: v for k, v in comps.items()})
    # Intended: the dia 7.0 screw heads touch the light cover's corner notches, which goBILDA sized for them.
    ok = [h for h in hits if h[0].startswith("screw") and h[1] == "ref_light_cover" and h[2] < 0.5]
    bad = [h for h in hits if h not in ok]
    print("interference, intended (screw heads in the cover's notches):", ok)
    print("interference, UNINTENDED:", bad if bad else "none")


if __name__ == "__main__":
    main()
