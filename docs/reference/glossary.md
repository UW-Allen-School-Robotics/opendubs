# Glossary

The terms the robot, the dashboard and these docs use, in alphabetical order. For what to do
when the robot reports a problem, see [Troubleshooting](troubleshooting.md).

<!-- FAULTS-AUTO:glossary -->
### Abort {#abort}

Ending a routine — a show, or an autonomous task — so that it stays ended. An abort does not pause: once a person has stepped in, the routine does not resume. Compare **stop**, which only commands the robot to stand still, and **e-stop**, which removes drive power.

**See also:** [Control authority and modes](../control-authority.md)

### Arbiter {#arbiter}

The software that decides which command source holds each [lease](#lease), so that two sources never command the same axis at once.

**See also:** [Control authority and modes](../control-authority.md)

### Authority {#authority}

*Also:* **CH5**

Who may command the robot, set by the transmitter's CH5 switch: `OFF` (nobody), `MANUAL` (the sticks) or `AUTO` (the computer). See [CH5](#ch5).

**See also:** [Control authority and modes](../control-authority.md)

### BMS {#bms}

*Also:* **battery management system**

The electronics inside the battery pack that protect its cells and report its state of charge, voltage and temperature. The pack's BMS reports to the StamPLC over RS-485.

**See also:** [Power architecture](../power-architecture.md)

### CH5 {#ch5}

The transmitter's three-position **authority** switch: `OFF`, `MANUAL` or `AUTO`. At `OFF` nothing may command motion. Re-arming after an e-stop requires CH5 at `OFF`.

**See also:** [Operating the robot](../operating.md#the-transmitter) · [Control authority and modes](../control-authority.md)

### Contactor {#contactor}

A heavy-duty, electrically operated switch. The robot's drive contactor connects the battery to the drive bus; every e-stop works by opening it. It is held closed only while every e-stop in the chain is released and the StamPLC holds its relay closed, so losing power opens it too.

**See also:** [E-stop system](../e-stop.md)

### Deadman {#deadman}

*Also:* **CH6**

The transmitter's CH6 control. It must be held for the sticks to move the robot, and releasing it commands neutral. In `AUTO` it must be held too.

**See also:** [Control authority and modes](../control-authority.md#the-deadman)

### DEGRADED {#degraded}

The robot state shown when a fault is latched — an over-temperature, for example. A DEGRADED fault stays shown after its cause has gone, so an operator sees that it happened.

**See also:** [Robot and Subsystem State Machines](../state-machines.md)

### Drive bus {#drive-bus}

The 24 V supply for everything that moves — the drive motors, the lift, the arms. It sits behind the [contactor](#contactor), so an e-stop removes it. Compare the [logic bus](#logic-bus).

**See also:** [Power architecture](../power-architecture.md)

### E-stop {#e-stop}

*Also:* **emergency stop**

Anything that opens the drive [contactor](#contactor). The red buttons on the robot do it in hardware, independent of all software. A **software e-stop**, from the dashboard, has the controller open it, and [latches](#latch) until an operator re-arms.

**See also:** [E-stop system](../e-stop.md) · [Operating the robot](../operating.md#stopping-the-robot)

### Edge controller {#edge-controller}

A small controller that sits between the computer and the hardware and enforces safety locally, so the robot stays safe if the computer stops. The StamPLC is the mobile base's edge controller.

**See also:** [System Overview](../system-overview.md)

### ESC {#esc}

*Also:* **electronic speed controller**

The motor driver that turns a control signal into motor power. The robot's drive motors run from two dual-channel ESCs.

**See also:** [PWM and ESC Safety](../pwm-esc-safety.md)

### Failsafe {#failsafe}

What an RC receiver reports when it has lost the transmitter's signal. The robot treats failsafe as no command.

**See also:** [RC control mapping](../rc-control-mapping.md)

### Head {#head}

*Also:* **the head**

The robot's face: an Android tablet on a pan/tilt neck. The tablet shows the face, plays sound and runs on its own battery; lifted off the mount, it is the operator's dashboard. An M5Dial neck controller moves the neck. The head has no e-stop of its own.

**See also:** [System Overview](../system-overview.md)

### Latch {#latch}

A condition that holds once it is set, until someone deliberately clears it. A software e-stop latches; so does a DEGRADED fault.

### Lease {#lease}

Temporary ownership of an axis — the drive, the lift, an arm. Every lease expires unless its holder keeps renewing it, so a command source that dies stops commanding.

**See also:** [Control authority and modes](../control-authority.md)

### Logic bus {#logic-bus}

The 24 V supply for the robot's thinking parts — the computer, the StamPLC, the router, the sensors. It is switched by **RUN** and is not behind the contactor, so the robot keeps reporting while its actuators are unpowered.

**See also:** [Power architecture](../power-architecture.md) · [Operating the robot](../operating.md#the-controls)

### Mecanum wheel {#mecanum-wheel}

A wheel ringed with angled rollers. Driving four of them in different combinations moves the robot forward, sideways or round on the spot.

**See also:** [Design philosophy](../design-philosophy.md)

### Monitor {#monitor}

Software that constrains whoever is commanding — it can limit or stop motion — but never commands motion itself. The pinch guard is a monitor. A monitor that changes the robot's behaviour must say so.

**See also:** [Control authority and modes](../control-authority.md)

### Payload mode {#payload-mode}

*Also:* **CH7**

The transmitter's CH7 switch: what the right stick talks to — `OFF`, `Z-AXIS` (the lift) or `PAN-TILT`. It is not an enable; authority is CH5 alone.

**See also:** [Operating the robot](../operating.md#the-transmitter)

### PDH {#pdh}

*Also:* **Power Distribution Hub**

A REV Robotics power distribution board with built-in breakers. Some robots use one on the drive bus; new builds use a fuse block instead.

**See also:** [6 · Power Bay](../build/power-bay.md)

### Pinch guard {#pinch-guard}

The lift's [monitor](#monitor) that stops its descent short of the bottom, where the closing gap can trap a hand. Continuing down needs a fresh, deliberate command.

**See also:** [Operating the robot](../operating.md#z-axis-pinch-protection)

### Re-arm {#re-arm}

Closing the drive contactor again after an e-stop. On the dashboard, hold **RE-ARM**. It is guarded: it refuses unless CH5 is at `OFF`, every button is released, the StamPLC is reporting, and the contactor is not welded.

**See also:** [Operating the robot](../operating.md#stopping-the-robot)

### Regen clamp {#regen-clamp}

A circuit that absorbs the energy a motor returns when it brakes, which would otherwise push the drive bus voltage up.

**See also:** [Regen Clamp — Placement & Brake-Resistor Sizing](../regen-clamp-sizing.md)

### StamPLC {#stamplc}

The mobile base's [edge controller](#edge-controller), an M5Stack StamPLC. It reads the RC receiver and the e-stop circuit, holds the contactor, commands the drive and the lift, and runs the pinch guard.

**See also:** [StamPLC connections](../stamplc-connections.md) · [System Overview](../system-overview.md)

### Thor {#thor}

The robot's main computer, an NVIDIA Jetson AGX Thor. It runs the dashboard, the ROS software and the arms.

**See also:** [Thor setup](../thor-setup.md)

### Z-axis {#z-axis}

*Also:* **the lift**

The robot's vertical axis — a scissor lift that raises and lowers the top deck.

**See also:** [Scissor Lift Module](../build/scissor-lift.md)
<!-- /FAULTS-AUTO:glossary -->
