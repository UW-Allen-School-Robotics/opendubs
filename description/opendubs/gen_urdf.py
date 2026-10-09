#!/usr/bin/env python3
"""Generate opendubs.urdf from dimensions.yaml -- the TF tree robot_state_publisher serves.

dimensions.yaml is the one place a dimension lives; the MJCF and this URDF are both views of it.
Run after editing the ROS FRAMES section (or any geometry it reads):

    uv run --with pyyaml python3 description/opendubs/gen_urdf.py          # write opendubs.urdf
    uv run --with pyyaml python3 description/opendubs/gen_urdf.py --check  # fail if it is stale

Frames whose position is TODO_CAD are left OUT and listed in the URDF's header: a consumer that
needs one fails loudly instead of fusing against a guess.

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
        assert name not in self.links, f"duplicate link {name}"
        self.links.add(name)
        if visual is None:
            self.parts.append(f'  <link name="{name}"/>')
        else:
            self.parts.append(f'  <link name="{name}">\n    <visual>\n{visual}\n    </visual>\n  </link>')

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


def placed(extent, pivot_x):
    """Head part extents are exported at pan=180 (head.export_pose); yaw them forward about the pan
    axis, which is on the centreline: x -> 2*pivot - x, y -> -y."""
    return {"x": sorted(2 * pivot_x - v for v in extent["x"]),
            "y": sorted(-v for v in extent["y"]),
            "z": list(extent["z"])}


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
    if fitted == "tab5_pantilt":
        tab5_head(u, d, ha[fitted])
    elif fitted == "galaxy_tablet":
        g = ha[fitted]
        if known(g.get("mount_xyz_mm")):
            u.link("head_link")
            u.joint("head_mount_joint", "fixed", "lift_platform", "head_link", g["mount_xyz_mm"])
        else:
            u.missing.append("head_link (galaxy_tablet: no CAD yet; no head frames emitted)")
    else:
        sys.exit(f"head_assembly.fitted = {fitted!r}: no such assembly")
    return u, fitted


def tab5_head(u, d, asm):
    h = d[asm["geometry"]]
    pivot = h["pan_axis_xyz_mm"]
    assert h["tilt_axis_xyz_mm"] == pivot, "pan and tilt axes no longer meet; revisit the head joints"
    assert h["export_pose"] == "yawed_180", "head parts are no longer exported at pan=180"
    ext = {k: placed(v, pivot[0]) for k, v in h["extents_cad_mm"].items()}

    def rel_span(span):
        return {a: [v - pivot[i] for v in span[a]] for i, a in enumerate("xyz")}

    lo, hi = h["pan_range_deg"]
    u.link("head_pan_link")
    u.joint("head_pan", "revolute", "lift_platform", "head_pan_link", pivot,
            axis=h["pan_axis"], limit=(rad(lo), rad(hi)))
    # Positive tilt is DOWN, as in the MJCF: about +y, +x swings toward -z.
    t = h["tilt_travel_deg"]
    u.link("head_tilt_link", box(rel_span(ext["pt_tilt_body"]), "frame"))
    u.joint("head_tilt", "revolute", "head_pan_link", "head_tilt_link",
            axis=h["tilt_axis"], limit=(rad(-t["up"]), rad(t["down"])))

    def face(span):
        """Centre of a part's forward (+x) face, in CAD coordinates at pan = tilt = 0."""
        return [span["x"][1], sum(span["y"]) / 2, sum(span["z"]) / 2]

    named = {"d415_face": face(ext["d415"]), "screen_face": face(ext["pt_screen_cover"])}
    for frame, spec in asm["frames"].items():
        pos = named[spec["at"]] if "at" in spec else spec.get("xyz_mm")
        u.fixed_sensor("head_tilt_link", frame, pos, spec.get("rpy_deg"), pivot, "tab5_pantilt")


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
        head.append("     LEFT OUT (no CAD position in the repo yet):")
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
