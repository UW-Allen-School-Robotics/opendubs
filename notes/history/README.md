# Archived / superseded design docs

These documents describe **earlier design directions that have since been replaced.**
They are kept for decision history — *not* as a description of the current robot. For the
current design, start at [`../../docs/system-overview.md`](../../docs/system-overview.md) and the live docs
in [`../`](../).

Two baseline changes (2026) make everything here out of date; read these docs with both in mind:

1. **The Pixhawk 6C / MAVLink / MAVROS "pilot" layer was eliminated.** RC now comes from the
   **M5Stack StampPLC via SBUS**; the StampPLC is the control authority and telemetry hub.
   GPS/IMU/pose fusion moved to a **ROS EKF on the Thor** (not the Pixhawk).
2. **The power source changed** from two DeWalt 20 V packs + a "shore power" leg to a single
   **52 V ECO-WORTHY LiFePO4 pack with a JBD BMS (RS485)**, split into a **logic-24 V rail**
   (always-on, upstream of the e-stop contactor) and a **drive-24 V rail** (behind the GIGAVAC
   contactor). There is no "shore power" concept — just a **charger** (connected ⇒ drive inhibit).
   The Victron Argo diode combiner and GIGAVAC GX11CAB contactor choices carried forward.

| Archived doc | Superseded because | Current source |
|---|---|---|
| `control-architecture.md` | Built entirely on the Pixhawk + StampPLC split | `../../docs/system-overview.md` (control is StampPLC-SBUS) |
| `power-architecture.md` | DeWalt/shore/Pixhawk framing; predates the 52 V pack + three-rail split | (current power doc TBD — see note) |
| `data-power-flow.md` | Same DeWalt/shore/Pixhawk flow diagram | (current power doc TBD) |
| `hardening-roadmap.md` | Tracks 3/7/8 are MAVROS/Pixhawk-specific and now moot | `../capability-backlog.md` |
| `telemetry-spec.md` | Describes an unbuilt ROS-topic/JSON scheme + old DeWalt power; never documented the real `[TAG] key=value` serial contract | `../../firmware/rc_serial_probe/README.md` (current serial tags) |

> **Note:** the live `docs/` tree currently has **no** dedicated power-architecture or
> control-architecture doc — the correct facts (52 V pack + JBD BMS, three-rail split, Victron
> Argo, GIGAVAC, StampPLC-SBUS control) live in `../../docs/system-overview.md` and the code/memory but
> are not yet written up as standalone baseline docs. Writing concise current replacements is a
> tracked follow-up.
