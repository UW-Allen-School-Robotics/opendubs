> ⚠️ **ARCHIVED / SUPERSEDED (2026-08).** Kept for decision history — this does NOT describe the current robot. RC is now StampPLC-SBUS (Pixhawk/MAVLink eliminated); power is a 52V LiFePO4 + JBD BMS + three-rail split (no DeWalt/shore). See [history/README.md](README.md) and [../system-overview.md](../../docs/system-overview.md).

# Schematics & Diagrams

## Authoritative schematic — Figma

The single source of truth for wiring (power + data + safety) is the **Figma board
"OpenDubsV2."** It is what is being physically built — keep this repo's docs aligned to
it (and vice-versa).

Main schematic regions:
- **Power spine:** 2× DeWalt 20 VDC packs (each + PWR switch) → switcher → **Victron
  Diode Battery Combiner** (source-OR) → ODrive regen clamp → **e-stop contactor** → REV
  PDH → subsystems (mast / mobile base / arms). Logic (Thor/StampLC/Pixhawk) is on a
  **separate mini-PDH upstream of the contactor**.
- **Compute/sensors:** Thor; front + rear RPLIDAR + head camera via a USB hub; Pixhawk 6C
  (RC/GPS/optical-flow/PWM); **M5 8-Servo** drives head pan/tilt + deadwheel-pod raise/lower;
  Pinpoint deadwheel odometry + PRISM LEDs on the 3.3 V I²C side.
- **Insets:** SHORE POWER (Victron DBC), SAFETY LOGIC (two-channel e-stop), I²C address
  table, I²C level-shifter (5 V↔3.3 V), RC (RadioLink).

Prose companions: [power-architecture.md](power-architecture.md),
[control-architecture.md](control-architecture.md), [control-authority.md](../../docs/control-authority.md).

## Reading the Figma board programmatically

The Figma **structured** API floods this board's rate limits — use **per-node PNG
screenshots** instead (the screenshot tool returns a short-lived URL you `curl` down,
then crop locally with Pillow/sips). Do **not** pull the full `get_figjam` / structured
node tree.

## Generated diagrams (in-repo)

State-machine diagrams are generated from [state-machines.md](../../docs/state-machines.md):
- `docs/diagrams/state_machine_robot.mmd`
- `docs/diagrams/state_machine_subsystem_pattern.mmd`

Regenerate with `python tools/generate_state_machine_mermaid.py`.
