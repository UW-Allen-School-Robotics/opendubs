"""Swap a variant part into an opendubs.xml MjSpec, before it is compiled.

opendubs.xml describes the FITTED parts. A part with alternatives (dimensions.yaml
femto_mount.variants) is changed here rather than in a second copy of the model, so the two
can't drift:

    import mujoco, variants
    spec = mujoco.MjSpec.from_file("description/opendubs/opendubs.xml")
    variants.femto_mount(spec, "tilt30")        # before spec.compile()

The camera sites and the mesh's placement come from dimensions.yaml (needs PyYAML). The collision
boxes are coarse, about 2 mm outward, like the default mount's in opendubs.xml.
"""
from __future__ import annotations

import math
from pathlib import Path

HERE = Path(__file__).resolve().parent
FEMTO_MOUNTS = ("straight", "tilt30")

# The 30-degree mount's collision, in the femto_mount body's frame (metres). From its mesh, after
# head.export.registered: the camera's envelope as a box turned 30 deg down with it, and the mount
# as one box around everything else (x 26.4..70.8, y +/-60.6, z 891.0..1019.3 mm in the export).
TILT30_CAMERA_BOX = {"pos": [0.01077, 0.0, 0.04164], "size": [0.0346, 0.05958, 0.0221], "pitch_deg": 30.0}
TILT30_MOUNT_BOX = {"pos": [-0.00719, 0.0, 0.01445], "size": [0.02419, 0.06255, 0.06614]}


def _pitch_quat(deg: float) -> list[float]:
    """About +y; positive swings +x toward -z (down), as the head's tilt does."""
    a = math.radians(deg) / 2
    return [math.cos(a), 0.0, math.sin(a), 0.0]


def _dimensions() -> dict:
    import yaml
    return yaml.safe_load((HERE / "dimensions.yaml").read_text())


def femto_mount(spec, variant: str):
    """Fit `variant` (one of FEMTO_MOUNTS) on the femto_mount body. "straight" is what opendubs.xml
    already carries, so it changes nothing."""
    if variant not in FEMTO_MOUNTS:
        raise ValueError(f"no Femto mount {variant!r}: one of {', '.join(FEMTO_MOUNTS)}")
    if variant == "straight":
        return spec
    d = _dimensions()
    ex = d["head"]["export"]
    marks = ex["landmarks_export_mm"]
    asm = d["head_assembly"][d["head_assembly"]["fitted"]]
    iface = marks[asm["femto_mount"]["at"]]
    mod = d["head_assembly"]["modules"][asm["femto_mount"]["variants"][variant]["module"]]
    reg = ex["registered"]["femto_mount_tilt30"]

    # One mesh carries the mount and the camera: it takes the mount's visual, the camera's goes.
    vis = spec.geom("femto_mount_visual")
    vis.meshname = "femto_mount_tilt30"
    vis.pos = [(r - i) / 1000.0 for r, i in zip(reg, iface)]
    spec.delete(spec.geom("femto_bolt_visual"))

    cam = spec.geom("femto_bolt_collision")
    cam.pos, cam.size = TILT30_CAMERA_BOX["pos"], TILT30_CAMERA_BOX["size"]
    cam.quat = _pitch_quat(TILT30_CAMERA_BOX["pitch_deg"])
    mount = spec.geom("femto_mount_collision")
    mount.pos, mount.size = TILT30_MOUNT_BOX["pos"], TILT30_MOUNT_BOX["size"]

    for site, f in mod["frames"].items():
        assert f["rpy_deg"][0] == f["rpy_deg"][2] == 0, f"{site}: only a pitch is supported"
        s = spec.site(site)
        s.pos = [(p - i) / 1000.0 for p, i in zip(marks[f["at"]], iface)]
        s.quat = _pitch_quat(f["rpy_deg"][1])
    return spec
