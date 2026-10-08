# Robot description

The single description of the robot's geometry, read by everything that needs it: the MuJoCo
simulator (`sim/`), the clearance checker, the choreography preview, and the dashboard's arm
view. Nothing else in the repository carries a copy.

| Folder | What | Source |
|---|---|---|
| [`opendubs/`](opendubs/) | the base, wheels, Z-axis lift and pan/tilt head: `opendubs.xml` (MJCF), its meshes, and `dimensions.yaml`, which records where every number came from | this project, from CAD and measurement |
| [`openarm/`](openarm/) | the two OpenArm v1 arms and their frame: `mjcf/` (MuJoCo), `urdf/` (for the dashboard), `meshes/collision/` (shared by both), `meshes/visual/` (MuJoCo only, decimated), and `chain.json` (generated) | vendored; see below |

**The geometry is real; the dynamics are not.** See the banner in `sim/README.md` before reading
anything about force, timing or mass out of these files.

## The vendored arms

`openarm/` comes from two upstream projects, both Apache-2.0 at their origin:

- **MuJoCo model** — `mjcf/` and `meshes/` from
  [`openarm_assets`](https://github.com/ericjgoossen/openarm_assets) (MIT), which packages
  [`enactic/openarm_mujoco`](https://github.com/enactic/openarm_mujoco). The MJCF and the collision
  meshes are unchanged; the visual meshes are decimated to keep this repository small.
  `PROVENANCE.json` records both commits and the triangle counts before and after.
- **URDF** — `urdf/v1.urdf` from
  [`enactic/openarm_description`](https://github.com/enactic/openarm_description), with its
  collision mesh paths pointed at the shared meshes. See `urdf/README.md`.

Licences: `LICENSE-openarm-models` (Apache-2.0, the arm models), `LICENSE-openarm_assets` (MIT,
the packaging), `urdf/LICENSE.txt` (Apache-2.0, the URDF).

### Refreshing the arms

```bash
git clone https://github.com/ericjgoossen/openarm_assets /tmp/oa_assets
uv run --python 3.11 --with trimesh --with fast-simplification --with numpy \
    python tools/vendor_openarm.py --from /tmp/oa_assets
python3 sim/scene.py --check
python3 tools/arm_chain.py --check
```

To try a newer upstream model without vendoring it, pass the checkout to `sim/scene.py --assets`.

## Who reads what

| Consumer | Reads |
|---|---|
| `sim/scene.py`, `sim/view.sh`, `sim/clearance.py`, `software/choreo/sim_preview.py` | `opendubs/opendubs.xml` + meshes, `openarm/mjcf/` + `openarm/meshes/` |
| The dashboard's arm view (`software/kiosk-dashboard/frontend/armview.js`) | `openarm/chain.json` + `openarm/meshes/collision/`, through `frontend/assets/arm`, a symlink to `openarm/` in a checkout. `deploy.sh` copies just those files to the robot |
| `tools/arm_chain.py` | `openarm/urdf/v1.urdf` → writes `openarm/chain.json` |
