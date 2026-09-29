# Coordinate frames & naming

This is the convention every other document, CAD file, firmware axis, and ROS frame refers back to.
Get it right once here.

## Body sides

Four sides, **from the robot's own point of view** (as if seated in it facing forward) — never the
viewer's:

**FRONT · REAR · LEFT · RIGHT**

!!! warning "Spell the sides out; reserve two-letter codes for corners only"
    Do **not** abbreviate a single side as `FR`/`RR` — those exact tokens are the standard **corner**
    codes (Front-Right, Rear-Right), which the drivetrain and firmware already use. Corner codes are
    the *only* place two letters appear:

    | Code | Corner |
    |---|---|
    | `FL` | Front-Left |
    | `FR` | Front-Right |
    | `RL` | Rear-Left |
    | `RR` | Rear-Right |

    Wheels, corner drive modules, and corner LED segments use these. (The PRISM LED map already reads
    `RIGHT FRONT`, `LEFT REAR`, … — spelled, unambiguous.)

## Coordinate frame — REP-103

The canonical robot frame is **ROS [REP-103](https://www.ros.org/reps/rep-0103.html)**:

| Axis | Direction |
|---|---|
| **+X** | **Forward** (toward FRONT) |
| **+Y** | **Left** (toward LEFT) |
| **+Z** | **Up** |

Right-handed: X (forward) × Y (left) = Z (up). This is what `base_link`, odometry, and every TF
transform assume.

!!! note "It agrees with NVIDIA Isaac"
    Isaac Sim / Isaac Lab use a **right-handed, Z-up** world, and NVIDIA's ROS bridge follows
    REP-103. Adopting REP-103 keeps **CAD ↔ firmware ↔ ROS 2 ↔ Isaac** in one frame with no
    sim-to-real sign flips. (Camera *optical* frames use the separate z-forward/x-right/y-down
    convention in both ecosystems — that lives on the sensor, not the body.)

## Origin (build datum)

**(0, 0, 0) = the top face of the center hole of the rear-most 432 mm goBILDA cross-rail.**

The goBILDA 1120-series 432 mm U-channel has **17 large (Ø14 mm) holes on 24 mm centers**, so there
is an exact center hole (#9): 16 gaps × 24 mm = 384 mm of holes with a 24 mm margin each end → hole
#9 sits at 216 mm = the rail's midline. That's a real, findable feature you can put calipers on,
which is why it's the datum.

With +X forward from that rear rail:

- The whole chassis sits at **x ≥ 0** (front extent ≈ +600 mm) — no negative length coordinates.
- **Y is mirror-symmetric** about the centerline: rail holes at 0, ±24, … ±192 mm; physical rail
  edges at ±216 mm. **+Y = LEFT**, −Y = RIGHT.
- **+Z = up**, out of the channel's top face; wheels/ground are at −Z.

Corners fall out cleanly:

| Corner | X | Y |
|---|---|---|
| FL | + (front) | + (left) |
| FR | + (front) | − (right) |
| RL | − (rear) | + (left) |
| RR | − (rear) | − (right) |

!!! info "Build datum vs. `base_link` (open refinement)"
    The rear-center-hole is the ideal **CAD/fabrication datum**. For ROS, `base_link` is often placed
    at the **wheelbase geometric center** instead (mecanum odometry and in-place rotation are
    cleanest about the true center of the wheel rectangle). These can be the same point or two frames
    joined by a fixed static transform — decide before wiring up odometry. Everything above holds
    either way.

> A clean annotated orientation figure (replacing the working CAD screenshot) is a TODO for the
> hero image.

## Module IDs

Assembly modules — which name CAD components, assembly-guide sections, the BOM `cad_ref` column, and
photo filenames — use a short hierarchical ID:

| Prefix | Module |
|---|---|
| `CH` | Chassis / frame |
| `DT` | Drivetrain, per corner → `DT-FL`, `DT-FR`, `DT-RL`, `DT-RR` |
| `PW` | Power (battery, BMS, PDH, e-stop, contactor) |
| `CMP` | Compute (Thor + mounts) |
| `CTL` | Control electronics (StamPLC, Tab5, PRISM, harness) |
| `SEN` | Sensing → `SEN-LID-F`, `SEN-LID-R`, `SEN-CAM`, `SEN-GPS`, `SEN-IMU` |
| `SHL` | Shell / covers / panels |
| `MST` | Mast (if in scope) |

A photo then names itself — `DT-FR_step-03_top.jpg` — and the build order is just these modules in
dependency order: chassis → drivetrain → power → compute + control → sensing → firmware → software →
calibration.
