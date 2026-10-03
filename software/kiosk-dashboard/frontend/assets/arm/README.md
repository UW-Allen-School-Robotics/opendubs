# Vendored OpenArm description

Upstream: **[enactic/openarm_description](https://github.com/enactic/openarm_description)**,
Apache-2.0, taken via the `EricJGoossen/openarm_description` fork at commit
`e6f3af6108a2faf1d5108547b22dccd4b7f64f9a`. `LICENSE.txt` is upstream's, unmodified.

Vendored rather than read from the robot so the dashboard is self-contained: the arm view works
from a browser cache with no ROS, no container and no network path to `/openarm_ws`.

## What is here, and what was left behind

| | |
|---|---|
| `v2.urdf` | the **pre-expanded** URDF (upstream ships it under `urdf/example/`). The source tree is xacro — 23 `.xacro` files against 2 plain `.urdf` — and a browser cannot process xacro, so this is the only usable form |
| `meshes/` | the 11 **collision** meshes the URDF references, 0.75 MB total, shared by both arms (21 references resolve to 11 files) |
| `chain.json` | **generated** by `tools/arm_chain.py`, not hand-written. Run `--check` to verify it still matches `v2.urdf` |

**The visual meshes are deliberately absent: they are 143 MB**, with single links up to 10.3 MB.
The collision meshes are the decimated set — upstream's body mesh is even named
`body_link0_symp.stl` — and at 0.75 MB they are smaller than one photograph while still being
real geometry rather than a stick figure.

## The one modification

Collision `<geometry><mesh filename="package://openarm_description/...">` paths are rewritten to
`meshes/<basename>`, because a browser cannot resolve `package://`. Visual mesh references are
left untouched and simply point at files that are not vendored — nothing reads them.

## Refreshing it

Re-copy from upstream, re-flatten the collision paths, then re-run `tools/arm_chain.py`. If the
kinematics changed, `--check` fails and `chain.json` must be regenerated — which is the point:
the arm view's geometry cannot silently drift from the model the robot is actually driving.
