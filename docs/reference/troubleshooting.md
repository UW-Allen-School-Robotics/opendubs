# Troubleshooting

What the robot is telling you, and what to do about it. Find what you are seeing under a heading
below; each heading lists its causes in the order to check them.

Every entry has a short name in capitals, such as `CONTACTOR_OPEN`. That name is stable: the
dashboard shows it beside a refusal or a fault, and its **?** opens the entry here. Terms in the
entries are defined in the [Glossary](glossary.md).

!!! danger "If the robot moves when it should not"
    Press an emergency stop first and troubleshoot afterwards. The red buttons on the rear of the
    robot open the drive contactor in hardware, whatever the software is doing.

<!-- FAULTS-AUTO:troubleshooting -->
## The robot won't power up, or the dashboard won't load {#wont-start}

Check in this order:

1. [Nothing powers up](#power-not-on)
2. [The dashboard won't load](#link-dashboard-unreachable)

### Nothing powers up {#power-not-on}

`POWER_NOT_ON` · Expected behaviour · seen on the robot itself

The robot has two power switches, and it needs both. **BATTERY**, on the battery itself, connects the pack; **RUN**, on the rear face, switches the logic bus. RUN is lit green while the logic bus is up.

**What to do**

1. Turn **BATTERY** on.
2. Turn **RUN** on, and check that it lights green.

**See also:** [Operating the robot](../operating.md#powering-on)

### The dashboard won't load {#link-dashboard-unreachable}

`LINK_DASHBOARD_UNREACHABLE` · Fault · seen on the dashboard

The tablet cannot reach the dashboard, which the robot's computer serves. The computer is the slowest part of the robot to start, so the dashboard is the last thing to appear after power-on.

**What to do**

1. Give the computer time to finish booting.
2. Check that the tablet is on the robot's network.
3. Check that **RUN** is on — the computer and the router run from the logic bus.

**See also:** [Operating the robot](../operating.md#powering-on) · [Network architecture](../network-architecture.md)

## The robot won't drive {#wont-drive}

The drive is gated by several conditions at once, and the controller re-checks every one of them continuously. The dashboard names the condition that is stopping it; find that one below, or work down the list.

Check in this order:

1. [The drive contactor is open](#contactor-open)
2. [An emergency stop button is pressed](#estop-button)
3. [A software e-stop is latched](#estop-software)
4. [The contactor is welded shut](#contactor-welded)
5. [No link to the StamPLC](#link-stamplc-lost)
6. [No RC link](#rc-no-link)
7. [The RC receiver is in failsafe](#rc-failsafe)
8. [CH5 is at OFF](#rc-authority-off)
9. [The deadman is released](#drive-deadman)
10. [Arming — hold the sticks at neutral](#drive-arming)
11. [The robot is still settling](#drive-not-ready)
12. [Not ready after 30 seconds](#link-not-ready)
13. [The charger is connected](#drive-charger-connected)

### The drive contactor is open {#contactor-open}

`CONTACTOR_OPEN` · Warning · seen on the dashboard, the head, the status lights

The drive [contactor](glossary.md#contactor) is open, so the drive bus is unpowered: no wheel, lift or other drive-bus load can move. That includes the head, which is powered from the drive bus and turns off. The computer, the controller and the router stay on — they run from the logic bus, which the contactor does not switch.

**What to do**

1. If the dashboard reports an e-stop, deal with that first — the entries below say how for each source.
2. After any e-stop the contactor stays open until an operator re-arms: set [CH5](glossary.md#ch5) to `OFF`, then hold **RE-ARM** on the dashboard.
3. At power-on the robot closes the contactor by itself a few seconds after boot, but only with CH5 at `OFF`. While it waits, the status lights strobe amber.
4. You cannot re-arm from the head: it is off while the contactor is open. Use the dashboard.

**See also:** [Operating the robot](../operating.md#stopping-the-robot) · [E-stop system](../e-stop.md)

### An emergency stop button is pressed {#estop-button}

`ESTOP_BUTTON` · Fault · seen on the dashboard, the robot itself

A physical [e-stop](glossary.md#e-stop) is pressed, which opens the contactor directly, in hardware, whatever the software is doing. The controller senses the e-stop circuit separately, so a circuit that is open for any other reason reads the same: a payload's own e-stop wired in series, or a broken conductor in the e-stop wiring.

**What to do**

1. Find the pressed button — there are two on the rear of the robot, left and right, plus any e-stop a payload has added — and twist it to release.
2. Re-arm: set CH5 to `OFF`, then hold **RE-ARM** on the dashboard. Releasing the button does not re-close the contactor by itself.
3. If no button is pressed and this persists, the e-stop circuit is open somewhere else. Check the payload e-stop jumpers and the e-stop wiring.

**See also:** [E-stop system](../e-stop.md) · [Operating the robot](../operating.md#stopping-the-robot)

### A software e-stop is latched {#estop-software}

`ESTOP_SOFTWARE` · Fault · seen on the dashboard

Someone pressed **E-STOP** on the dashboard or on the head, and the controller opened the contactor. A software e-stop [latches](glossary.md#latch): it stays in force until an operator re-arms, and nothing clears it on its own.

**What to do**

1. Make sure it is safe for the robot to move again.
2. Set CH5 to `OFF`.
3. Hold **RE-ARM** on the dashboard. The head cannot re-arm: it is powered behind the contactor, so it is off until the dashboard has re-armed.

**See also:** [Operating the robot](../operating.md#stopping-the-robot)

### The contactor is welded shut {#contactor-welded}

`CONTACTOR_WELDED` · Fault · seen on the dashboard

The contactor's auxiliary contact reports its main contacts closed when they were commanded open. The e-stop works by opening this contactor, so **treat the robot as having no working e-stop** until this is resolved. A fault in the auxiliary-contact wiring reports the same thing.

**What to do**

1. Do not drive the robot.
2. Turn the **BATTERY** switch off. With the contactor welded, that is the way to remove drive power.
3. Check the auxiliary-contact wiring first; if it is sound, replace the contactor.
4. This fault cannot be cleared from the dashboard. After the repair, restart the robot.

**See also:** [E-stop system](../e-stop.md) · [6 · Power Bay](../build/power-bay.md)

### No link to the StamPLC {#link-stamplc-lost}

`LINK_STAMPLC_LOST` · Fault · seen on the dashboard

The dashboard has stopped hearing from the [StamPLC](glossary.md#stamplc), the controller that owns the e-stop, the RC receiver, the drive and the lift. With its telemetry stale the dashboard cannot know the safety state, so it shows the robot as e-stopped (source **STALE TELEMETRY**), refuses to re-arm, and does not send lift or relay commands. The e-stop buttons and the transmitter act on the StamPLC directly and do not depend on this link.

**What to do**

1. Check that **RUN** is on and the StamPLC's screen is lit.
2. If its firmware was just updated or it was restarted, wait — the link returns by itself once it is back up.
3. Check the network cable between the StamPLC and the computer, and the router.

**See also:** [StamPLC ↔ Thor over Ethernet (PoE UDP)](../stamplc-ethernet-transport.md) · [Operating the robot](../operating.md#the-controls)

### No RC link {#rc-no-link}

`RC_NO_LINK` · Fault · seen on the dashboard, the head

The receiver on the robot is hearing nothing from the transmitter. Without a link nothing moves, whatever the switches show.

**What to do**

1. Turn the transmitter on, and check its battery.
2. Move closer to the robot.
3. If the transmitter is on and close, check the receiver and its cable to the StamPLC.

**See also:** [RC receiver wiring](../build/rc-receiver.md) · [RC control mapping](../rc-control-mapping.md)

### The RC receiver is in failsafe {#rc-failsafe}

`RC_FAILSAFE` · Fault · seen on the dashboard, the head

The receiver has lost the transmitter's signal and is reporting [failsafe](glossary.md#failsafe) instead of live stick positions. The robot treats this as no command.

**What to do**

1. Check that the transmitter is on, charged and within range.
2. If it persists with the transmitter close by, check the receiver and its cable to the StamPLC.

**See also:** [RC receiver wiring](../build/rc-receiver.md)

### CH5 is at OFF {#rc-authority-off}

`RC_AUTHORITY_OFF` · Expected behaviour · seen on the dashboard, the head, the transmitter

[CH5](glossary.md#ch5) is the authority switch. At `OFF` nobody may command motion — not the sticks and not the computer. The dashboard shows this as **STANDBY**.

**What to do**

1. Set CH5 to `MANUAL` to drive with the sticks.
2. Set CH5 to `AUTO` to let the computer drive. `AUTO` also needs the deadman held.

**See also:** [Control authority and modes](../control-authority.md) · [Operating the robot](../operating.md#making-it-move)

### The deadman is released {#drive-deadman}

`DRIVE_DEADMAN` · Expected behaviour · seen on the dashboard, the transmitter

[CH6](glossary.md#deadman) is the deadman. The sticks do nothing unless it is held, and releasing it commands neutral. In `AUTO` it must be held too.

**What to do**

1. Hold CH6 while you drive.

**See also:** [Control authority and modes](../control-authority.md#the-deadman)

### Arming — hold the sticks at neutral {#drive-arming}

`DRIVE_ARMING` · Expected behaviour · seen on the dashboard

The drive arms only after the sticks have been at neutral for two seconds with every other condition met. If any condition drops out, the two seconds start again — so an intermittent RC link, or a deadman that slips, keeps the drive disarmed even though every switch looks right.

**What to do**

1. Centre both sticks and wait two seconds.
2. If it never arms, work through the RC link entries above.

**See also:** [Operating the robot](../operating.md#making-it-move)

### The robot is still settling {#drive-not-ready}

`DRIVE_NOT_READY` · Expected behaviour · seen on the dashboard

After power-on, or after the StamPLC restarts — a firmware update restarts it — its control loop takes a moment to start keeping time, and the dashboard holds motion off until it does. The dashboard shows **ROBOT NOT READY**.

**What to do**

1. Wait. It clears by itself.
2. If it lasts longer than about 30 seconds, see *Not ready after 30 seconds*.

**See also:** [Robot and Subsystem State Machines](../state-machines.md)

### Not ready after 30 seconds {#link-not-ready}

`LINK_NOT_READY` · Fault · seen on the dashboard

The dashboard declares the robot ready only once it has both fresh StamPLC telemetry and a live RC link. If it has not had both within 30 seconds of starting, it raises **TELEMETRY NOT READY** rather than declaring the robot ready.

**What to do**

1. Work through *No link to the StamPLC*.
2. Work through *No RC link*.
3. It clears by itself once both are present.

**See also:** [Robot and Subsystem State Machines](../state-machines.md)

### The charger is connected {#drive-charger-connected}

`DRIVE_CHARGER_CONNECTED` · Expected behaviour · seen on the dashboard

While the charger is plugged in, the drive is inhibited, so the robot cannot drive off while tethered to it.

**What to do**

1. Unplug the charger.

**See also:** [Operating the robot](../operating.md#charging)

## Re-arm is refused {#rearm-refused}

Re-arming closes the drive contactor, so the dashboard guards it and lists every reason it refused. Clear each one, then re-arm again.

Check in this order:

1. [Re-arm needs CH5 at OFF](#estop-rearm-ch5)
2. [An emergency stop button is pressed](#estop-button)
3. [The contactor is welded shut](#contactor-welded)
4. [No link to the StamPLC](#link-stamplc-lost)

### Re-arm needs CH5 at OFF {#estop-rearm-ch5}

`ESTOP_REARM_CH5` · Refused action · seen on the dashboard

Re-arming closes the contactor, and the dashboard will not do that while the transmitter grants [authority](glossary.md#authority) — the robot could move the instant drive power returned. This is the most common refusal.

**What to do**

1. Set CH5 to `OFF`.
2. Hold **RE-ARM** again.
3. Set CH5 back to `MANUAL` when you are ready to drive.

**See also:** [Operating the robot](../operating.md#stopping-the-robot) · [Control authority and modes](../control-authority.md)

## The head is dark or offline {#head-dark}

The head is the robot's face and front control panel. Check the contactor before anything else: an open contactor turns the head off, and that looks exactly like a network fault.

Check in this order:

1. [The drive contactor is open](#contactor-open)

## The lift won't move {#lift-wont-move}

The lift answers to the same safety gate as the drive, plus its own pinch guard near the bottom of its travel.

Check in this order:

1. [The lift stopped near the bottom](#lift-pinch-hold)
2. [The drive contactor is open](#contactor-open)
3. [CH7 is not set to Z-AXIS](#lift-ch7-not-zaxis)
4. [No link to the StamPLC](#link-stamplc-lost)
5. [No RC link](#rc-no-link)
6. [The RC receiver is in failsafe](#rc-failsafe)
7. [CH5 is at OFF](#rc-authority-off)
8. [The lift is not reporting](#lift-not-responding)
9. [Relays 1 and 2 belong to the lift](#lift-relay-reserved)

### The lift stopped near the bottom {#lift-pinch-hold}

`LIFT_PINCH_HOLD` · Expected behaviour · seen on the head, the robot itself

The [pinch guard](glossary.md#pinch-guard) arrests the lift's descent short of the bottom, where the closing gap can trap a hand. The head shows an amber **Z-AXIS HELD** banner and two short tones sound. Nothing is latched and nothing needs clearing.

**What to do**

1. Release the stick, and keep it released for about half a second.
2. Command down again. The banner turns green and reads **LOWERING**.

**See also:** [Operating the robot](../operating.md#z-axis-pinch-protection) · [Scissor Lift Module](../build/scissor-lift.md)

### CH7 is not set to Z-AXIS {#lift-ch7-not-zaxis}

`LIFT_CH7_NOT_ZAXIS` · Expected behaviour · seen on the transmitter

[CH7](glossary.md#payload-mode) chooses what the right stick talks to: `OFF`, `Z-AXIS` or `PAN-TILT`. Unless it is at `Z-AXIS`, the right stick does not move the lift.

**What to do**

1. Set CH7 to `Z-AXIS`.

**See also:** [Operating the robot](../operating.md#the-transmitter)

### The lift is not reporting {#lift-not-responding}

`LIFT_NOT_RESPONDING` · Fault · seen on the dashboard

The StamPLC link is healthy, but its report of the lift — height, motion and the pinch guard's state — has stopped, so the dashboard marks the robot [DEGRADED](glossary.md#degraded).

**What to do**

1. Do not move the lift until it reports again; the pinch guard's state is unknown.
2. The fault stays shown after the report returns. Restarting the robot clears it.

**See also:** [Scissor Lift Module](../build/scissor-lift.md)

### Relays 1 and 2 belong to the lift {#lift-relay-reserved}

`LIFT_RELAY_RESERVED` · Refused action · seen on the dashboard

StamPLC relays 1 and 2 drive the lift's two legs, up and down. Switching them directly would bypass the pinch guard, the travel limits and the authority gate, so the dashboard refuses.

**What to do**

1. Move the lift with the lift controls, which go through all three.

**See also:** [Scissor Lift Module](../build/scissor-lift.md) · [StamPLC connections](../stamplc-connections.md)

## The arms won't activate {#arms-wont-activate}

Check in this order:

1. [ACTIVATE recovered the arm after a power loss](#arm-power-restored)
2. [Another arm action is running](#arm-op-running)
3. [The dashboard is not allowed to run the arm commands](#arm-sudo-denied)
4. [The arms' CAN links are not ready](#arm-can-not-ready)
5. [The arm stack did not start](#arm-stack-start-failed)
6. [Some arm motors do not answer](#arm-motors-silent)
7. [An arm motor reports a fault](#arm-motor-fault)
8. [The arm's motors answer but are disabled](#arm-motors-disabled)
9. [The arm could not be reset after a power loss](#arm-reset-failed)
10. [Only some of an arm's motors enabled](#arm-partial-enable)
11. [An arm's controller would not start](#arm-controller-inactive)
12. [An arm's gripper controller would not start](#arm-gripper-controller-inactive)
13. [The arm stack is dead](#arm-stack-dead)
14. [An arm came up active by itself](#arm-came-up-active)
15. [An arm action timed out](#arm-op-timeout)

### ACTIVATE recovered the arm after a power loss {#arm-power-restored}

`ARM_POWER_RESTORED` · Information · seen on the dashboard

After an e-stop, or any loss of the arms' power, the motors come back disabled while the arm stack still has the arm marked active. ACTIVATE recognises that state — every motor answering, none enabled, none faulted — resets just that arm, and activates it as usual.

**What to do**

1. Nothing. The press already recovered the arm; its output says so before the normal activation.

**See also:** [Arm controls on the tablet](../arm-dashboard.md#arm-power-restored)

### Another arm action is running {#arm-op-running}

`ARM_OP_RUNNING` · Refused action · seen on the dashboard

One arm action runs at a time, because two would be two commands fighting over the same arm. This one was requested while another was running, usually from another screen such as the Shows page.

**What to do**

1. Wait for the running action to finish — its button counts its seconds — then press again.

**See also:** [Arm controls on the tablet](../arm-dashboard.md#arm-op-running)

### The dashboard is not allowed to run the arm commands {#arm-sudo-denied}

`ARM_SUDO_DENIED` · Fault · seen on the dashboard

The arm buttons run `openarm-up` and the teleop service through sudo, and the dashboard's sudoers rule is missing, so every press reports *sudo: a password is required*.

**What to do**

1. Install the dashboard's sudoers rule from `software/kiosk-dashboard/deploy/opendubs-sudoers`.

**See also:** [Arm controls on the tablet](../arm-dashboard.md#arm-sudo-denied) · [OpenArm setup](../openarm-setup.md#running-teleop-from-the-dashboard)

### The arms' CAN links are not ready {#arm-can-not-ready}

`ARM_CAN_NOT_READY` · Fault · seen on the dashboard

The arms' two CAN interfaces are missing or down, so the arm stack cannot reach the motors and starts nothing.

**What to do**

1. Check that the CAN adapter is plugged in and its driver is installed.
2. Check that each link reports `ERROR-ACTIVE`; restart a link that does not.

**See also:** [Arm controls on the tablet](../arm-dashboard.md#arm-can-not-ready) · [OpenArm setup](../openarm-setup.md#troubleshooting)

### The arm stack did not start {#arm-stack-start-failed}

`ARM_STACK_START_FAILED` · Fault · seen on the dashboard, the logs

The arm software could not start: its container is missing or would not start, or it launched and exited, or the arms did not report inactive within 30 s.

**What to do**

1. On the robot's computer, run `openarm-up status` and `openarm-up logs` to see which.

**See also:** [Arm controls on the tablet](../arm-dashboard.md#arm-stack-start-failed) · [OpenArm setup](../openarm-setup.md#troubleshooting)

### Some arm motors do not answer {#arm-motors-silent}

`ARM_MOTORS_SILENT` · Fault · seen on the dashboard

Those motors are unpowered, or the CAN wiring is broken at or before the first silent one. The gripper is last in each arm's chain.

**What to do**

1. Check that the e-stop is released and the drive contactor is closed — the arms are on the drive bus.
2. Press ACTIVATE again.
3. If they still do not answer, `openarm-motors status` on the robot's computer lists the silent motors.

**See also:** [Arm controls on the tablet](../arm-dashboard.md#arm-motors-silent) · [E-stop system](../e-stop.md)

### An arm motor reports a fault {#arm-motor-fault}

`ARM_MOTOR_FAULT` · Fault · seen on the dashboard

A motor reports a fault such as over-voltage. A motor's fault latches until the motor loses power.

**What to do**

1. Press and release the e-stop to power-cycle the arms, re-arm, then press ACTIVATE again.

**See also:** [Arm controls on the tablet](../arm-dashboard.md#arm-motor-fault)

### The arm's motors answer but are disabled {#arm-motors-disabled}

`ARM_MOTORS_DISABLED` · Fault · seen on the dashboard

The arms lost power and got it back, and the motors restarted disabled under an arm the stack still marks active — a case the automatic reset did not cover.

**What to do**

1. Press **DE-ENERGIZE** and tap it again to confirm, then press **ACTIVATE** for that arm.

**See also:** [Arm controls on the tablet](../arm-dashboard.md#arm-motors-disabled)

### The arm could not be reset after a power loss {#arm-reset-failed}

`ARM_RESET_FAILED` · Fault · seen on the dashboard

ACTIVATE tried to reset the arm after a power loss, and the arm did not go inactive.

**What to do**

1. Press **DE-ENERGIZE** and tap it again to confirm, then press **ACTIVATE** for that arm.

**See also:** [Arm controls on the tablet](../arm-dashboard.md#arm-reset-failed)

### Only some of an arm's motors enabled {#arm-partial-enable}

`ARM_PARTIAL_ENABLE` · Fault · seen on the dashboard

The arm's hardware went active, but not all eight of its motors enabled.

**What to do**

1. Press **DE-ENERGIZE**, tap again to confirm, then **ACTIVATE**.
2. If it happens again, power-cycle the arms with the e-stop first.

**See also:** [Arm controls on the tablet](../arm-dashboard.md#arm-partial-enable)

### An arm's controller would not start {#arm-controller-inactive}

`ARM_CONTROLLER_INACTIVE` · Fault · seen on the dashboard

The arm energized, but its controller is inactive or not loaded, so it cannot be commanded.

**What to do**

1. Press **DE-ENERGIZE**, then **ACTIVATE**.
2. If it repeats, `openarm-up logs` on the robot's computer says why.

**See also:** [Arm controls on the tablet](../arm-dashboard.md#arm-controller-inactive)

### An arm's gripper controller would not start {#arm-gripper-controller-inactive}

`ARM_GRIPPER_CONTROLLER_INACTIVE` · Warning · seen on the dashboard

The arm works, but its gripper controller is not running. The teleop bridge needs both grippers' controllers and exits about 30 s after START without them.

**What to do**

1. Press **DE-ENERGIZE**, then **ACTIVATE** that arm again.

**See also:** [Arm controls on the tablet](../arm-dashboard.md#arm-gripper-controller-inactive)

### The arm stack is dead {#arm-stack-dead}

`ARM_STACK_DEAD` · Fault · seen on the dashboard

The arm software crashed but did not fully exit, so nothing answers it.

**What to do**

1. Press **DE-ENERGIZE**, which clears a dead stack, then **ACTIVATE**.
2. If DE-ENERGIZE refuses because motors are still energized with no controller, follow the options it prints: the motors hold their last command until something disables them.

**See also:** [Arm controls on the tablet](../arm-dashboard.md#arm-stack-dead)

### An arm came up active by itself {#arm-came-up-active}

`ARM_CAME_UP_ACTIVE` · Fault · seen on the dashboard

The arm software's configuration energizes arms on launch. Activation moves an arm, so it must always be a deliberate step, and `openarm-up` stops the stack instead.

**What to do**

1. Restore `hardware_components_initial_state` to `inactive` in the arm controller configuration.

**See also:** [Arm controls on the tablet](../arm-dashboard.md#arm-came-up-active) · [OpenArm setup](../openarm-setup.md#start-the-arms-inactive)

### An arm action timed out {#arm-op-timeout}

`ARM_OP_TIMEOUT` · Fault · seen on the dashboard

The action did not finish within its time limit. It may have partly completed.

**What to do**

1. Run `openarm-up status` on the robot's computer before pressing anything else.

**See also:** [Arm controls on the tablet](../arm-dashboard.md#arm-op-timeout)

## An arm won't park, hold or power down {#arms-wont-park}

Check in this order:

1. [Another arm action is running](#arm-op-running)
2. [The dashboard is not allowed to run the arm commands](#arm-sudo-denied)
3. [There is nothing to park or hold](#arm-not-active)
4. [No stow pose is configured](#arm-no-stow-pose)
5. [De-energize refused, because an arm would fall](#arm-down-would-drop)
6. [The arm stack is dead](#arm-stack-dead)
7. [An arm action timed out](#arm-op-timeout)

### There is nothing to park or hold {#arm-not-active}

`ARM_NOT_ACTIVE` · Refused action · seen on the dashboard

PARK and HOLD command an energized arm, and this one is not energized — or the arm stack is not running at all. A de-energized arm is already limp.

**What to do**

1. Press **ACTIVATE** for that arm first, if it should move at all.

**See also:** [Arm controls on the tablet](../arm-dashboard.md#arm-not-active)

### No stow pose is configured {#arm-no-stow-pose}

`ARM_NO_STOW_POSE` · Refused action · seen on the dashboard

The robot has not been told where its arms are safe to rest. PARK refuses rather than guess, because a wrong pose can drive an arm into the robot's own head.

**What to do**

1. Set the stow pose in the robot's stow file.

**See also:** [Arm controls on the tablet](../arm-dashboard.md#arm-no-stow-pose) · [OpenArm setup](../openarm-setup.md#configuring-the-stow-pose)

### De-energize refused, because an arm would fall {#arm-down-would-drop}

`ARM_DOWN_WOULD_DROP` · Refused action · seen on the dashboard

An energized arm is away from its stow pose. De-energizing removes its torque, and it would fall from wherever it is.

**What to do**

1. Press **PARK BOTH**, then **DE-ENERGIZE**.
2. If the arm is already somewhere it can safely drop from, say so explicitly from the robot's computer: `openarm-up down --drop`.

**See also:** [Arm controls on the tablet](../arm-dashboard.md#arm-down-would-drop) · [OpenArm setup](../openarm-setup.md#bringing-the-arms-down-without-dropping-them)

## Teleop doesn't move the arm {#teleop-wont-move}

Check in this order:

1. [Teleop would not start or stop](#arm-service-failed)
2. [The dashboard is not allowed to run the arm commands](#arm-sudo-denied)
3. [Teleop exits about 30 s after START](#arm-teleop-gripper-timeout)
4. [The teleop page loads but nothing reaches the robot](#arm-teleop-cert-not-accepted)
5. [Teleop refused the grip — CH5 is not MANUAL](#arm-teleop-grip-refused-mode)
6. [The arm stopped when CH5 left MANUAL](#arm-teleop-stopped-mode)
7. [A hand stopped streaming](#arm-teleop-tracking-lost)
8. [Holding A or X did not send the arm home](#arm-teleop-home-refused)
9. [The arm stops following the hand](#arm-teleop-unreachable)
10. [The tablet shows no joint data](#arm-joint-data-missing)
11. [An arm's gripper controller would not start](#arm-gripper-controller-inactive)

### Teleop would not start or stop {#arm-service-failed}

`ARM_SERVICE_FAILED` · Fault · seen on the dashboard

The dashboard could not start or stop the teleop bridge's service.

**What to do**

1. Check `systemctl status opendubs-teleop` and `journalctl -u opendubs-teleop` on the robot's computer.

**See also:** [Arm controls on the tablet](../arm-dashboard.md#arm-service-failed)

### Teleop exits about 30 s after START {#arm-teleop-gripper-timeout}

`ARM_TELEOP_GRIPPER_TIMEOUT` · Fault · seen on the dashboard, the logs

An arm's gripper controller is not running, and the teleop bridge needs both. Its log ends with *Timed out waiting for left gripper action server*.

**What to do**

1. Press **ACTIVATE** for both arms, which starts their gripper controllers, then **START** again.

**See also:** [Arm controls on the tablet](../arm-dashboard.md#arm-teleop-gripper-timeout)

### The teleop page loads but nothing reaches the robot {#arm-teleop-cert-not-accepted}

`ARM_TELEOP_CERT_NOT_ACCEPTED` · Fault · seen on the dashboard, the logs

The headset has not accepted the teleop certificate on the pose channel's port. The browser warns only for the page, so a refused pose channel fails silently: the teleop bridge's log never reports *first pose data received*.

**What to do**

1. In the Quest Browser, open the pose channel's address once and accept the warning, then reload the teleop page.

**See also:** [Arm controls on the tablet](../arm-dashboard.md#arm-teleop-cert-not-accepted) · [WebXR teleop](../teleop-setup.md#certificates)

### Teleop refused the grip — CH5 is not MANUAL {#arm-teleop-grip-refused-mode}

`ARM_TELEOP_GRIP_REFUSED_MODE` · Refused action · seen on the dashboard, the logs

Teleop moves an arm only with [CH5](glossary.md#ch5) in MANUAL, the middle position.

**What to do**

1. Set CH5 to MANUAL, release the grip, and squeeze it again.

**See also:** [Arm controls on the tablet](../arm-dashboard.md#arm-teleop-grip-refused-mode) · [Control authority and modes](../control-authority.md)

### The arm stopped when CH5 left MANUAL {#arm-teleop-stopped-mode}

`ARM_TELEOP_STOPPED_MODE` · Expected behaviour · seen on the dashboard, the logs

CH5 left MANUAL during a grip, so the arm stopped where it was. Holding the grip through the switch does not resume.

**What to do**

1. Set CH5 back to MANUAL, release the grip, and squeeze again.

**See also:** [Arm controls on the tablet](../arm-dashboard.md#arm-teleop-stopped-mode)

### A hand stopped streaming {#arm-teleop-tracking-lost}

`ARM_TELEOP_TRACKING_LOST` · Expected behaviour · seen on the dashboard, the logs

The headset sent no pose for that hand for over half a second — the controller left the headset's view, the headset's menu opened, or the network dropped — so the arm stopped where it was.

**What to do**

1. Bring the controller back into view, release the grip, and squeeze again.
2. Frequent losses with the controller in plain view point at the network.

**See also:** [Arm controls on the tablet](../arm-dashboard.md#arm-teleop-tracking-lost)

### Holding A or X did not send the arm home {#arm-teleop-home-refused}

`ARM_TELEOP_HOME_REFUSED` · Refused action · seen on the dashboard, the logs

Home needs CH5 in MANUAL, a stow pose configured for that arm, the grip released, and the arm's joint readings. The log names which was missing.

**What to do**

1. Fix the stated reason. For the grip, release it first, then hold A or X.

**See also:** [Arm controls on the tablet](../arm-dashboard.md#arm-teleop-home-refused)

### The arm stops following the hand {#arm-teleop-unreachable}

`ARM_TELEOP_UNREACHABLE` · Refused action · seen on the dashboard, the logs

The arm's inverse kinematics found no joint configuration for where the hand is asking the arm to be, so the arm holds. The bridge's log says why: a joint at its limit, a target out of reach, or one reachable only with the hand at another angle.

**What to do**

1. Move the hand back the way it came.
2. If it happens constantly, especially right after starting, restart the immersive session while facing the robot's forward.

**See also:** [Arm controls on the tablet](../arm-dashboard.md#arm-teleop-unreachable) · [WebXR teleop](../teleop-setup.md#known-limits)

### The tablet shows no joint data {#arm-joint-data-missing}

`ARM_JOINT_DATA_MISSING` · Refused action · seen on the dashboard

The TELEOP page is not receiving the arms' joint states, so its arm view is blank or stale.

**What to do**

1. Check the **ROSBRIDGE** badge at the top of the page. DOWN means the page lost its data connection.
2. Reload the tablet's page.

**See also:** [Arm controls on the tablet](../arm-dashboard.md#arm-joint-data-missing)

## A power or temperature warning {#power-warning}

Check in this order:

1. [The electronics cycle on and off while charging](#power-charging-pack-off)
2. [The battery is low](#power-battery-low)
3. [Part of the robot is over temperature](#power-overtemp)
4. [The power distribution hub reported a brownout](#power-pdh-brownout)
5. [The power distribution hub reported a hardware fault](#power-pdh-fault)

### The electronics cycle on and off while charging {#power-charging-pack-off}

`POWER_CHARGING_PACK_OFF` · Warning · seen on the robot itself

The **BATTERY** switch is off while the charger is connected. The charger's output then reaches the logic bus with no pack in the circuit, and a charger cannot hold its voltage against that load, so the electronics brown out and restart over and over.

**What to do**

1. Turn **BATTERY** on whenever the charger is connected.

**See also:** [Operating the robot](../operating.md#charging)

### The battery is low {#power-battery-low}

`POWER_BATTERY_LOW` · Warning · seen on the dashboard

The pack's state of charge, as its [BMS](glossary.md#bms) reports it, is low. When it is very low the dashboard says **RETURN**.

**What to do**

1. Bring the robot back and charge it.

**See also:** [Operating the robot](../operating.md#charging)

### Part of the robot is over temperature {#power-overtemp}

`POWER_OVERTEMP` · Fault · seen on the dashboard

One of the robot's internal temperature sensors reads 70 °C or more, so the dashboard marks the robot [DEGRADED](glossary.md#degraded).

**What to do**

1. Stop and let the robot cool.
2. Check that the cooling fan runs and that its airflow is not blocked. The dashboard's thermal view shows which zone is hot.
3. The fault stays shown after the temperature falls. Restarting the robot clears it.

**See also:** [8 · Cooling Fan Integration](../build/cooling-fan.md)

### The power distribution hub reported a brownout {#power-pdh-brownout}

`POWER_PDH_BROWNOUT` · Fault · seen on the dashboard

Only on a robot built with the REV Power Distribution Hub ([PDH](glossary.md#pdh)); a robot built with a fuse block never shows it. The hub saw its input voltage dip too low, and the dashboard marks the robot DEGRADED.

**What to do**

1. Check the pack's charge on the dashboard.
2. Check that the hub's input connections are tight.
3. The fault stays shown after the voltage recovers. Restarting the robot clears it.

**See also:** [6 · Power Bay](../build/power-bay.md)

### The power distribution hub reported a hardware fault {#power-pdh-fault}

`POWER_PDH_FAULT` · Fault · seen on the dashboard

Only on a robot built with the REV PDH. The hub reports an internal hardware fault. The PDH is designed for 12 V systems and here runs on the 24 V drive bus, which is why new builds use a fuse block instead.

**What to do**

1. Stop the robot and turn the **BATTERY** switch off.
2. Inspect the hub, and replace it with a fuse block.
3. This fault cannot be cleared from the dashboard. After the repair, restart the robot.

**See also:** [6 · Power Bay](../build/power-bay.md)
<!-- /FAULTS-AUTO:troubleshooting -->

## Adding an entry

This page is generated from the registry in `docs/reference/registry/` — one YAML file per area of
the robot, plus the symptom list. To add or change an entry, edit the YAML and run
`tools/faults.py --inject`; never edit the generated part of this page by hand.
`tools/faults.py --check` fails if the page is out of date, or if the dashboard uses a name the
registry does not have.
