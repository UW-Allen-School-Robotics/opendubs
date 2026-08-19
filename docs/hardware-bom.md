# Hardware BOM (Core System)

This BOM is the core architecture baseline. It is intentionally limited to
primary compute/control, mobility, mast, manipulator, and power distribution
components.

Subsystem values in this table are constrained to:
- `Control/Electronics`
- `Mobile Base`
- `Mast`
- `Manipulators`
- `Power`

Status key:
- `in-use`: integrated and currently used
- `wired-not-enabled`: physically connected but not yet active in software
- `planned`: intended architecture component
- `tbd`: expected but final part/config not locked

| Subsystem | Component | Qty | Part / Identifier | Vendor/Product Page | Reference Page | Notes | Status |
|---|---|---:|---|---|---|---|---|
| Control/Electronics | NVIDIA Thor AGX Dev Kit | 1 | Thor AGX Dev Kit | https://www.nvidia.com/ | https://developer.nvidia.com/ | Primary autonomy brain | planned |
| Control/Electronics | M5StampPLC | 1 | M5StampPLC | https://docs.m5stack.com/en/core/stamplc | https://github.com/m5stack/M5StamPLC | Low-level I/O, safety gating, bus interfaces | in-use |
| Control/Electronics | RC TX/RX set | 1 | Radiolink T8S Transmitter/Rcvr | https://a.co/d/0cFzagC4 | tbd | Manual repositioning control path | in-use |
| Control/Electronics | M5 8Servo Unit (PWM source) | 1 | I2C addr 0x25 | https://docs.m5stack.com/en/unit/8Servos%20Unit | https://github.com/m5stack/M5Unit-8Servo | PWM source for mixer/drive ESC inputs and mast ESC input | planned |
| Control/Electronics | REV Power Distribution Hub | 1 | REV-11-1850 | https://www.revrobotics.com/rev-11-1850/ | https://docs.revrobotics.com/ion-control-system/pdh/specs | CAN telemetry + switchable channel | wired-not-enabled |
| Mobile Base | goBILDA Mecanum Mixer | 1 | 3109-0001-0002 | https://www.gobilda.com/mecanum-mixer/ | https://www.gobilda.com/mecanum-mixer/ | Mixes rotate/forward/strafe control channels | planned |
| Mobile Base | ServoCity dual-channel ESC | 2 | 3105-0202-0080 class | https://www.servocity.com/ | https://www.servocity.com/content/user_manuals/3105-0202-0080_data-sheet.pdf | Two ESC units, two control channels each, four drive channels total | planned |
| Mobile Base | Mecanum drive motors | 4 | drivetrain-specific | https://www.servocity.com/5303-series-saturn-planetary-gear-motor-99-5-1-ratio-24mm-length-8mm-rex-shaft-180-rpm-3-3-5v-encoder/ | tbd | Drive motor class also reused for mast motor | tbd |
| Mobile Base | 4-Bar Odometry Pack (2 Pods + Pinpoint) | 1 | 3203-3110-0002 | https://www.servocity.com/4-bar-odometry-pack-2-pods-1-pinpoint-computer/ | https://www.servocity.com/pinpoint-v2-odometry-computer-imu-sensor-fusion-for-2-wheel-odometry/ | Includes two deadwheel pods and Pinpoint computer; Pinpoint connects over I2C | planned |
| Mast | M5 ExtEncoder Unit | 1 | I2C addr 0x59 | https://docs.m5stack.com/en/unit/ExtEncoder%20Unit | https://docs.m5stack.com/en/unit/ExtEncoder%20Unit | Lift position telemetry | planned |
| Mast | ServoCity ESC (same class as drive) | 1 | 3105-0202-0080 class | https://www.servocity.com/ | https://www.servocity.com/content/user_manuals/3105-0202-0080_data-sheet.pdf | Mast uses same ESC class as drive for hardware commonality | planned |
| Mast | Mast motor (same class as drive) | 1 | drivetrain-specific | https://www.servocity.com/5303-series-saturn-planetary-gear-motor-99-5-1-ratio-24mm-length-8mm-rex-shaft-180-rpm-3-3-5v-encoder/ | tbd | Mast motor intentionally reuses drive motor class for simplicity | tbd |
| Manipulators | OpenArm 7-DOF | 2 | OpenArm | https://github.com/enactic/OpenArm | https://github.com/enactic/OpenArm | Left and right arms | planned |
| Power | E-stop main contactor | 1 | GIGAVAC GX11CAB | https://www.sensata.com/products/gigavac-power-contactors-fuses/gx-contactors-800v/gx-normally-open-contactors | https://www.sensata.com/sites/default/files/a/sensata-gigavac-gx11-series-open-contactors-datasheet.pdf | Sealed NO DC, 150A cont/800V; 24Vdc coil (`C`); SPDT aux → DI6 weld sense. In the main power path; opens on HW/SW/no-power e-stop | planned |
| Power | Source-OR diode combiner | 1 | Victron Argo Diode BCD 802 | https://www.victronenergy.com/upload/documents/Datasheet-Argo-Diode-Battery-Combiners-EN.pdf | https://www.victronenergy.com/upload/documents/Datasheet-Argo-Diode-Battery-Combiners-EN.pdf | Battery + shore source-OR; passive highest-voltage-wins + reverse-block; 80A, Schottky, M6 studs | planned |
| Power | Regen clamp / ideal-diode | 1 | ODrive Regen Clamp | https://shop.odriverobotics.com/products/odrive-regeneration-clamp | https://docs.odriverobotics.com/v/latest/hardware/regen-clamp-datasheet.html | Downstream of DBC, upstream of PDH; absorbs 100% braking regen (DBC blocks regen-to-pack) | planned |
| Power | Battery packs | 2 | DeWalt 20V Max (5S) in goBILDA Smart Dock 3104-0020-0001 | https://www.gobilda.com/gobilda-smart-dock-for-20v-battery-dewalt-20v-max-compatible/ | tbd | ~15–21V each; Auto-Switcher hot-swap; charged off-robot | in-use |

## Immediate BOM Gaps

- **Shore PSU** (24 V, current-limited) — wattage tbd once Case-2 load is summed.
- **E-stop buttons** (dual-pole NC) part/qty; battery Auto-Switcher p/n; mini-PDH for logic.
- Add final mast ESC channel allocation details versus installed ESC hardware.
- Lock final motor model and gearing for both drive and mast reuse strategy.
- Add fuse, wiring, and connector-level BOM (next phase).
- Add per-component ownership and update cadence.
