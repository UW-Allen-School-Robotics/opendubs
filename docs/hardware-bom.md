# Hardware BOM (Core System)

This BOM is the core architecture baseline. It is intentionally limited to
primary compute/control, mobility, mast, manipulator, and power distribution
components.

!!! tip "Educational discounts available"
    goBILDA / ServoCity offers **educational pricing for K-12 and university groups**.
    If you're ordering for a school or university team, apply through
    [servocity.com/education](https://www.servocity.com/education) before you purchase.

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
| Power | Regen clamp | 1 | ODrive Regen Clamp | https://shop.odriverobotics.com/products/odrive-regeneration-clamp | https://docs.odriverobotics.com/v/latest/hardware/regen-clamp-datasheet.html | Downstream of the PDH, at the ESCs (one per ESC); absorbs braking regen. The one-way 52→24 buck traps regen on the 24 V bus (can't backfeed the pack), so it's shunted here. See regen-clamp-sizing.md | planned |
| Power | Battery | 1 | ECO-WORTHY ~52 V LiFePO4 (16S), JBD BMS | https://www.ecoworthy.com/ | tbd | **Locked baseline** battery. BMS on RS485 to the StampPLC; charged off-robot | in-use |

## Tools & consumables

Shared across the whole build (buy once, not per module). goBILDA hardware is **metric**;
structural screws take a **3 mm hex**. **Generated from `hardware/parts.csv`** — third-party
tool links are examples, any equivalent works.

### Fasteners

<!-- BOM-AUTO:purchase:fasteners -->
| Item | Need | Qty | Source | Notes |
|---|---|---|---|---|
| M4 x 8 mm socket-head screw (bulk) | recommended | 2-3 packs | [buy](https://a.co/d/06F4f8am) | 105-count pack; the primary structural fastener; DEGREASE before use |
| M4 x 10 mm socket-head screw (bulk) | recommended | 2-3 packs | [buy](https://a.co/d/07bUYr2o) | 105-count pack; DEGREASE before use |
| M4 cap-head screw assortment | optional | 1 assortment | — | Some parts use cap-head M4 (installed with a 2.5 mm hex); buy an assortment |
| M3 socket-head screw assortment | optional | 1 assortment | — | For non-goBILDA parts; not used in the base frame |
| M4 x 55 mm socket-head screw | — | 4 | [buy](https://www.amazon.com/iExcell-Stainless-Socket-Screws-Wrench/dp/B089NGGLZQ) | Long standoff screws for the 10 mm sensor mount; confirm qty |
| M10 riv-nut | — | 2 | [buy](https://a.co/d/07JbuGAl) | Set into the rail's 14 mm holes; rivnut install is a FOLLOW-ON base-frame step; confirm qty |
<!-- /BOM-AUTO:purchase:fasteners -->

### Tools

<!-- BOM-AUTO:purchase:tools -->
| Item | Need | Qty | Source | Notes |
|---|---|---|---|---|
| Safety glasses (PPE) | required | 1 | — | Wear during ALL assembly — cutting, wiring, battery handling |
| Work gloves (PPE) | required | 1 | — | Protect hands during assembly, wiring, and battery handling |
| 3 mm hex wrench (4-pack) | required | 1 | [buy](https://a.co/d/028aG2GM) | Minimum tool; builds the frame by hand (~$7) |
| Rechargeable hex driver (incl. 3 mm bit) | recommended | 1 | [buy](https://a.co/d/06aqx7Jc) | Much faster for the bulk of the screws |
| 2.5 mm hex wrench / bit | recommended | 1 | — | For M4 cap-head screws |
| 7 mm ratcheting wrench | optional | 1 | [buy](https://a.co/d/03lJ5n8P) | For nuts |
| Long 3 mm hex bits | optional | 1 | [buy](https://a.co/d/05i2Vbj9) | Reach screws in tight spots |
| Riv-nut tool (manual) | — | 1 | [buy](https://a.co/d/0am8KbEV) | Sets the M10 riv-nuts — CHOOSE this OR the drill attachment |
| Riv-nut drill attachment | — | 1 | [buy](https://a.co/d/09zmlaXE) | Drill-driven alternative to the manual riv-nut tool |
| Soldering iron + solder | required | 1 | — | Required to build the regen clamp resistor lead |
| Wire cutter | recommended | 1 | [buy](https://a.co/d/04pImvE0) | Or similar |
| Wire stripper | recommended | 1 | [buy](https://a.co/d/05DNUngu) | Or similar |
| Heat gun | recommended | 1 | [buy](https://a.co/d/00PNwYUp) | For heat-shrink; or similar |
| Wire crimper | recommended | 1 | [buy](https://a.co/d/0dhX4RmB) | For battery connections; also if you crimp instead of WAGO/solder |
<!-- /BOM-AUTO:purchase:tools -->

### Consumables

<!-- BOM-AUTO:purchase:consumables -->
| Item | Need | Qty | Source | Notes |
|---|---|---|---|---|
| Blue removable threadlocker | recommended | 1 | [buy](https://a.co/d/0igs0Bmw) | Apply to structural screws; BLUE (removable), not red |
| WAGO lever connectors | — | a few | [buy](https://a.co/d/09VO6gwP) | Qwiic<->Grove and I2C/power joins — or solder instead |
| WAGO lever connectors | — | a few | [buy](https://a.co/d/09VO6gwP) | PaHub<->PRISM (goBILDA I2C) and PaHub<->LLC<->Pinpoint joins |
| Heat-shrink assortment / labels | recommended | 1 | [buy](https://a.co/d/04p7f5Vs) | Heat-shrink tube assortment or heat-shrink labels |
| Crimp connector assortment (butt joints + ring terminals) | recommended | 1 | [buy](https://a.co/d/08GtC4Ql) | Butt joints + ring connectors |
<!-- /BOM-AUTO:purchase:consumables -->

## Immediate BOM Gaps

- **Bench/shore PSU** (24 V, current-limited) for running off-battery — wattage TBD.
- **E-stop buttons** (dual-pole NC) part/qty; mini-PDH for logic.
- Add final mast ESC channel allocation details versus installed ESC hardware.
- Lock final motor model and gearing for both drive and mast reuse strategy.
- Add fuse, wiring, and connector-level BOM (next phase).
- Add per-component ownership and update cadence.
