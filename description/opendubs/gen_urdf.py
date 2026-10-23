#!/usr/bin/env python3
"""Generate opendubs.urdf from dimensions.yaml -- the TF tree robot_state_publisher serves.

dimensions.yaml is the one place a dimension lives; the MJCF and this URDF are both views of it.
Run after editing the ROS FRAMES section (or any geometry it reads):

    uv run --with pyyaml python3 description/opendubs/gen_urdf.py          # write opendubs.urdf
    uv run --with pyyaml python3 description/opendubs/gen_urdf.py --check  # fail if it is stale

Frames whose position is TODO_CAD (or TODO_MEASURE) are left OUT and listed in the URDF's header:
a consumer that needs one fails loudly instead of fusing against a guess.

Movable joints (zaxis, head_pan, head_tilt) need a joint_states source, or robot_state_publisher
publishes nothing below them. Their names match the MJCF.
"""
import argparse
import math
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
SRC = HERE / "dimensions.yaml"
OUT = HERE / "opendubs.urdf"
TODO = "TODO_CAD"


def m(mm):
    """mm -> metres, printed without float noise."""
    v = round(mm / 1000.0, 6)
    return f"{v:.6f}".rstrip("0").rstrip(".") if v else "0"


def rad(deg):
    v = round(math.radians(deg), 6)
    return f"{v:.6f}".rstrip("0").rstrip(".") if v else "0"


def xyz(v):
    return " ".join(m(c) for c in v)


def rpy(v):
    return " ".join(rad(c) for c in v)


def known(v):
    return isinstance(v, list) and len(v) == 3 and all(isinstance(c, (int, float)) for c in v)


class Urdf:
    def __init__(self):
        self.parts, self.missing, self.links = [], [], set()

    def link(self, name, visual=None):
        """visual: one visual's inner XML, or a list of them."""
        assert name not in self.links, f"duplicate link {name}"
        self.links.add(name)
        if visual is None:
            self.parts.append(f'  <link name="{name}"/>')
            return
        vis = "".join(f"    <visual>\n{v}\n    </visual>\n" for v in ([visual] if isinstance(visual, str) else visual))
        self.parts.append(f'  <link name="{name}">\n{vis}  </link>')

    def joint(self, name, kind, parent, child, origin=(0, 0, 0), rot=(0, 0, 0), axis=None, limit=None):
        body = [f'    <parent link="{parent}"/>', f'    <child link="{child}"/>',
                f'    <origin xyz="{xyz(origin)}" rpy="{rpy(rot)}"/>']
        if axis:
            body.append(f'    <axis xyz="{" ".join(str(a) for a in axis)}"/>')
        if limit:
            lo, hi = limit
            body.append(f'    <limit lower="{lo}" upper="{hi}" effort="0" velocity="0"/>')
        self.parts.append(f'  <joint name="{name}" type="{kind}">\n' + "\n".join(body) + "\n  </joint>")

    def fixed_sensor(self, parent, frame, pos_mm, rpy_deg, origin_mm, what):
        """A frame whose CAD position may still be TODO. origin_mm is the parent's CAD origin."""
        if not known(pos_mm):
            self.missing.append(f"{frame} ({what})")
            return
        self.link(frame)
        rel = [p - o for p, o in zip(pos_mm, origin_mm)]
        self.joint(f"{frame}_joint", "fixed", parent, frame, rel, rpy_deg or (0, 0, 0))


def box(span, color):
    """A box visual from a CAD span {x:[lo,hi], y:..., z:...}, in its link's frame."""
    c = [(span[a][0] + span[a][1]) / 2 for a in "xyz"]
    s = [span[a][1] - span[a][0] for a in "xyz"]
    return (f'      <origin xyz="{xyz(c)}" rpy="0 0 0"/>\n'
            f'      <geometry><box size="{xyz(s)}"/></geometry>\n'
            f'      <material name="{color}"/>')


