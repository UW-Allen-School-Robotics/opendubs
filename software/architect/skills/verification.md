<!-- ssa-meta
always_loaded: true
description: Verification by default on OpenDubs: check the outcome of a grasp or handoff once, stop on failure.
-->
# Verification by default

After each sub-operation whose success the joints cannot show (a grasp, a handoff), check it
once, then go on or stop.

```python
set_gripper_width("right", 0.0)
move_ee_to_rel_pose("right", {"x": 0, "y": 0, "z": 0.10})
held = verify_grasp("candy", "right")
if not held["data"]["grasped"]:
    say("Oops, let me try that again.")
    raise RuntimeError("grasp failed: " + held["data"]["reason"])
```

## Rules
- **No in-program retry of a motion.** A failed check stops the program; the supervisor's
  correction changes the program, and retrying the same motion unchanged rarely helps.
- Check only the outcome-bearing step of each sub-operation, not every move: each camera
  check takes about a second, and a child is waiting.
- `verify_grasp(object, arm)` for a grasp. `place_in_hand` reports its own outcome in
  `released`; do not add a camera check after it.
- Ambiguous answers ("unclear", "I can't tell") count as success; the next check catches a
  real failure.
- A `MotionRefused` is not a failed check: never catch it.

## Phrasing a yes/no question
- One question per outcome, phrased so that "no" is the failure:
  "Is the right gripper holding a candy? Yes or No?", not "Did the grasp fail?".
- Name the object: "holding the candy" beats "holding the object".
