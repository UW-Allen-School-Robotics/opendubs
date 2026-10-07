#!/usr/bin/env python3
"""Vendor the OpenArm MuJoCo model into description/openarm/ — and refresh it later.

    git clone https://github.com/ericjgoossen/openarm_assets /tmp/oa_assets
    uv run --python 3.11 --with trimesh --with fast-simplification --with numpy \\
        python tools/vendor_openarm.py --from /tmp/oa_assets

What it copies, from an openarm_assets checkout:

  generated/openarm_bimanual_decorated.xml  -> mjcf/   unchanged (meshdir is overridden at load)
  meshes/collision/**                       -> meshes/collision/   byte-for-byte
  meshes/visual/**.obj                      -> meshes/visual/      DECIMATED (see KEEP)
  the two licences                          -> LICENSE-openarm-models (Apache-2.0, Enactic)
                                               LICENSE-openarm_assets (MIT)

and writes PROVENANCE.json: the source commit, the keep fraction, and triangle counts before and
after, so the reduction can be audited and repeated.

WHY DECIMATE. Upstream's visual meshes are 41 MB of OBJ — more than half the size of this whole
repository — for detail nobody sees at viewer scale. KEEP = 0.10 targets a tenth of the
triangles. A few parts (link 7's shell, the hand) stop well short of that — the quadric
simplifier will not collapse their features further — so the result is ~21 % of the triangles
and ~6 MB of visual mesh (about 2 MB once git compresses it). The collision meshes, which
clearance checks use, are never decimated.

The dashboard's arm view uses the COLLISION meshes and urdf/v1.urdf, which this script does not
touch; tools/arm_chain.py regenerates chain.json from that URDF.
"""
from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import re
import shutil
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
DEST = REPO / "description" / "openarm"
KEEP = 0.10            # fraction of visual-mesh triangles kept
MIN_TRIANGLES = 400    # small parts are left alone below this; decimating them buys nothing


def decimate(src: pathlib.Path, dst: pathlib.Path, keep: float) -> tuple[int, int]:
    import fast_simplification
    import numpy as np
    import trimesh

    mesh = trimesh.load(src, force="mesh", process=False)
    before = len(mesh.faces)
    mesh.merge_vertices()
    if before > MIN_TRIANGLES:
        target = max(keep, MIN_TRIANGLES / before)
        pts, faces = fast_simplification.simplify(
            np.asarray(mesh.vertices, dtype=np.float32), np.asarray(mesh.faces),
            target_reduction=1.0 - target)
        mesh = trimesh.Trimesh(pts, faces, process=False)
    dst.parent.mkdir(parents=True, exist_ok=True)
    # No normals, no material library: MuJoCo computes normals and the MJCF defines materials.
    # Units are millimetres (the MJCF scales by 0.001), so two decimals is 10 µm — far below
    # anything visible, and these meshes are for looking at, not for clearance.
    dst.write_text(trimesh.exchange.obj.export_obj(mesh, include_normals=False, include_texture=False,
                                                   include_color=False, digits=2))
    return before, len(mesh.faces)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--from", dest="src", required=True, help="an openarm_assets checkout")
    ap.add_argument("--keep", type=float, default=KEEP, help="fraction of visual triangles kept")
    a = ap.parse_args()

    root = pathlib.Path(a.src).expanduser().resolve()
    models = root / "src" / "openarm_assets" / "models" / "openarm"
    xml = models / "generated" / "openarm_bimanual_decorated.xml"
    if not xml.is_file():
        sys.exit(f"not an openarm_assets checkout: {xml} is missing")

    for sub in ("mjcf", "meshes"):
        shutil.rmtree(DEST / sub, ignore_errors=True)
    (DEST / "mjcf").mkdir(parents=True)
    shutil.copy2(xml, DEST / "mjcf" / xml.name)
    shutil.copytree(models / "meshes" / "collision", DEST / "meshes" / "collision")

    tri_before = tri_after = 0
    for obj in sorted((models / "meshes" / "visual").rglob("*.obj")):
        rel = obj.relative_to(models / "meshes" / "visual")
        b, f = decimate(obj, DEST / "meshes" / "visual" / rel, a.keep)
        tri_before += b
        tri_after += f

    shutil.copy2(models / "LICENSE", DEST / "LICENSE-openarm-models")
    shutil.copy2(root / "LICENSE", DEST / "LICENSE-openarm_assets")

    commit = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                            capture_output=True, text=True).stdout.strip() or "unknown"
    readme = (root / "README.md").read_text() if (root / "README.md").is_file() else ""
    m = re.search(r"openarm_mujoco/commit/([0-9a-f]{7,40})", readme)
    prov = {
        "source": "https://github.com/ericjgoossen/openarm_assets",
        "source_commit": commit,
        "upstream": "https://github.com/enactic/openarm_mujoco",
        "upstream_commit": m.group(1) if m else "see openarm_assets README",
        "vendored": datetime.date.today().isoformat(),
        "visual_keep_fraction": a.keep,
        "visual_triangles_before": tri_before,
        "visual_triangles_after": tri_after,
        "unchanged": ["mjcf/openarm_bimanual_decorated.xml", "meshes/collision/**"],
        "modified": ["meshes/visual/** — decimated by tools/vendor_openarm.py"],
    }
    (DEST / "PROVENANCE.json").write_text(json.dumps(prov, indent=2) + "\n")
    size = sum(p.stat().st_size for p in (DEST / "meshes").rglob("*") if p.is_file())
    print(f"vendored {commit[:8]} -> {DEST.relative_to(REPO)}: visual {tri_before:,} -> "
          f"{tri_after:,} triangles; meshes {size / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
