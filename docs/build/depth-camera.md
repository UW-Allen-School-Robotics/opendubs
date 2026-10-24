# Depth Camera Mount (Femto Bolt)

!!! warning "In development"
    The mount is designed and its geometry is in the repository, but this page does not yet have
    photos, print settings, the CAD downloads or the fastener sizes. Those are marked *TODO*
    below.

!!! info "At a glance"
    | | |
    |---|---|
    | **You'll build** | A 3D-printed sleeve that holds an Orbbec Femto Bolt depth camera on top of the robot's face, looking where the face looks |
    | **Time** | *TODO* |
    | **Difficulty** | Beginner (print one part, slide it in, fit two bolts) |
    | **Prerequisites** | The [Pan/Tilt HMI module](pan-tilt-hmi.md), whose face holder carries the mount; an Orbbec Femto Bolt |
    | **Mates to** | Two pockets in the top of the face holder; *TODO: the camera's cable to the [Thor](thor-install.md)* |
    | **Optional?** | Yes — the robot drives and performs without one |

## What this is

A depth camera measures how far away each point in its view is. The robot uses one to find
people: the face can turn toward them, and the stage page draws the people dancing in front of
the robot as live skeletons. No camera image is shown, streamed or recorded for that; only
joint positions are used.

This module puts an **Orbbec Femto Bolt**, a time-of-flight depth camera, on top of the robot's
face. It rides the face's tilt, so the camera always looks where the face looks.

The mount is a single printed part: a **rectangular sleeve** that wraps the camera's body, with
**two legs** underneath that drop into pockets in the top of the face holder.

The Intel RealSense D415 is an alternative depth camera, also listed as optional in the
[Hardware BOM](../hardware-bom.md). This mount is for the Femto Bolt only.
[Camera selection](../camera-selection.md) compares these and other cameras.

## CAD & 3D model

- **View in browser (Fusion):** *TODO — add permalink*
- **Download:** *TODO — STEP + the mount's STL, into `hardware/`*

The robot's description in the repository already carries simplified meshes of both parts,
for the simulator and the robot model: `description/opendubs/meshes/femto_mount.stl` (the mount)
and `femto_bolt.stl` (the camera's 115 × 65 × 40 mm outer envelope, not Orbbec's CAD). Use the
CAD downloads above for printing, not these.

## Bill of materials

!!! note "Generated from `hardware/parts.csv`"
    Edit the CSV (rows tagged `module = depth-camera`), then run `tools/bom.py --inject`.

<!-- BOM-AUTO:build:depth-camera -->
**Camera**

| Part | SKU | Qty | Notes |
|---|---|---:|---|
| [Orbbec Femto Bolt depth camera](https://store.orbbec.com/products/femto-bolt) | `100100433` | 1 | Optional. A time-of-flight depth camera; an alternative to the RealSense D415. |

**Mount**

| Part | SKU | Qty | Notes |
|---|---|---:|---|
| Femto Bolt mount (3D-printed) | — | 1 | A sleeve around the camera with two legs that drop into the face holder's pockets. STL in hardware/ TBD; print settings TBD. |
| M4 bolt, through each leg (length TBD) | — | 2 | One through each pocket of the face holder and the leg inside it (4.3 mm hole). Length, and nut or threaded insert, to be confirmed. |
<!-- /BOM-AUTO:build:depth-camera -->

## Print

*TODO — material, orientation, infill and supports.*

The sleeve's walls are about 3 mm thick at the sides and 3.75 mm top and bottom. The legs carry
the camera's weight on a tilting head, so print them solid enough not to flex.

## Assembly

*TODO — photos for each step.*

1. **Slide the camera into the sleeve.** The sleeve is open at the back and the front. It
   holds the camera on all four sides, with the camera's front face standing about 19 mm proud
   of the sleeve so nothing overlaps the lenses. Face the camera's lenses forward, the same way
   as the tablet's screen.
2. **Secure the camera.** Each side wall of the sleeve has two small holes at the camera's
   mid-height, 20 mm apart. *TODO: confirm what goes through them (the screw size, and which
   threaded holes on the Femto Bolt's sides they meet), or how else the camera is held.*
3. **Drop the legs into the face holder.** The top of the face holder has two rectangular
   pockets, one at each side. Each leg slides about 50 mm down into its pocket, which holds it
   on all four sides; the sleeve then sits just above the top of the face holder.
4. **Bolt each leg in.** A 4.3 mm hole, sized for an M4 bolt, runs sideways through each pocket
   and the leg inside it. Put a bolt through each. *TODO: the bolt length and whether it takes
   a nut or a threaded insert.*
5. **Run the camera's cable** to the Thor. *TODO: the cable, its route along the neck, and
   enough slack for the head's full pan and tilt.*

## Where the camera is, for software

The robot model places the camera with three frames. The numbers below are from the CAD, with
the head at its centre (no pan, no tilt) and the lift fully down; the robot model
(`description/opendubs/dimensions.yaml`, turned into the robot's URDF by
`description/opendubs/gen_urdf.py`) is the source for them.

| Frame | Attached to | Where | Orientation |
|---|---|---|---|
| `femto_mount_link` | `head_tilt_link` (the tilting face) | where the mount's legs enter the face holder: centred between the two pockets, on the holder's top face | the same as `head_tilt_link` |
| `camera_link` | `femto_mount_link` | on the **depth** camera's lens, at the camera's front face: 33.95 mm forward, 30.0 mm to the robot's right and 27.51 mm up from `femto_mount_link` | pitched **6° down** |

The depth lens is the one 30 mm to the right of the camera's centre, as seen from behind the
robot; the colour (RGB) lens is near the centre. Orbbec's documentation gives the depth camera a
6° downward tilt relative to the colour camera, which is square to the housing, so
`camera_link` is pitched 6° down.

`camera_link` is placed at the front face of the housing. The depth camera's true optical
centre is somewhat behind the glass; its exact depth is not in this geometry.

The frames *below* `camera_link` (the depth, colour and IMU frames, and their optical frames)
are published by Orbbec's ROS 2 driver, OrbbecSDK_ROS2, from the camera's own factory
calibration. The robot model stops at `camera_link`, so the two never publish the same
transform.

## Verify — done when…

- [ ] The camera sits square in the sleeve, lenses forward, with nothing over the lenses.
- [ ] Both legs are fully down in their pockets and bolted; the camera doesn't rock when the
      head tilts.
- [ ] The cable has enough slack for the head's full pan and tilt.
- [ ] The camera appears on the Thor. *TODO: the check command.*
