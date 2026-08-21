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
| CH2 | **Mast** up / down | |
| CH3 | **Forward / reverse** | mecanum drive input |
| CH4 | **Strafe** | mecanum strafe input |
| CH5 | **Mode** | OFF / MANUAL / AUTO |
| CH6 | **Deadman** | hold to enable motion; release → neutral |
| CH7 | **Mast enable** | on/off toggle for the mast (Z-axis) — lets you drive without risk of actuating it |
| CH8 | **Speed** | scales drive output |

## See also

- [Control authority & modes](control-authority.md) — how RC interacts with autonomy (precedence,
  override).
- [Command & telemetry interface](interfaces/command-telemetry.md) — RC as one control source, and
  the failsafe/deadman rules.