def build(d):
    u = Urdf()
    bl = d["base_link"]
    base = bl["xyz_mm"]

    # base_link is the root (drive_bridge publishes odom -> base_link onto it). Everything in CAD
    # coordinates hangs off `datum`, so no number below is converted by hand.
    u.link("base_link")
    u.link("base_footprint")
    u.joint("base_footprint_joint", "fixed", "base_link", "base_footprint",
            (0, 0, bl["footprint_z_mm"] - base[2]))
    u.link("datum", box(d["meshes"]["chassis"]["span_mm"], "frame"))
    u.joint("datum_joint", "fixed", "base_link", "datum", [-c for c in base])

    w = d["meshes"]["wheels"]
    for name, (x, y) in w["centres_mm"].items():
        link = f"wheel_{name.lower()}"
        u.link(link, f'      <origin xyz="0 0 0" rpy="{rad(90)} 0 0"/>\n'
                     f'      <geometry><cylinder radius="{m(w["diameter_mm"] / 2)}" '
                     f'length="{m(w["width_mm"])}"/></geometry>\n'
                     f'      <material name="rubber"/>')
        u.joint(f"{link}_joint", "fixed", "datum", link, (x, y, w["centre_z_mm"]))

    for key, s in d["sensors"].items():
        if s.get("fitted"):
            u.fixed_sensor("datum", s["frame"], s["xyz_mm"], s.get("rpy_deg"), (0, 0, 0), key)

    # The lift platform's origin is the datum with the lift fully down (as in the MJCF).
    lift = d["lift"]
    u.link("lift_platform", box(d["meshes"]["lift_platform"]["span_mm"], "plate"))
    u.joint("zaxis", "prismatic", "datum", "lift_platform", axis=(0, 0, 1),
            limit=(0, m(lift["stroke_mm"])))

    # The arms' own description attaches its root (openarm_body_link0) here.
    mount = d["arms"]["mount"]
    u.link("arm_mount")
    u.joint("arm_mount_joint", "fixed", "lift_platform", "arm_mount",
            mount["xyz_mm"], mount.get("rpy_deg", (0, 0, 0)))

    ha = d["head_assembly"]
    fitted = ha["fitted"]
    if fitted != "galaxy_tablet":
        sys.exit(f"head_assembly.fitted = {fitted!r}: no such assembly")
    tablet_head(u, d, ha, ha[fitted])
    return u, fitted


def pan_tilt(u, h, pan_visual=None, tilt_visual=None):
    """The neck's two joints, from a `head:`-style geometry block; returns the pivot (CAD mm).
    Joint names match the MJCF (head_pan, head_tilt) whichever head rides on them."""
    pivot = h["pan_axis_xyz_mm"]
    assert h["tilt_axis_xyz_mm"] == pivot, "pan and tilt axes no longer meet; revisit the head joints"
    lo, hi = h["pan_range_deg"]
    u.link("head_pan_link", pan_visual)
    u.joint("head_pan", "revolute", "lift_platform", "head_pan_link", pivot,
            axis=h["pan_axis"], limit=(rad(lo), rad(hi)))
    # Positive tilt is DOWN, as in the MJCF: about +y, +x swings toward -z.
    t = h["tilt_travel_deg"]
    u.link("head_tilt_link", tilt_visual)
    u.joint("head_tilt", "revolute", "head_pan_link", "head_tilt_link",
            axis=h["tilt_axis"], limit=(rad(-t["up"]), rad(t["down"])))
    return pivot


