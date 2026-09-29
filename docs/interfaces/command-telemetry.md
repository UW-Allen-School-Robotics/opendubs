# Command & telemetry interface

*(Draft — the contract every control source speaks, and the safety rules around it.)*

The base is driven by a **single failsafe-gated command contract**: a source produces drive and
lift commands, they pass the **StamPLC arm + failsafe gate**, and telemetry streams back.
Swapping control sources never changes the robot's safety behavior — only who's holding the
(virtual) stick.

**Two paths do not pass that gate today** — the arms and the Tab5. See
[what the gate does not cover](#what-the-gate-does-not-cover) below, which is a gap rather than
a design choice.

## Where a command goes

```mermaid
flowchart TB
  subgraph SRC["Sources - interchangeable"]
    L["Laptop"]
    TB["Tablet"]
    DB["Dashboard"]
    AG["Autonomy"]
  end

  SRC --> THOR["Onboard computer - command listener"]

  RC["RC transmitter"] -->|"sticks, CH5 mode, CH6 deadman"| AUTH

  subgraph PLC["StamPLC"]
    AUTH["Authority gate"]
    ST["Robot state"]
  end

  THOR -->|"drive, lift"| AUTH
  AUTH --> DRIVE["Drive"]
  AUTH --> LIFT["Lift"]
  AUTH -->|"pan and tilt forwarded"| TAB5
  ST --> LIGHT["Lights"]

  THOR ==>|"ungated"| TAB5["Tab5 — front panel"]
  THOR ==>|"ungated"| ARMCTL["Arm controllers"]
  ARMCTL --> CAN["CAN bus"]
  CAN --> ARMS["Arms and grippers"]

  TAB5 --> FACE["Face"]
  TAB5 --> HEAD["Head servos"]
  TAB5 --> SOUND["Sound"]

  ST -.-> TAB5
  ST -.-> THOR
  ST -.-> DB
```

Solid is a command, thick is a command that does not pass the authority gate, dashed is state.

Three controllers share the robot. The **StamPLC** (an ESP32-S3 industrial controller) owns the
RC receiver, the robot state, drive mixing, the lift relays, the lights and the e-stop
contactor. The **Tab5** (an ESP32-P4 panel) is the front control panel and the
human–robot interface, and it drives the pan/tilt system: the face screen, the head servos and
the speaker. The **onboard computer** runs ROS 2, forms and sends the arms' CAN
traffic, and hosts a listener that accepts commands from any source.

Three things follow from the drawing:

- **Releasing the deadman stops actuators, not software.** The computer keeps listening and
  keeps sending; the StamPLC stops acting on what arrives.
- **State is an output**, broadcast from the StamPLC to the Tab5, the computer and the
  dashboard. The face and the lights follow state rather than being commanded per device.
- **The Tab5 drives the head servos.** The StamPLC forwards pan and tilt from the
  sticks; the computer can also send them directly.

## Control sources — all speak the same contract

| Source | Role | Notes |
|---|---|---|
| **RC** (2.4 GHz) | Independent safety backstop + manual | Deterministic, low-latency, **long range**, own radio link. |
| **On-robot touchscreen** | Local UI / status | A supported variant of the build. |
| **WiFi tablet** (PWA + gamepad) | Primary UX, teleop, richer display | Reuses the web dashboard; removable/handheld. Shares fate with the network. |
| **Autonomy / LeRobot policy** | Programmatic control | Same command topics — teleop, dataset capture, and learned policies all drive here. |

The base is **designed to fit a tablet**, and an **on-robot touchscreen variant** is also
supported — both are just UIs over this one contract.

## Safety rules (non-negotiable)

1. **Every source is heartbeat-gated.** Lose the heartbeat → motion cuts to neutral (the RC
   failsafe and the StamPLC Ethernet heartbeat watchdog are the existing pattern).
2. **The hardware e-stop is independent of every source.** The physical button → GX11 contactor
   kill path must not depend on RC, WiFi, the tablet, or Thor. Never let the thing you drive
   with be the only thing that can stop you.
3. **RC wins on determinism *and* range.** WiFi teleop is convenient but not a substitute for an
   independent, long-range control/e-stop link — so RC stays as the backstop until (if ever) a
   WiFi watchdog is proven equivalent.
4. **The control path is authenticated.** A source must be trusted (token/cert) before it can
   command motion — the same gap flagged for rosbridge/foxglove/dashboard.

## What the gate does not cover

The e-stop contactor gates the **drive bus** only — the 60&nbsp;A step-down sits behind it and
feeds the power distribution hub and the drive ESCs (see
[Power architecture](../power-architecture.md)). The arms are not on that bus, and the onboard
computer forms and sends their CAN traffic itself. The computer also addresses the Tab5
directly.

So in `MANUAL`, releasing the deadman stops drive and lift, but **not the arms, the head or
sound**. The intent is that the deadman gates motion from every actuator; a replicator should
know that this build does not yet do that, and that closing it is one of two changes:

- **software** — the computer already receives state, so the arm and front-panel paths refuse to
  emit unless authority permits;
- **hardware** — arm power behind a contactor pole, so releasing the deadman removes torque.

## The contract

*TODO:* the actual ROS 2 topics / message types for command + telemetry, the heartbeat rate and
timeout, the arm/deadman semantics, and the auth/pairing scheme. Once specified, RC, the
touchscreen, the tablet PWA, and a policy are all just clients of it.
