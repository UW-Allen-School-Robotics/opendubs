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
| Lift platform | a prismatic joint, 275.1 mm stroke (10.83 in), carrying the whole arm frame |
| Head bracket + Tab5 panel | **the thing the arms nearly hit** — the reason this is worth doing |

Wheels are drawn but not actuated: `DRIVE` is not a choreography slot, and the base does not
move under a show until odometry can say where the robot is.

## Fidelity ladder — what each stage can and cannot answer

| | Answers | Does not answer |
|---|---|---|
| Kinematic MuJoCo | collisions, reach, clearance, whether a sequence reads | forces, grasping, sag |
| Dynamic MuJoCo | approximate gravity sag, impedance feel, payload effects | contact-rich manipulation with any fidelity |
| Isaac | camera/LIDAR imagery, perception, RL, many robots | anything we have not measured properly |

Nothing here answers "will the gripper hold this object". Say so out loud when showing it.

## Status

`dimensions.yaml` holds what we know, with provenance, and `TODO` for what we do not. Nothing
in the model is invented: a missing dimension stays missing until it is measured or exported
from CAD.

Needed next, as simplified binary STLs in millimetres, **with the origin at the datum named in
`dimensions.yaml`** rather than the CAD world origin:

- chassis / base frame
- lift platform (the moving stage)
- head bracket and the Tab5 panel outline

Plus three transforms: where `openarm_body_link0` sits on the lift platform, the platform's
face height when fully down, and where the head pivot is — and on which body the head rides.
