# Arm controls on the tablet

The dashboard's **TELEOP** page is where an operator powers the arms, parks them, starts and stops
teleop, and sees what the arms are doing. Every arm button runs the same `openarm-up` command
described in [OpenArm setup](openarm-setup.md), so the tablet and a shell on the Thor follow the
same rules. This page lists what each button does, how it reports progress, and every message it
can show, each under a stable **code** such as `ARM_OP_RUNNING`. An entry's link anchor is its code,
lower-cased (`#arm-op-running`), so a link survives the entry being reworded.

For the headset side of teleop, see [WebXR teleop](teleop-setup.md).

## The buttons

Every button that moves an arm or ends a session is **confirm-on-tap**: the first tap changes its
label to a confirmation (for example **MOVES TO ZERO**), and only a **second tap within 5 s** sends
it. A single tap does nothing.

| Panel | Button | Confirm label | What it does |
|---|---|---|---|
| ARM POWER | **ACTIVATE L** / **ACTIVATE R** | MOVES TO ZERO | Energizes that arm and moves it to its zero pose over about 2 s, then starts its controller and its gripper controller. If the arm stack is not running, it starts it first (about 15–30 s). After an e-stop, it also recovers the arm by itself — see [`ARM_POWER_RESTORED`](#arm-power-restored). |
| ARM POWER | **DE-ENERGIZE** | CONFIRM DROP | Turns both arms' controllers off, makes the arms limp, stops the arm stack, and confirms every motor is off. Refuses while an arm is energized away from its stow pose, because a limp arm falls. |
| RECOVER | **PARK BOTH** | CONFIRM PARK | Moves both arms to the robot's stow pose, so they can be de-energized safely. |
| RECOVER | **HOLD L** / **HOLD R** | CONFIRM HOLD | Commands that arm to stay exactly where it is, stopping any motion in progress. |
| TELEOP | **START** / **STOP** | CONFIRM START / CONFIRM STOP | Starts or stops the teleop bridge. START does not power the arms: activate them first. |

**One arm action runs at a time.** While one runs:

- its button reads, for example, **ACTIVATING… 12 s**, counting up until it really finishes;
- the other arm buttons are dimmed and do not respond, and a line under them says what is running;
- when it finishes, its button shows **DONE** for a few seconds, or **FAILED — SEE BELOW** with the
  message in the readiness list.

The readiness list (SERVER panel) shows the bridge's two ports, whether the arms are energized, the
motor count and faults, and the last arm action's message.

## Typical sequences

| Goal | Steps |
|---|---|
| Power up and teleoperate | CH5 to MANUAL → **ACTIVATE L** → **ACTIVATE R** → **START** → put on the headset |
| Put the arms away | release both grips → **STOP** → **PARK BOTH** → **DE-ENERGIZE** |
| After an e-stop | release the e-stop and re-arm → CH5 to MANUAL → **ACTIVATE L** → **ACTIVATE R** — nothing else is needed |

## Arm action messages

These come from `openarm-up`, run by an arm button, and appear on the button and in the readiness
list. The same text appears in a shell.

### Another arm action is running {#arm-op-running}

**Code:** `ARM_OP_RUNNING`

**Shown as:** the button reads **BUSY — WAIT**; the line under the buttons reads
*… refused: activate left is still running (12s)*.

**Means:** a second arm action was requested while one was running — usually from another screen,
such as the Shows page. Running two would mean two commands fighting over the same arm.

**Do:** wait for the running action to finish, then press again.

### Power was lost and restored {#arm-power-restored}

**Code:** `ARM_POWER_RESTORED`

**Shown as:** in the action's output, *power was lost and restored (motors disabled under an active
arm) -- resetting this arm*, followed by a normal activation.

**Means:** this is not an error. After an e-stop, or any loss of the arms' power, the motors come
back **disabled** while the arm stack still has the arm marked active. ACTIVATE recognises that
state — every motor answering, none enabled, none faulted — resets just that arm, and activates it
as usual.

**Do:** nothing; the press already recovered the arm.

### The arm could not be reset after a power loss {#arm-reset-failed}

