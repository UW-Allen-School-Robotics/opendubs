<!-- ssa-meta
description: Grasping on OpenDubs: detect, approach from above, close, lift, verify; markers when an object carries one.
-->
# Grasping

## Finding the object
- `detect_objects(text_prompt, camera="head")` returns a grasp pose:
  `{"best_grasp": {"translation_wrt_base": [x, y, z], "quaternion_wrt_base": {...}, "score": s}}`.
  Name the object simply: `"candy"`, `"wrapped candy"`, not a sentence.
- If the object or its container carries an ArUco marker, `detect_markers()` is more reliable:
  use the marker's `grasp_pose` as the target.
- Adjust a grasp with the offsets of `detect_objects` (meters, chassis frame), not by editing the
  returned numbers: "a bit higher" is `detect_objects("candy", z_offset=0.02)`.

## Converting a detection into a pose
```python
g = detect_objects("candy")["best_grasp"]
grasp = {"position": {"x": g["translation_wrt_base"][0], "y": g["translation_wrt_base"][1],
                      "z": g["translation_wrt_base"][2]},
         "orientation": g["quaternion_wrt_base"]}
```

## Sequence
1. `set_gripper_width(arm, 1.0)`: open fully before the approach.
2. Move 10 cm above the grasp (`grasp` with z + 0.10, clamped to `get_workspace()`).
3. `move_ee_to_rel_pose(arm, {"x": 0, "y": 0, "z": -0.10})`: straight down.
4. `set_gripper_width(arm, 0.0)`: close.
5. `move_ee_to_rel_pose(arm, {"x": 0, "y": 0, "z": 0.10})`: lift before any sideways move.
6. `verify_grasp(object, arm)` (verification.md).

## From a bowl
Objects in a bowl sit below its rim: approach from directly above and lift straight up until
the gripper is clear of the rim before moving sideways.
