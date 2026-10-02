# Arm telemetry and visualization

Goal 5: show the arms on the tablet, and get arm telemetry to other clients without duplicating
the path. Partly UI, mostly a question of which source of truth and which transport.

## Do not route Viser to the tablet

The obvious move is to reuse what exists. `mj_viser` is a **browser-based** 3D viewer, and
`mj_manipulator/status_hud.py` already overlays a status HUD on it that reads `OwnerKind` straight
out of the ownership registry. A tablet is a browser. It looks like a free win.

**It is not, and the reason disqualifies it for operator use.** Viser renders the MuJoCo *shadow*
model, and `eric-openarm/hw_validation/common/hardware_sync.py` states plainly what the shadow
holds:

> the shadow MuJoCo model that planning reads and writes is never otherwise synced from the real
> arm's measured position. Left unsynced, planning starts from whatever the shadow happens to
> hold — a fixed assumed pose, or wherever `execute()` nominally commanded it, which can differ
> from where the arm's impedance controller actually settled

Only the `hw_validation` stage scripts call `sync_shadow_from_hardware()`. So Viser shows the arm
**where it was told to go**, not where it is — and the same comment names when the divergence is
worst: *"especially after an abort/e-stop, which can leave the arm partway through a trajectory
rather than at its planned endpoint."*

A display that is most wrong immediately after an e-stop is the opposite of what an operator
display is for. Viser stays a planning and development viewer. The tablet gets a display driven
from **`/joint_states`** — measured.

This is not a criticism of Viser; a planning viewer *should* show the planning model. It is the
wrong instrument for this job.

## Transport: rosbridge, not the dashboard backend

The arms' ROS stack runs in the `openarm` container, created with **`--network=host`**
(`docs/openarm-setup.md:121`), so it shares the host's network and default
`ROS_DOMAIN_ID=0`/FastDDS. Host-side `rosbridge` on **9090** can therefore see `/joint_states`
without new plumbing — worth confirming on the robot, but the architecture needs nothing added.

**The dashboard backend must not proxy arm telemetry.** Its job is the StamPLC's UDP text protocol.
Putting ROS data through FastAPI and back out over the dashboard websocket is a second hop, a second
staleness model and a second place for the rate to be wrong, for no gain — the frontend already
talks to rosbridge.

### Efficiency is a subscription parameter, not a new node

`/joint_states` arrives at controller rate for 16 motors. Streaming that raw to a tablet is
wasteful and is the thing to avoid, but it does **not** need a decimating republisher: rosbridge
takes `throttle_rate` and `queue_length` **per subscription**, so each client asks for what it can
use. The tablet asks for display rate; a recorder asks for full rate; nobody is penalised by
another client's needs.

That is the answer to "efficiently communicating arm telemetry to other clients": **one publisher,
many subscriptions, each throttled at the subscriber.** No fan-out node to keep in sync, and no
client's rate baked into a shared path.

## What the display has to show

Joint positions alone are not a useful operator display. From
[arm authority](arm-authority.md), per arm: **mode**, **lease holder**, and **liveness** — and the
reason liveness is on that list is that a frozen display showing a plausible pose is the failure
this note exists to avoid. A stale `/joint_states` must read as stale, not as "not moving".

Also worth surfacing, because they exist and already caused confusion: motor temperature and the
per-motor `ENABLED`/`DISABLED` status that `openarm-motors` reads directly over CAN, independent of
whether the stack is running. That path is the only one that answers "is this arm energized" when
the ROS stack is down.

## Open

- **Whether `/joint_states` is actually visible to host rosbridge.** `--network=host` says it should
  be; nothing has checked. If it is not, the cause is `ROS_LOCALHOST_ONLY` or an RMW mismatch, not
  the architecture.
- **Display rate.** Needs choosing against the tablet's own render budget, not guessed here.
- **Whether the arm display is its own tab or part of the operate view.** Depends on the per-arm
  `HOLD` controls from [arm authority](arm-authority.md) landing in the same place.
