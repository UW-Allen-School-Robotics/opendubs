# RC control mapping

<figure markdown="span">
  ![RC transmitter control mapping](assets/diagrams/rc-control-mapping.png)
  <figcaption>Which stick / switch drives which robot function.</figcaption>
</figure>

- **Diagram source (Figma):** [OpenDubs board — RC controls](https://www.figma.com/board/LVbgJwMKO65TARF3m6v7I6/OpenDubs?node-id=2-83) — anyone with the link can view and export it.

The transmitter's sticks and switches map to the robot's drive, mast, and mode functions as
below; the diagram shows the physical layout. RC reaches the robot as **SBUS** into the
StampPLC.

| Channel | Function | Notes |
|---|---|---|
| CH1 | **Rotate** (yaw) | mecanum rotate input |
| CH2 | **Mast** up / down, or **tilt** | right stick, vertical — meaning set by CH7 |
| CH3 | **Forward / reverse** | mecanum drive input |
| CH4 | **Strafe**, or **pan** | right stick, horizontal — meaning set by CH7 |
| CH5 | **Mode** | OFF / MANUAL / AUTO |
| CH6 | **Deadman** | hold to enable motion; release → neutral |
| CH7 | **Payload mode** | 3-position: OFF / Z-AXIS / PAN-TILT — see below |
| CH8 | **Speed** | scales drive output |

## CH7 — what the right stick means

There are two sticks and three things that want one, so a 3-position switch arbitrates.
**The left stick never changes meaning**; forward/reverse and rotate work in every position.

| CH7 | Right stick, vertical | Right stick, horizontal |
|---|---|---|
| **OFF** | *nothing* | strafe |
| **Z-AXIS** | mast up / down | strafe |
| **PAN-TILT** | tilt | pan |

OFF exists because the lift used to creep whenever the operator only meant to drive: with
the switch here, the stick cannot move the Z-axis at all.

**Strafe is unavailable in PAN-TILT** — CH4 is doing something else. Forward/reverse and
rotate still work, so the robot can be repositioned tank-style while the head is aimed.

!!! note "The right stick must be re-centred after a mode change"
    The stick means something different in each position, so it is ignored until it returns
    to centre once. Otherwise flipping from PAN-TILT to Z-AXIS with the stick held down would
    hand the lift a full-scale command the instant the switch clicked — and flipping the
    other way, a stick held sideways would lurch the robot into a strafe.

    Telemetry reports this as `pmarm=0` while it waits. If an axis seems dead right after
    moving the switch, centre the stick.

The deadman still gates everything: nothing moves unless CH6 is held, in any mode.

Only the RC **stick** is gated by CH7. The dashboard and the Tab5's own buttons are separate
operator interfaces with their own hold-to-move, and they keep working whatever the switch
is doing — a touchscreen button that silently did nothing because of a switch position on
the transmitter would be its own kind of bug.

## See also

- [Control authority & modes](control-authority.md) — how RC interacts with autonomy (precedence,
  override).
- [Command & telemetry interface](interfaces/command-telemetry.md) — RC as one control source, and
  the failsafe/deadman rules.