def tablet_head(u, d, ha, asm):
    """The tablet on the neck: the two joints, box visuals from the meshes' extents, and the frames
    that are located. The meshes are exported in the head's own frame (head.export), which a pure
    translation puts on the robot, so every export number moves by the same offset."""
    h = d[asm["geometry"]]
    ex = h["export"]
    shift = ex["to_robot_mm"]
    pp = [p + o for p, o in zip(ex["pan_tilt_point_mm"], shift)]
    assert pp == h["pan_axis_xyz_mm"], (
        f"head.export puts the pan/tilt point at {pp}, not at the measured {h['pan_axis_xyz_mm']}")

    def rel_span(span):
        """An export extent, in the frame of the pan/tilt point."""
        return {a: [v - ex["pan_tilt_point_mm"][i] for v in span[a]] for i, a in enumerate("xyz")}

    ext = ex["extents_export_mm"]
    pivot = pan_tilt(u, h, box(rel_span(ext["neck"]), "frame"), box(rel_span(ext["face"]), "plate"))
    marks = {k: [v + o for v, o in zip(p, shift)] for k, p in ex["landmarks_export_mm"].items()}
    for frame, spec in (asm.get("frames") or {}).items():
        pos = marks[spec["at"]] if "at" in spec else spec.get("xyz_mm")
        u.fixed_sensor("head_tilt_link", frame, pos, spec.get("rpy_deg"), pivot, "galaxy_tablet")
        if spec.get("optical"):
            optical_frame(u, frame)

    # The Femto Bolt: a fixed attach frame on the face, then the module that rides on it. Its
    # frames are placed relative to the attach frame (both in robot CAD coordinates at rest).
    fm = asm.get("femto_mount")
    if fm:
        at = marks[fm["at"]] if "at" in fm else fm.get("xyz_mm")
        mod = (ha.get("modules") or {}).get(fm["module"], {})
        if known(at) and known(fm.get("rpy_deg")):
            assert fm["rpy_deg"] == [0, 0, 0], "the module's frames assume an unrotated mount frame"
            rel_mount = lambda span: {a: [v - at[i] for v in span[a]] for i, a in enumerate("xyz")}
            moved = lambda key: {a: [v + shift[i] for v in ext[key][a]] for i, a in enumerate("xyz")}
            vis = [box(rel_mount(moved(k)), c) for k, c in (("femto_mount", "frame"), ("femto_bolt", "rubber"))
                   if mod.get("fitted") and k in ext]
            u.link(fm["frame"], vis or None)
            u.joint(f"{fm['frame']}_joint", "fixed", fm["parent"], fm["frame"],
                    [p - o for p, o in zip(at, pivot)], fm["rpy_deg"])
        else:
            u.missing.append(f"{fm['frame']} (the Femto mount)")
        if mod.get("fitted") and fm["frame"] in u.links:
            for frame, spec in (mod.get("frames") or {}).items():
                if spec.get("urdf") is False:
                    continue
                pos = marks[spec["at"]] if "at" in spec else spec.get("xyz_mm")
                if known(pos) and known(spec.get("rpy_deg")):
                    u.link(frame)
                    u.joint(f"{frame}_joint", "fixed", fm["frame"], frame,
                            [p - o for p, o in zip(pos, at)], spec["rpy_deg"])
                else:
                    u.missing.append(f"{frame} ({fm['module']})")


def optical_frame(u, parent):
    """A camera's optical frame (z forward, x right, y down), as a child of its body frame
    (x forward). Missing whenever the body frame is."""
    name = parent.removesuffix("_link") + "_optical_frame"
    if parent not in u.links:
        u.missing.append(f"{name} (follows {parent})")
        return
    u.link(name)
    u.joint(f"{name}_joint", "fixed", parent, name, (0, 0, 0), (-90, 0, -90))


def validate(text):
    """One tree, rooted at base_link: no link with two parents, no joint naming a missing link."""
    root = ET.fromstring(text.split("\n", 1)[1])
    links = {l.get("name") for l in root.findall("link")}
    kids = [j.find("child").get("link") for j in root.findall("joint")]
    parents = {j.find("parent").get("link") for j in root.findall("joint")}
    assert len(kids) == len(set(kids)), "a link has two parents"
    assert set(kids) | parents <= links, "a joint names an undefined link"
    assert links - set(kids) == {"base_link"}, f"roots: {sorted(links - set(kids))}"


def render(u, fitted):
    head = ["<?xml version=\"1.0\"?>",
            "<!-- GENERATED by description/opendubs/gen_urdf.py from dimensions.yaml. Do not edit:",
            "     change dimensions.yaml and re-run the generator.",
            f"     Head assembly fitted: {fitted}.",
            "     base_link: centre of the wheel rectangle, axle height (workplan-thor-stack L.1).",
            "     Movable joints zaxis / head_pan / head_tilt need a joint_states source."]
    if u.missing:
        head.append("     LEFT OUT (no CAD or measured position in the repo yet):")
        head += [f"       - {x}" for x in u.missing]
    head[-1] += " -->"
    mats = ['  <material name="frame"><color rgba="0.55 0.57 0.6 1"/></material>',
            '  <material name="plate"><color rgba="0.75 0.75 0.78 1"/></material>',
            '  <material name="rubber"><color rgba="0.1 0.1 0.1 1"/></material>']
    return "\n".join(head + ['<robot name="opendubs">'] + mats + u.parts + ["</robot>", ""])


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="exit 1 if opendubs.urdf is stale")
    args = ap.parse_args()
    u, fitted = build(yaml.safe_load(SRC.read_text()))
    text = render(u, fitted)
    validate(text)
    if args.check:
        if not OUT.exists() or OUT.read_text() != text:
            sys.exit(f"{OUT.name} is stale: re-run gen_urdf.py")
        print(f"OK  {OUT.name}: {len(u.links)} links, {len(u.missing)} frames awaiting CAD")
        return
    OUT.write_text(text)
    print(f"wrote {OUT.name}: {len(u.links)} links; head = {fitted}")
    for x in u.missing:
        print(f"  awaiting CAD: {x}")


if __name__ == "__main__":
    main()
