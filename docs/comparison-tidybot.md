# OpenDubs vs TidyBot++

[TidyBot++](https://tidybot2.github.io/) ([paper](https://arxiv.org/abs/2412.10447) ·
[code](https://github.com/jimmyyhwu/tidybot2); Princeton / Stanford, CoRL 2024) is a closely
related open project — an affordable, learning-friendly mobile-manipulation base. It's an
excellent design and worth studying. This page compares the two **fairly**: they make different,
defensible choices, and the goal here is to make the tradeoffs legible, not to declare a winner.

!!! note "Sourcing"
    TidyBot++ figures are from their [paper](https://arxiv.org/abs/2412.10447) (arXiv 2412.10447) —
    confirm against their latest docs. Cells marked *TODO* are OpenDubs numbers we still need to
    total from the [Hardware BOM](hardware-bom.md). Where a value isn't in TidyBot++'s paper, the
    cell says "not documented" rather than guessing.

## The one‑line version

Both bases are **holonomic** (both strafe and rotate in place) — the divergence is *how*.
**OpenDubs spends on autonomy compute and a cheap, reconfigurable drive; TidyBot++ spends on a
premium holonomic drive and higher payload.** Pick accordingly.

## Drive & mobility

| | OpenDubs | TidyBot++ |
|---|---|---|
| **Mechanism** | 4× **mecanum** roller wheels, **1 motor each** | 4× **powered‑caster** modules, **2 motors each** (steer + drive), CAN bus |
| **Holonomic?** | Yes (strafe + rotate in place, via rollers) | Yes (via powered‑caster offset) |
| **Traction & efficiency** | Roller slip; lower efficiency; small rollers sensitive to thresholds/debris | Full‑traction wheels; higher efficiency; better over thresholds/carpet |
| **Reconfigurable to fixed FWD / tank** | **Yes** — swap wheels + change the mixer (jumper‑interlocked) | **No** — the casters *are* the drive |
| **Drive motor count** | **4** | **8** (+ steering) |
| **Relative drive cost / corner** | Baseline — cheap motor + ESC | **~2–3×** — caster module + CAN controller |
| **Control** | Simple 4‑wheel mecanum mixer | Coordinated steer + drive per module |

## Cost, size & payload

| | OpenDubs | TidyBot++ |
|---|---|---|
| **Approx. total cost** | *TODO — total from the [BOM](hardware-bom.md)*; low thousands, **dominated by the Thor compute** | **~$5–6k** |
| **Cost allocation** | More on **autonomy compute**, less on drive | More on **drive**, less on compute |
| **Footprint** | ~**432 × 624 mm** frame | **50 × 54 cm** |
| **Weight** | ~**25 kg** loaded (frame + Thor + 52 V pack + electronics) | **34 kg** (base only) |
| **Payload** | Lab‑scale top‑deck + an arm; **8 mm REX drivetrain limits** heavy/dynamic loads (see [load limits](design-philosophy.md#the-drive-system)) | **~60 kg** conservative (tested 122 kg) — a clear TidyBot++ strength |

## Compute, autonomy & teleop

| | OpenDubs | TidyBot++ |
|---|---|---|
| **Onboard compute** | **NVIDIA Jetson AGX Thor** (GPU autonomy compute, 128 GB) | **Intel NUC** mini PC |
| **Software substrate** | **ROS 2** (Jazzy) | Open control stack (ROS not mentioned in the paper) |
| **Teleoperation** | RC (SBUS) + on‑screen tablet + gamepad + Meta Quest | **Mobile‑phone** interface (WebXR, streams 6‑DoF pose) |
| **Learning / data** | Base exposed as a ROS 2 action/observation robot for policies | Built around **imitation‑learning data collection** via phone teleop |

## Power & safety

| | OpenDubs | TidyBot++ |
|---|---|---|
| **Power** | **52 V LiFePO₄ pack** (JBD BMS) → always‑on 24 V logic + drive rails | **768 Wh portable power station** (AC out, ~8 h) + SLA motor battery |
| **Hardware e‑stop / safety** | **Integrated contactor e‑stop** (hardware kill of the drive bus) — see [E‑stop system](e-stop.md) | Not documented in the paper (battery monitoring via FRC parts) |

## Manipulation & project

| | OpenDubs | TidyBot++ |
|---|---|---|
| **Arm support** | Arm‑agnostic mounts — OpenArm / YAM / SO‑101 / COTS — **plus a Z‑axis mast option** | Arm‑agnostic; **Kinova Gen3** as the primary demonstrated arm |
| **Structure / fabrication** | goBILDA structural + 3D‑printed parts | 3D‑printed + off‑the‑shelf (their CAD) |
| **Origin / openness** | Community‑replication build; open | CoRL 2024 research (Princeton / Stanford); open‑source |

## Which to pick

- **Choose OpenDubs** if you want lower cost, a **reconfigurable drive** (mecanum ↔ cheaper fixed
  FWD/tank), **onboard GPU autonomy compute** (Thor), an **integrated hardware‑safety** story, and
  the goBILDA parts ecosystem.
- **Choose TidyBot++** if you want the **best holonomic traction/efficiency** and **high payload**
  for manipulation, a clean **phone‑teleop data‑collection** workflow, and don't need heavy onboard
  autonomy compute.

A **swerve / powered‑caster drive alternative for OpenDubs** is likely future work — at which point
the drive rows above converge and cost/compute/safety become the main distinctions.