**Code:** `ARM_RESET_FAILED`

**Shown as:** *could not reset the arm after a power loss (hardware is '…', not inactive)*.

**Do:** press **DE-ENERGIZE**, tap it again to confirm, then press **ACTIVATE** for that arm.

### Motors answer but are disabled {#arm-motors-disabled}

**Code:** `ARM_MOTORS_DISABLED`

**Shown as:** *the motors answer but are DISABLED, while the arm stack still has this arm active.*

**Means:** the power-loss state, in a case the automatic reset did not cover.

**Do:** press **DE-ENERGIZE**, tap it again to confirm, then press **ACTIVATE** for that arm.

### Motors do not answer {#arm-motors-silent}

**Code:** `ARM_MOTORS_SILENT`

**Shown as:** *N of 8 motors do not answer on CAN. Not starting its controller.*

**Means:** those motors are unpowered, or the CAN wiring is broken at or before the first silent
one. The gripper is last in the chain, so a lone silent gripper points at the end of the chain.

**Do:** check the e-stop is released and the contactor closed, then press ACTIVATE again. If they
still do not answer, `openarm-motors status` on the Thor shows which motors are silent; see
[OpenArm setup § Troubleshooting](openarm-setup.md#troubleshooting).

### A motor reports a fault {#arm-motor-fault}

**Code:** `ARM_MOTOR_FAULT`

**Shown as:** *motor fault — id3:OVERVOLTAGE. Not starting its controller.* (the motor and its fault
vary).

**Means:** a motor's fault latches until the motor loses power.

**Do:** press and release the e-stop to power-cycle the arms, then press ACTIVATE again.

### Only some motors enabled {#arm-partial-enable}

**Code:** `ARM_PARTIAL_ENABLE`

**Shown as:** *the hardware went active but only N/8 motors enabled*.

**Do:** press **DE-ENERGIZE**, tap it again to confirm, then **ACTIVATE**. If it happens again,
power-cycle the arms with the e-stop first.

### A controller would not start {#arm-controller-inactive}

**Code:** `ARM_CONTROLLER_INACTIVE`

**Shown as:** *right_controller is 'inactive', not active* (or *not loaded*).

**Do:** **DE-ENERGIZE**, then **ACTIVATE**. If it repeats, the arm stack's log (`openarm-up logs` on
the Thor) says why.

### The gripper controller would not start {#arm-gripper-controller-inactive}

**Code:** `ARM_GRIPPER_CONTROLLER_INACTIVE`

**Shown as:** a warning in the action's output: *…_gripper_controller is '…', not active. The arm
will move, but TELEOP WILL NOT START*.

