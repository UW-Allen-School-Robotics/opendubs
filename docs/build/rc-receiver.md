# RC receiver wiring

The RC receiver's **SBUS** output feeds the StampPLC's **blue serial port** (signal on **G5**,
inverted in firmware). The catch: the StampPLC port uses the **M5Stack / Grove** 4-wire convention,
while the receiver's SBUS pin is a **3-wire servo cable** (Yellow / Red / Black). You make a short
**Grove-to-servo pigtail** to bridge them — and because the two conventions use *different colors and
pin orders*, you must wire by **function, not by matching colors**.

!!! warning "Wire by function — verify +5 V and GND before plugging in the receiver"
    Grove ribbon colors do **not** map to servo colors. Reversing +5 V and GND can destroy the
    receiver. **Meter the pigtail** (5 V present on the right pin, continuity on GND) *before* you
    connect the receiver.

!!! info "At a glance"
    - **Time:** ~15 min · **Difficulty:** easy (one pigtail, three wires)
    - **You make:** a StampPLC blue-port (Grove) ⟷ 3-pin servo pigtail
    - **Result:** RC receiver powered from the port, SBUS on G5, `[SBUS]` frames flowing

## The two conventions

| Function | StampPLC **blue port** (Grove/M5) | RC receiver **SBUS** (servo cable) |
|---|---|---|
| **Signal** | **G5** (RX, inverted) | **Yellow** (or white/orange) |
| **Power** | **+5 V** | **Red** |
| **Ground** | **GND** | **Black** (or brown) |

*Servo (Futaba/JR) SBUS is 3-wire: signal / +5 V / ground. The Grove port is 4-wire — the second
signal pin is unused here (leave it unconnected).*

!!! note "Confirm the blue-port pin positions"
    Check the exact GND / 5 V / G5 pin positions against the StampPLC silkscreen and the
    [M5StamPLC pinout](https://docs.m5stack.com/en/core/StamPLC) before crimping — port pin order
    varies. The map above is by **function**; get GND and +5 V right first.

## Make the pigtail

1. Start from a **Grove 4-pin cable** (or a connector that mates the blue port) and a **3-pin servo
   lead** (male header to plug the receiver's female SBUS socket, or vice-versa — match your
   receiver).
2. Join them **by function** using the table above: receiver **signal → G5**, **red → +5 V**,
   **black → GND**. Cap or tape the unused 4th Grove wire.
3. Join method: WAGO / solder + heat-shrink / crimp — your choice (same as the rest of the build).
4. **Meter it:** with the StampPLC powered and the receiver *unplugged*, confirm +5 V on the power
   pin and continuity to GND. Only then plug in the receiver.

*TODO photo: the finished pigtail, Grove end + servo end, colors labeled.*

## Why inverted, and no hardware inverter

SBUS is an **inverted** serial signal. The StampPLC firmware puts G5 into inverted-UART mode in
software ("SBUS is locked to inverted mode on the blue port"), so you **do not** need an external
inverter — wire the receiver's SBUS pin straight to G5.

## Verify

1. Power the StampPLC and turn on the RC transmitter.
2. On the dashboard (or `[SBUS]` telemetry), confirm **`ok` climbing, `bad` ~0**, and **RC live**.
3. Move a stick — the drive bars on the RC/DRIVE screen should track it.

If `ok` stays 0: re-check signal is on **G5** (not the other Grove signal pin) and that the receiver
has power. If `bad` climbs, suspect the signal wire routing (keep it away from motor/power leads).

## See also

- [StampPLC connections](../stamplc-connections.md) — the full port/pin reference.
- [RC control mapping](../rc-control-mapping.md) — which channel drives which function.
- [Flash a StampPLC](../flashing-stamplc.md) — get firmware on the board first.
