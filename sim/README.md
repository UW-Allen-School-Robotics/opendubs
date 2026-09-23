# Simulation

> ## The geometry is real. The dynamics are not.
>
> Every length, axis and pivot here traces to CAD or to a measurement, and where one does not
> it says so. **None of that is true of the masses, inertias, stiffnesses, damping or speeds.**
> The chassis mass is a guess. The head's is mesh volume at the density of water. The arm
> masses come from someone else's model. The lift's speed and thrust were chosen to be
> physically possible, not measured.
>
> So this model answers: *does this pose collide, can the arm reach there, how much clearance
> is there, does the sequence read.* It does **not** answer anything about force, torque, sag,
> tipping, motor sizing, how long a move takes, or what happens when something pushes the
> robot. Numbers of that kind out of this model are decoration.

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

## Where this fits

**This directory is the robot description, and it is independent of whoever moves the robot.**
Geometry, and the platform's own dynamics: chassis, wheels, Z-axis, head. It is not a planner
and does not assume one.

The arms are deliberately not described here — `openarm_assets` owns them and they are attached
at runtime. Planners are *consumers* of this description, not owners of it:

- **`mj_manipulator`** (the UW Personal Robotics Lab stack, which Eric works in) plans and
  executes multi-arm trajectories. It moves **the arms and grippers, and nothing else** — not
  the lift, the head, the base, sound, the face or the lights. Those are ours whatever planner
  is in use.
- **Direct joint trajectories** through `ros2_control`, which is what the hand-written demos do
  today, and which needs no planner at all.
- **A full-body planner** is on the roadmap. It will consume the same description.

So expect several of these over the robot's life, and expect them to disagree. Anything that
would have to be rewritten per planner does not belong in here.

## Clearance checking

`clearance.py` answers *how close does the robot come to hitting itself*, from geometry:

```bash
python3 clearance.py --joints "right:2=182,right:4=100"     # how close is this pose?
python3 clearance.py --contact right:2 --hold "right:4=100" # where does it touch?
```

It reports the closest pairs and marks which ones actually **move** with the pose, because the
arm frame sits a fixed distance from the head whatever the arms do and that constant floor
otherwise hides the number an author cares about.

**Checked against the robot, and six degrees optimistic.** Contact was felt by hand at J2 182;
the model reaches zero at 188.3, for both arms, and barely varies with elbow fold (187.5 at
J4=60, 188.3 at J4=140). Six degrees in the *unsafe* direction, so the measured constant stays
in charge and this supplies the shape of the curve between the points nobody measured.

**It is a check, not the guard.** Two things already protect the real robot, and this is a third
opinion rather than the thing standing between the arm and the head:

- the arm model's *collision* geometry is a single `body_link0.stl` that still contains the
  suppressed chest cover's volume, so torso clearance is conservative by construction;
- the impedance controller's TCP workspace constraint.

And the poses where the face is reachable at all are unlikely in normal operation.

**The arms cannot reach the lift**, so that pair is not a question this needs to answer: the
whole arm frame rides on the platform, so its reach envelope moves with it and always sits
above it. Worth stating because it is easy to model the envelope as if it were fixed to the
chassis and then go looking for a collision that geometry rules out.

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
| Lift platform | a prismatic joint, 275.1 mm stroke (10.83 in), carrying the whole arm frame. Stiff, slow and force-limited. A screw-driven scissor does not bounce and cannot throw the robot into the air, so the actuator is capped at 2500 N and the joint damped to about 56 mm/s — full stroke in ~5 s, arriving with the base rising 0.08 mm. Both numbers are placeholders |
| Head bracket + Tab5 panel | **the thing the arms nearly hit** — the reason this is worth doing |

| Mecanum wheels | driven, with rollers — see below |

The wheels are **actuated mecanum**, not scenery. Each is a hub on its axle carrying 16
free-spinning rollers skewed 45 degrees, so the sideways motion emerges from roller contact
rather than being imposed. They are driven **as the RC sticks drive them** — forward, strafe, rotate — not one slider per
wheel, because a wheel at a time tells you nothing about a mecanum base. The three controls are
actuators on three fixed tendons whose coefficients *are* `driveMix()` from
`firmware/rc_serial_probe`, so `--drive "1,0,0"` means on this model what `DRIVE 1 0 0` means on
the robot:

| control | stick | + direction |
|---|---|---|
| `drive_fwd` | CH3 | forward |
| `drive_strafe` | CH4 | right |
| `drive_rot` | CH1 | turning right |

Three controls over four wheels is not a shortfall: a mecanum base has exactly three degrees of
freedom, and the fourth wheel combination is internal slip that no stick commands.

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

The chassis is a **box primitive**, not a mesh. `chassis.stl` was a bounding shape whose extrude
left an 8 mm step at two diagonally opposite corners — an artefact rather than the robot — and
MuJoCo filled it back in anyway, since mesh collision uses the convex hull. Stating the same
volume directly is exact and has no corner to explain. It goes back to a mesh when the chassis
is drawn for real.

## Tripwires

Meshes arrive from CAD by hand, and a re-export can come back mirrored, upside down, or about a
different origin. All three have happened here, and none of them looks wrong in a render taken
from the front. So `scene.py --check` asserts a few things a human could confirm by looking at
the robot — the face points forward, the camera sits above the screen, nothing on the head is
more than 200 mm from the axis it turns about, and the shoulders land at 1149.5 mm — and exits
non-zero when one fails:

```
FAIL: screen is BEHIND the tilt axis (x 363 <= 423) — the face is pointing backwards
```

Supplied and in use: four wheels and the lift plate — all exported at the CAD
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

The 1120 channel is not modelled and does not need to be — it fits inside the tilt body, so
nothing can reach it without reaching that first.

What is still missing is not geometry: the **head and chassis masses**. Both are currently mesh
volume at MuJoCo's default density, which is to say solid water — 2.97 kg for a head that is
nearer 0.8 kg. A scale would fix both.
