<!-- ssa-meta
always_loaded: true
description: OpenDubs arm motion: frames, which arm, workspace from the robot, gripper opening, stow, refusals.
-->
# Arm motion (OpenDubs, two OpenArm arms)

## Frames and axes
Every pose is in `base_link`: **x forward, y left, z up**, meters. `move_ee_to_rel_pose(arm, delta)`
uses the same axes: `{"x": 0.05}` is 5 cm forward, `{"y": 0.05}` is 5 cm to the robot's left,
`{"z": 0.05}` is 5 cm up.

## Which arm
Every arm call names `"left"` or `"right"`. Use the arm on the side of the target
(the target's y > 0 is the left side). Use one arm per task unless the task needs both.

## Workspace: read it, never guess it
Call `get_workspace()` once at the start and clamp every computed target into the arm's box
before moving. A target outside it raises `MotionRefused`, which stops the program.

```python
ws = get_workspace()["right"]
def clamp(v, lo_hi):
    return max(lo_hi[0], min(lo_hi[1], v))
target = {"position": {"x": clamp(x, ws["x"]), "y": clamp(y, ws["y"]), "z": clamp(z, ws["z"])},
          "orientation": grasp_orientation}
move_ee_to_pose("right", target)
```

## Gripper
`set_gripper_width(arm, opening)`: **0.0 closed, 1.0 fully open**, not meters. The robot caps
the grip effort, so closing fully on a soft object is safe: close with `0.0`.

## Named poses
`get_named_poses()` lists joint-space poses the robot knows. `"stow"` is always there:
the arms tucked in. `reset_robot()` stows both arms and keeps the grippers as they are,
so it never drops what an arm is holding.

## Start and finish
- Start every program from a known pose: `reset_robot()` first.
- Finish with the arm used back at `"stow"` (`move_arm_to_named_pose(arm, "stow")`), so the
  next run starts from the same place.

## Orientation
The robot places the gripper's tool point; it does not yet control the gripper's orientation.
The arm reaches each position in the posture nearest the one it is in, so approach a grasp
from straight above with a short vertical move (`move_ee_to_rel_pose`), not a long diagonal one.

## Motion is gated by the robot, not by the program
A motion runs only while the supervisor holds the deadman and authority is in AUTO. If
either stops, the arm holds where it is and the call raises `MotionRefused`. A move is also
refused before it starts if its target is out of reach or the arm would pass too close to the
robot's own head, body or other arm: choose a different target, do not retry the same one.
**Never catch `MotionRefused`** and never retry after it: the program must stop.
Do not wrap motion calls in `try/except Exception`.

## Common patterns

**Approach from above, grasp, lift:**
```python
set_gripper_width("right", 1.0)                              # open before approaching
move_ee_to_pose("right", above_pose)                          # 10 cm above the grasp
move_ee_to_rel_pose("right", {"x": 0, "y": 0, "z": -0.10})   # straight down
set_gripper_width("right", 0.0)                              # close
move_ee_to_rel_pose("right", {"x": 0, "y": 0, "z": 0.10})    # lift before moving sideways
```

## Correction guidelines
- "A bit" or "slightly" means 0.02 to 0.05 m. Prefer the small end near people.
- "Higher" / "lower" is z, "closer to the robot" is -x, "further out" is +x,
  "left" / "right" are the robot's left (+y) and right (-y).