**Means:** the arm works, but the teleop bridge needs both grippers' controllers and will exit about
30 s after START without them ([`ARM_TELEOP_GRIPPER_TIMEOUT`](#arm-teleop-gripper-timeout)).

**Do:** **DE-ENERGIZE**, then **ACTIVATE** that arm again.

### Nothing to park or hold {#arm-not-active}

**Code:** `ARM_NOT_ACTIVE`

**Shown as:** *…: controller is not active — nothing to park* (or *nothing to hold*), or *stack is
not running — nothing to park*.

**Means:** PARK and HOLD command an energized arm; this one is not energized.

**Do:** **ACTIVATE** that arm first, if it should move at all. A de-energized arm is already limp.

### No stow pose is configured {#arm-no-stow-pose}

**Code:** `ARM_NO_STOW_POSE`

**Shown as:** *…: no stow pose configured in /etc/opendubs/openarm-stow.conf*.

**Means:** the robot has not been told where its arms are safe to rest. PARK refuses rather than
guess, because a wrong pose can drive an arm into the robot's head.

**Do:** set the stow pose, per [OpenArm setup § Configuring the stow pose](openarm-setup.md#configuring-the-stow-pose).

### De-energize refused: an arm would fall {#arm-down-would-drop}

**Code:** `ARM_DOWN_WOULD_DROP`

**Shown as:** *refusing — left still active, and 'inactive' removes torque: the arm goes limp and
falls from wherever it is now.*

**Means:** an energized arm is away from its stow pose; de-energizing would drop it.

**Do:** **PARK BOTH**, then **DE-ENERGIZE**. If the arm is already somewhere it can safely drop
from, a shell on the Thor can say so explicitly: `openarm-up down --drop`.

### The arm stack is dead {#arm-stack-dead}

**Code:** `ARM_STACK_DEAD`

**Shown as:** *a DEAD stack is still holding the launch process — the controller_manager does not
answer.*

**Means:** the arm stack crashed but did not fully exit.

**Do:** **DE-ENERGIZE** (it clears a dead stack), then **ACTIVATE**. If DE-ENERGIZE refuses because
motors are still energized with no controller, follow the options it prints — the arms hold their
last command until something disables them.

### The CAN links are not ready {#arm-can-not-ready}

**Code:** `ARM_CAN_NOT_READY`

**Shown as:** *CAN links not ready — nothing launched*.

**Means:** the arms' two CAN interfaces are missing or down, so the arm stack cannot reach the
motors.

**Do:** see [OpenArm setup § Troubleshooting](openarm-setup.md#troubleshooting): is the adapter
plugged in, is its driver installed, is each link `ERROR-ACTIVE`.

### The arm stack did not start {#arm-stack-start-failed}

**Code:** `ARM_STACK_START_FAILED`

**Shown as:** *arms did not reach 'inactive' in 30 s*, *launch exited*, *no 'openarm' container*, or
*container would not start*.

**Means:** the arm stack could not start. The first two point at the stack itself, the last two at
the robot's provisioning.

**Do:** check `openarm-up status` and `openarm-up logs` on the Thor, and
[OpenArm setup § Troubleshooting](openarm-setup.md#troubleshooting).

### An arm came up active by itself {#arm-came-up-active}

**Code:** `ARM_CAME_UP_ACTIVE`

**Shown as:** *an arm came up ACTIVE by itself — the controller config no longer starts arms
inactive. stopping the stack now.*

**Means:** the arm stack's configuration changed so that it energizes arms on launch. Activation
moves an arm, so it must always be a deliberate step; `openarm-up` stops the stack.

**Do:** restore `hardware_components_initial_state: inactive` in the controller configuration; see
[OpenArm setup § Start the arms inactive](openarm-setup.md#start-the-arms-inactive).

### Permission denied {#arm-sudo-denied}

**Code:** `ARM_SUDO_DENIED`

**Shown as:** *sudo: a password is required*.

**Means:** the dashboard is not allowed to run the arm commands. Its sudoers rule is missing.

**Do:** install the dashboard's sudoers rule (`kiosk-dashboard/deploy/opendubs-sudoers`).

### Timed out {#arm-op-timeout}

**Code:** `ARM_OP_TIMEOUT`

**Shown as:** *activate timed out after 180s* (the action and its limit vary).

**Do:** check `openarm-up status` on the Thor before pressing anything else: the action may have
partly completed.

### Teleop would not start or stop {#arm-service-failed}

**Code:** `ARM_SERVICE_FAILED`

**Shown as:** *systemctl start failed: …* or *systemctl stop returned …* under TELEOP.

**Means:** the dashboard could not start or stop the teleop bridge's service.

**Do:** check `systemctl status opendubs-teleop` and `journalctl -u opendubs-teleop` on the Thor. A
*password is required* here is [`ARM_SUDO_DENIED`](#arm-sudo-denied).

## Teleop messages

These appear on the TELEOP page's **SESSION** panel, within a second of happening. Each hand has a
tile: a filled dot while its pose data is arriving, **GRIPPING** while its grip drives the arm,
**HOMING** while X or A returns it to stow, and a red border while something is holding it back.
Under the tiles is what that is, with a **?** that opens its entry here. A message clears by itself
when its cause does; the last one stays, grey, for 15 s, so a half-second dropout can still be read
after it has passed.

The same messages are in the bridge's log, `journalctl -u opendubs-teleop` on the Thor.

### Teleop exits about 30 s after START {#arm-teleop-gripper-timeout}

**Code:** `ARM_TELEOP_GRIPPER_TIMEOUT`

**Shown as:** the SERVER badge drops back to STOPPED, and SESSION reads *last run ended: … Timed out
waiting for left gripper action server*.

**Means:** an arm's gripper controller is not running. The bridge needs both.

**Do:** **ACTIVATE** both arms (which starts their gripper controllers), then **START** again.

### Grip refused: CH5 is not MANUAL {#arm-teleop-grip-refused-mode}

**Code:** `ARM_TELEOP_GRIP_REFUSED_MODE`

**Shown as:** *R: grip refused — CH5 is OFF, teleop needs MANUAL*, for as long as CH5 stays out of
MANUAL. With no grip squeezed, the panel reads *CH5 is OFF — the arms follow only in MANUAL*.

**Do:** set CH5 to MANUAL (the middle position), release the grip, and squeeze it again.

### CH5 left MANUAL mid-grip {#arm-teleop-stopped-mode}

**Code:** `ARM_TELEOP_STOPPED_MODE`

**Shown as:** *R: CH5 is OFF — stopped where it is; set MANUAL, release and re-grip*.

**Do:** set CH5 back to MANUAL, release the grip, and squeeze again.

### A hand stopped streaming {#arm-teleop-tracking-lost}

**Code:** `ARM_TELEOP_TRACKING_LOST`

**Shown as:** *R: tracking lost (no pose data for > 0.50s) — stopped where it is*, and that hand's
tile loses its filled dot.

**Means:** the headset stopped sending that hand's pose for over half a second — the controller
left the headset's view, the headset's menu opened, or the network dropped. The arm stopped where
it was.

**Do:** bring the controller back into view, release the grip, and squeeze again. Frequent losses
with the controller in plain view point at the network.

### Home refused {#arm-teleop-home-refused}

**Code:** `ARM_TELEOP_HOME_REFUSED`

**Shown as:** *R: A held but homing refused — …* (X for the left arm), while the button is held. The
reason is one of: CH5 is not MANUAL; no stow pose for this arm; the grip clutch is engaged; no joint
data for this arm.

**Do:** fix the stated reason — for the grip, release it first, then hold A or X.

### The arm stops following {#arm-teleop-unreachable}

**Code:** `ARM_TELEOP_UNREACHABLE`

**Shown as:** the arm stops while the hand keeps moving, and SESSION reads *R: target out of reach —
the arm holds; move the hand back*, with the diagnosis on a smaller line beneath: *RIGHT
UNREACHABLE: target … | near limits: … | ignoring limits: … | same position, hand tilted 30 deg: …*.

**Means:** the inverse kinematics found no joint configuration for where the hand is asking the arm
to be, so the arm holds. The line says why: a joint at its limit, a target out of reach, or one
reachable only with the hand at a different angle.

**Do:** move the hand back the way it came. If it happens constantly, especially right after
starting, restart the immersive session while facing the robot's forward.

### The page loads but nothing moves {#arm-teleop-cert-not-accepted}

**Code:** `ARM_TELEOP_CERT_NOT_ACCEPTED`

**Shown as:** the teleop page is open in the Quest Browser, but SESSION reads *no pose data yet — is
the teleop page open on the headset?* for both hands (and the log has no *first pose data
received*).

**Means:** the headset has not accepted the certificate on the pose channel's port, which fails
silently.

**Do:** open `https://<robot-address>:8766/` in the Quest Browser once and accept the warning, then
reload the teleop page. See [WebXR teleop § Certificates](teleop-setup.md#certificates).

### No joint data {#arm-joint-data-missing}

**Code:** `ARM_JOINT_DATA_MISSING`

**Shown as:** the TELEOP page's arm view says **no joint data**, or **STALE**, or the SESSION panel
says **NO JOINT DATA**.

**Means:** the page is not receiving the arms' joint states. Check the **ROSBRIDGE** badge at the top
of the page: DOWN means the page lost its connection to the robot's data feed.

**Do:** reload the tablet's page. If the arms are energized but this persists, the robot side can be
checked from a shell: `ros2 topic hz /joint_states` should report about 400 Hz.
