# Simulation

Two simulators, one model, different jobs.

**MuJoCo, now, on any laptop.** Fast, runs natively on Apple Silicon, and answers the questions
choreography actually asks: does this pose hit anything, can the arm reach there, does the
sequence read well. Authoring a show should not require the robot — waiting for robot access is
the slowest part of making one.

**Isaac Sim / Omniverse, later, on a workstation with an RTX card.** Earns its keep for what
MuJoCo is not for: photoreal camera and LIDAR simulation, domain randomisation, learned policies.
Not on a Mac. See `notes/` for the `embot3` host, which already runs Isaac Sim.

The bridge between them is a **URDF/xacro kinematic description**, which Isaac imports to USD
cleanly. MJCF is what MuJoCo runs; the two stay consistent because both are generated from the
same measured dimensions in `dimensions.yaml`.

## The single most valuable thing this buys

**Self-collision checking, which nothing in the real stack has.**

Today `software/choreo/cue.py` refuses a shoulder above 157° because *I measured contact with
the head at 182° by hand, once, on one axis, with a human watching*. Eric's controller will not
stop it either — its only Cartesian guard is a 1.2 m workspace box, and the arm reaches the head
comfortably inside that box.

With a model we compute the real thing: minimum distance between any arm link and the head, the
chassis, or the other arm, for any pose in a show. The hand-measured constant becomes a
fallback rather than the rule.

## What is already modelled, and must not be redrawn

`openarm_assets` (public, pulled by the arm software's `setup.sh`) ships a complete bimanual
model — **including the stock OpenArm V1 extrusion frame the arms mount to** — with collision
meshes, visual meshes, and per-motor joint defaults:

```
models/openarm/generated/openarm_bimanual_decorated.xml   # MJCF
models/openarm/urdf/...                                   # xacro
```

Root body `openarm_body_link0`, 13.89 kg, with the shoulders at `[0, ±31, 698] mm` from its
origin. We `include` that model and place its root. We do not touch the arms.

## What is ours to model

| Part | Why it matters |
|---|---|
| Chassis / base frame | what the arms can collide with, and the floor datum |
| Lift platform | a prismatic joint, 275.1 mm stroke (10.83 in), carrying the whole arm frame. Stiff: a screw-driven scissor does not bounce, so it steps 275 mm with 0.1 mm of overshoot |
| Head bracket + Tab5 panel | **the thing the arms nearly hit** — the reason this is worth doing |

| Mecanum wheels | driven, with rollers — see below |

The wheels are **actuated mecanum**, not scenery. Each is a hub on its axle carrying 16
free-spinning rollers skewed 45 degrees, so the sideways motion emerges from roller contact
rather than being imposed. They are driven by `driveMix()` copied from
`firmware/rc_serial_probe`, so `--drive "1,0,0"` means on this model what `DRIVE 1 0 0` means
on the robot.

The rollers are **authored, not supplied**: all four wheel meshes are byte-identical
412-triangle cylinders with no roller detail and no handedness. The count and capsule shape are
a simplification. The handedness is not — it comes from the firmware mixer, where FL and RR
share a strafe sign and FR and RL share the opposite one — and it was then *checked* rather
than assumed:

```
DRIVE command      dx mm    dy mm  dyaw deg
fwd +1            1153.0      3.0      0.38
fwd -1           -1143.8     -0.9      0.07
strafe +1           -2.9  -1038.6      0.33      (+y is left, so this is right — correct)
rot +1             562.6    226.1   -137.95      (turns right; the origin orbits the wheelbase)
```

That table is the point: a mecanum model with one roller skewed the wrong way still drives
forward perfectly and only fails in strafe, which is exactly the kind of error that survives a
visual check.

## Fidelity ladder — what each stage can and cannot answer

| | Answers | Does not answer |
|---|---|---|
| Kinematic MuJoCo | collisions, reach, clearance, whether a sequence reads | forces, grasping, sag |
| Dynamic MuJoCo | approximate gravity sag, impedance feel, payload effects | contact-rich manipulation with any fidelity |
| Isaac | camera/LIDAR imagery, perception, RL, many robots | anything we have not measured properly |

Nothing here answers "will the gripper hold this object". Say so out loud when showing it.

## Building the whole robot

```bash
git clone --depth 1 https://github.com/ericjgoossen/openarm_assets /tmp/oa_assets   # once
python3 scene.py --check
python3 scene.py --render /tmp/up.png --lift 275 --joints "right:2=150,right:4=100"
```

`scene.py` attaches the bimanual arm model to the lift platform with MuJoCo's `MjSpec`, so
neither file is edited or copied — when openarm_assets updates, we get the update. The assets
are found via `--assets`, `$OPENDUBS_OPENARM_ASSETS`, or the paths the arm software's own
`setup.sh` uses; they are deliberately **not** vendored here.

**The chest cover is suppressed**, because it is removed on our robot and the face mounts at the
top of the extrusion it used to cover (`--chest-cover` draws it again for comparison).
Caveat worth remembering: the arm model's *collision* geometry is a single `body_link0.stl`
that still contains the cover's volume, so clearance against the torso is conservative until we
supply our own frame collision mesh.

## Status

`dimensions.yaml` holds what we know, with provenance, and `TODO` for what we do not. Nothing
in the model is invented: a missing dimension stays missing until it is measured or exported
from CAD.

Supplied and in use: the chassis, four wheels, and the lift plate — all exported at the CAD
origin, which is also the model's datum. From them we derived the floor (z = -94 mm, off the
wheel geometry), a 408 mm wheelbase and a 589 mm track.

The head is in too — `pt_tilt_body`, `pt_screen_cover` and `D415`, supplied in CAD coordinates,
so they are placed by cancelling the head frame's offset rather than by positioning each part.
That was the point of the exercise: the head is what the arms nearly hit, and until it existed
`software/choreo/cue.py` had to refuse shoulder angles on a constant measured by hand once.

Two corrections are folded into that single transform, both confirmed against the robot: the
export is **posed at pan=180** (as exported the screen and camera sat behind the tilt axis,
facing -x, while the wheels put forward at +x — FL/FR at x=528, RL/RR at x=120), and it is
**1.85 mm off the centreline**, which is an artefact rather than a real offset.

## Poking at it interactively

```bash
./view.sh          # orbit, zoom, joint sliders; space pauses
```

Deliberately **not** `mjpython`, despite MuJoCo's own macOS instructions. On this machine
mjpython starts fine — `viewer._MJPYTHON` is set — but constructing the viewer then dies with
`RuntimeError: Caught an unknown exception!`, on 3.14.0 and 3.3.5 alike, for any model
including a single sphere, from every shell tried. The same construction succeeds under plain
`python`, which works here because this is the *blocking* viewer and so already owns the main
thread. `launch_passive` — a script driving the viewer while it runs — would need mjpython, so
that problem is still there if we ever want it.

`glfw` is an explicit dependency: MuJoCo's simulate dlopens it at runtime and fails with
`cannot obtain a shared object handle` when the package is missing.

Still needed, same convention — **binary STL, millimetres, at the CAD model origin**:

- the 1120 channel's pose. Its mesh is still in its own part frame, so it is left out rather
  than placed on a guess — and it is the structure an arm would actually strike on the way up.
