# Summary: raise my right hand, open and close its gripper, and put the arm back down.
# The first program for the physical robot: arms only, no perception (OPENDUBS_ARCHITECT_PROFILE=arms).
reset_robot()
ws = get_workspace()["right"]
clamp = lambda v, r: max(r[0], min(r[1], v))
target = {"position": {"x": clamp(0.45, ws["x"]), "y": clamp(-0.25, ws["y"]), "z": clamp(0.85, ws["z"])},
          "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0}}
move_ee_to_pose("right", target)
set_gripper_width("right", 1.0)
set_gripper_width("right", 0.0)
move_ee_to_rel_pose("right", {"x": 0.0, "y": 0.0, "z": 0.05})
reset_robot()
