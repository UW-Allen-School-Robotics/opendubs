<!-- ssa-meta
description: Handing an object to a person on OpenDubs: get_hand_pose, place_in_hand, and what to do when no hand appears.
-->
# Handing an object to a person

The hand tracker and its controller do the hard part. A program never computes a pose near a
person itself: it never calls `move_ee_to_pose` toward a hand. It calls `place_in_hand(arm)`,
which tracks the hand, moves the gripper into it, opens and retracts.

## Pattern
```python
say("Hold out your hand!")
result = place_in_hand("right", timeout_s=15.0)
if result["released"]:
    say("Happy Halloween!")
else:
    say("I'll keep this one for the next trick-or-treater.")
move_arm_to_named_pose("right", "stow")
```

## Rules
- Hold the object first and verify the grasp (verification.md), then hand it over.
- `released: False` means no steady hand appeared in time: the arm has already retracted and is
  still holding the object. Do not call `place_in_hand` again in the same run; finish the run.
- `get_hand_pose()` is read-only. Use it to decide what to say ("I see your hand!"), not to move.
- Corrections like "hand it over higher" or "closer to the person" are about the handoff
  controller, which ARCHITECT does not tune: say so in the reply instead of changing the program.
