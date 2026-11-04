<!-- ssa-meta
description: Driving the OpenDubs base: named places, short relative moves, arms stowed first. Off unless enabled.
-->
# Navigation

**Navigation is off unless it is enabled for the session**; while it is off, `navigate_to` and
`move_base_rel` raise `MotionRefused`. The Halloween demo does not drive the base.

- Stow both arms before any base motion: `reset_robot()`. The robot refuses base motion otherwise.
- `list_places()` gives the named places on the current map (room numbers from the floor plan).
  Use a name from that list; never invent one.
- `navigate_to(place="204")` plans and drives there. `navigate_to(pose={"x": .., "y": .., "yaw_deg": ..})`
  takes a map-frame pose instead. Give exactly one.
- `move_base_rel(dx, dy, dyaw_deg)` is for short adjustments in front of a table: dx forward,
  dy left (the base strafes), meters; dyaw_deg counter-clockwise.
- After arriving, point the head (`set_head_pose`) before detecting anything: the scene has changed.
