"""Swap a variant part into an opendubs.xml MjSpec, before it is compiled.

opendubs.xml describes the FITTED parts. A part with alternatives (dimensions.yaml
femto_mount.variants) is changed here rather than in a second copy of the model, so the two
can't drift:

    import mujoco, variants
    spec = mujoco.MjSpec.from_file("description/opendubs/opendubs.xml")
    variants.femto_mount(spec, "straight")      # before spec.compile()

The camera sites and the meshes' placement come from dimensions.yaml (needs PyYAML). The collision
boxes are coarse, about 2 mm outward, like the fitted mount's in opendubs.xml.
"""
from __future__ import annotations

import math
from pathlib import Path

HERE = Path(__file__).resolve().parent
FEMTO_MOUNTS = ("tilt30", "straight")
FITTED = "tilt30"     # what opendubs.xml carries (dimensions.yaml femto_mount.module)

# The straight mount's collision, in the femto_mount body's frame (metres): a box from each mesh's
# extents, about 2 mm outward. Mount: x -31.3..15.0, y +/-60.6, z -49.7..51.4 mm; camera:
# x -31.3..33.9, y +/-57.6, z 7.4..47.6 mm.
STRAIGHT_CAMERA_BOX = {"pos": [0.00135, 0.0, 0.0275], "size": [0.0346, 0.0596, 0.0221], "pitch_deg": 0.0}
STRAIGHT_MOUNT_BOX = {"pos": [-0.00813, 0.0, 0.00084], "size": [0.02513, 0.06255, 0.05253]}


def _pitch_quat(deg: float) -> list[float]:
    """About +y; positive swings +x toward -z (down), as the head's tilt does."""
    a = math.radians(deg) / 2
    return [math.cos(a), 0.0, math.sin(a), 0.0]


def _dimensions() -> dict:
    import yaml
    return yaml.safe_load((HERE / "dimensions.yaml").read_text())


def femto_mount(spec, variant: str):
    """Fit `variant` (one of FEMTO_MOUNTS) on the femto_mount body. FITTED is what opendubs.xml
    already carries, so it changes nothing."""
    if variant not in FEMTO_MOUNTS:
        raise ValueError(f"no Femto mount {variant!r}: one of {', '.join(FEMTO_MOUNTS)}")
    if variant == FITTED:
        return spec
    import mujoco
    d = _dimensions()
    ex = d["head"]["export"]
    marks = ex["landmarks_export_mm"]
    asm = d["head_assembly"][d["head_assembly"]["fitted"]]
    iface = marks[asm["femto_mount"]["at"]]
    mod = d["head_assembly"]["modules"][asm["femto_mount"]["variants"][variant]["module"]]

    # The fitted mount's one mesh carries the mount and the camera; the straight mount's are two,
    # both exported in the head's frame, so the camera gets its own visual.
    vis = spec.geom("femto_mount_visual")
    vis.meshname = "femto_mount"
    vis.pos = [-i / 1000.0 for i in iface]
    body = spec.body("femto_mount")
    body.add_geom(default=spec.find_default("visual"), name="femto_bolt_visual", type=mujoco.mjtGeom.mjGEOM_MESH,
                  meshname="femto_bolt", material="rubber", pos=list(vis.pos))

    cam = spec.geom("femto_bolt_collision")
    cam.pos, cam.size = STRAIGHT_CAMERA_BOX["pos"], STRAIGHT_CAMERA_BOX["size"]
    cam.quat = _pitch_quat(STRAIGHT_CAMERA_BOX["pitch_deg"])
    mount = spec.geom("femto_mount_collision")
    mount.pos, mount.size = STRAIGHT_MOUNT_BOX["pos"], STRAIGHT_MOUNT_BOX["size"]

    for site, f in mod["frames"].items():
        assert f["rpy_deg"][0] == f["rpy_deg"][2] == 0, f"{site}: only a pitch is supported"
        s = spec.site(site)
        s.pos = [(p - i) / 1000.0 for p, i in zip(marks[f["at"]], iface)]
        s.quat = _pitch_quat(f["rpy_deg"][1])
    return spec
