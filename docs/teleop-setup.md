# WebXR teleop

How to drive the two arms from a Meta Quest: the headset's controllers stream their poses to a
bridge on the Thor, and the bridge moves each arm to follow the hand that holds its grip. This page
covers what must be in place, how to start a session, how the controls behave, and what "working"
looks like. The tablet's arm buttons, and what each of their messages means, are on
[Arm controls on the tablet](arm-dashboard.md).

!!! warning "The arms move"
    Teleop moves the arms wherever the operator's hands go. The bridge's configured speed limit is
    1.0 m/s at the gripper. **There is no self-collision checking**, and nothing yet keeps an arm out of the robot's own
    head: with the elbow folded, the shoulder (J2) reaches the head at about 182°. Keep a clear
    workspace, and keep the shoulder well below that. Releasing the grip stops an arm where it is,
    and the [e-stop](e-stop.md) removes arm power entirely.

## Prerequisites

- The arms provisioned and working per [OpenArm setup](openarm-setup.md): `openarm-up activate`
  brings both arms up, including their **gripper controllers**.
- The robot's RC transmitter, with its mode switch (**CH5**) available. Teleop moves an arm only
  in **MANUAL**, the middle position; see [Control authority](control-authority.md).
- A Meta Quest headset with its two controllers, on the **same network as the robot**. Only the
  Quest Browser is used; no app is installed on the headset.
- The robot's dashboard on the tablet, with the TELEOP page.

## How it fits together

| Piece | What it does | Where |
|---|---|---|
| WebXR page | Runs in the Quest Browser, reads the controllers every frame and streams their poses, grips, triggers and buttons — plus the headset's own pose | served by the bridge at `https://<robot-address>:8444/` |
| Pose channel | A secure WebSocket the page sends those poses over | `wss://<robot-address>:8766/` |
| Teleop bridge | Turns each hand's motion into a target for its arm, solves the arm's inverse kinematics, and streams joint commands to the arm controllers | `opendubs-teleop.service`, running inside the `openarm` container |
| Arm stack | The arms' controllers, started by `openarm-up` | see [OpenArm setup](openarm-setup.md) |
| TELEOP page | Starts and stops the bridge, shows readiness, the arms' live pose and the control loop's health | the dashboard |

The bridge is a copy of the upstream OpenArm WebXR teleop with changes for this robot: losing the
headset stops the arm, releasing the grip stops it where it is, the gripper and the home buttons
answer only while allowed, motion requires CH5 in MANUAL, and the bridge keeps its internal model of
each arm synced to the real joints. Those behaviours are described below.

The bridge's service is **installed but not enabled**: it never starts at boot, only from the
TELEOP page. It also never restarts itself after it stops, so a session the operator ended stays
ended.

## Certificates

A WebXR session needs a secure page, so the bridge serves both the page and the pose channel over
TLS with a **self-signed certificate**. Two things follow.

**The certificate must name the robot's address.** It lists the address the headset connects to
(its *subject alternative names*). If the robot's address changes — a new network, a different
robot — regenerate it inside the container and restart the bridge:

```sh
sudo docker exec openarm bash -c 'cd /openarm_ws/src/openarm && bash scripts/generate_certs.sh --force'
```

The pair is written to `certs/cert.pem` and `certs/key.pem` in the bridge's checkout, which is
git-ignored: it is per machine and is never committed.

**The headset must accept the certificate on both ports.** A browser treats each port as a separate
site, and it shows a warning only for the page — **a refused pose channel fails silently**: the
page loads and nothing reaches the robot. In the Quest Browser, open both once and accept the
warning:

1. `https://<robot-address>:8766/` — the pose channel. Accepting is all that is needed here; the
   page itself shows nothing useful.
2. `https://<robot-address>:8444/` — the teleop page.

The TELEOP page shows the exact address to use, derived from however the tablet reached the
dashboard.

## Starting a session

1. **CH5 to MANUAL** (the middle position).
2. On the tablet's TELEOP page, **ACTIVATE L** and **ACTIVATE R**. Each arm energizes and moves to
   its zero pose, so clear the workspace first. Each press is confirmed with a second tap; see
   [Arm controls on the tablet](arm-dashboard.md).
3. **START** under TELEOP, then wait for the SERVER badge to read **READY**.
4. **Stand behind the robot, facing the way it faces.** Hand motion is measured in a frame fixed
   where the headset faced when the immersive session began, and the bridge assumes that was the
   robot's forward.
5. In the Quest Browser, open the address the TELEOP page shows and enter the immersive session.
6. Squeeze a controller's **grip** and move: that arm follows.

To end: release both grips, **STOP** under TELEOP, then **PARK BOTH** and **DE-ENERGIZE** if the
arms are done for now.

## The controls

| Input | Effect |
|---|---|
| **Grip** (held) | The clutch for that hand's arm. While held, the arm follows the hand's motion *relative to where the hand was when the grip closed*, so the arm never jumps to meet the hand. |
| Grip released | The arm **stops where it is** — not where it was last told to go. |
| **Trigger** (while gripping) | Opens or closes that arm's gripper. Squeezed while the grip is not held, it does nothing. |
| **A** (right) / **X** (left), held, grip released | Sends that arm **home** to the robot's stow pose, in a straight line in joint space at about 15°/s per joint. It stops the moment the button is released. |

The bridge's rules about when motion is allowed:

- **CH5 must be in MANUAL.** In OFF or AUTO, a grip is refused. If CH5 leaves MANUAL mid-grip, the
  arm stops where it is, and the grip must be released and squeezed again once CH5 is back in
  MANUAL.
- **The headset must keep streaming.** A hand whose data is more than 0.5 s old is treated as lost:
  its arm stops where it is. Once data returns, that hand must release and squeeze its grip again —
  holding the grip through a dropout never resumes on its own.
- **Home** needs CH5 in MANUAL, a stow pose configured for that arm, and the grip released.

## What working looks like

On the tablet's TELEOP page:

- **SERVER** reads **READY**, with **HTTPS page :8444** and **pose channel :8766** both shown as
  good, **arms energized**, and **motors 16 enabled, 0 faults**.
- **SESSION** reads **ARMS LIVE**, and the arm view moves with the arms.
- **LOOP vs BUDGET** shows the bridge's control loop meeting its rate. Occasional overruns are
  normal; a large share over budget is why teleop can feel vague.

On the headset: the controllers are tracked, and squeezing a grip makes the arm follow.

From a shell on the Thor, the bridge's log is the ground truth:

```sh
journalctl -u opendubs-teleop -f
```

Look for `first pose data received from RIGHT controller` (and LEFT) once the headset is in the
session.

## Known limits

- **From the zero or stow pose, an arm cannot move forward or up at first.** A straight, hanging
  arm has its elbow on its stop, and the inverse kinematics finds no solution in those directions.
  Move it backward or outward first, or work from a pose with the elbow bent.
- **The arm lags the hand.** The arm controller trails its commands by about 40–75 ms, before any
  network delay, and joints overrun the commanded speed briefly when they reverse direction.
- **Wrist rotation follows world axes.** Tipping the controller nose-down tips a gripper that hangs
  straight down *backward*, which can read as mirrored.
- **The bridge's refusals are not shown on the tablet yet.** A refused grip, a lost hand or a refused
  home appears only in the bridge's log; see [Arm controls on the tablet](arm-dashboard.md#teleop-messages).

## Troubleshooting

See [Arm controls on the tablet](arm-dashboard.md) for every message the tablet and the bridge
produce, each under a short name, with what it means and what to do.
