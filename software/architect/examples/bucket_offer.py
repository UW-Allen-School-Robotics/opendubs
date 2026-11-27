# Summary: reach down into the candy bucket on the stage in front of me from above, fingers pointing down and the gripper wide open, take a candy, wait for a secure grasp, lift it higher straight out, swing it forward and to the right, hold completely still pointing straight down for a kid to put their hand under it, let go, pause while still holding still for them to retrieve it safely, and return to rest.
# The Halloween hand-off, updated for the handle-down bucket (#601) on the bucket stage (#602)
# against its right edge (centre about chassis [0.705, -0.07], rim at z 0.553, candy at z 0.535).
# 
# Sequence:
# 1. Approach: opens partially to TRAVEL (0.4) on the way in, moves behind and right of the bucket
#    (BACK -> SIDE), hovers over the rim (ABOVE = 0.66), and opens fully (1.0).
# 2. Grasp: goes straight down with fingers 20 degrees off vertical (DOWN) to the candy (0.535),
#    closes the gripper (0.0), and waits 0.8 s for the fingers to firmly secure the candy.
# 3. Extraction: ascends straight back up higher (CANDY -> LIFT_ABOVE = 0.74 m, ~19 cm above rim)
#    in vertical steps keeping the gripper angled downward to clear the rim safely without tilting.
# 4. Delivery: swings out to the right (SIDE) and moves forward and to the right (DROP_OFF = [0.70, -0.28, 0.95]),
#    holding the gripper perpendicular to the ground (pointing straight down).
# 5. Motionless hand-off:
#    - Pauses 2.0 s completely stationary before dropping so the child can safely place their hand underneath.
#    - Opens the gripper (1.0) to release candy straight down into the child's hand.
#    - Pauses 2.5 s completely stationary after dropping so the child can safely retrieve the candy and withdraw hand.
# 6. Return: swings back via SIDE to the verified "behind_bucket" named pose, then resets to rest.

DOWN = {"x": 0.242, "y": 0.242, "z": -0.940}  # fingers 20 deg off vertical, pointing downward into bucket
STRAIGHT_DOWN = {"x": 0.0, "y": 0.0, "z": -1.0}  # fingers pointing straight down perpendicular to ground
X, Y = 0.705, -0.07                          # over the bucket, near its centre
ABOVE, CANDY = 0.66, 0.535                   # approach 11 cm above rim; candy 2 cm below it
LIFT_ABOVE = 0.74                            # lift higher when exiting (~19 cm above rim) before swinging out
TRAVEL = 0.4                                 # 40% open on approach so fingers don't clip the rim
SIDE = (0.62, -0.40, 0.95)                   # out to the right: clear of the bucket
BACK = (0.50, -0.40, 0.90)                   # out to the right and behind the bucket: to and from rest
DROP_OFF = (0.70, -0.28, 0.95)               # forward and to the right: kid's hand range (41 in off floor)
PAUSE_GRASP_S = 0.8                          # settle grasp before vertical lift
PAUSE_BEFORE_DROP_S = 2.0                    # hold completely still before drop (>= 1.5s requirement)
PAUSE_AFTER_DROP_S = 2.5                     # hold completely still after drop before moving (>= 2.0s requirement)


def to(x, y, z, approach=None):
    pose = {"position": {"x": x, "y": y, "z": z}, "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0}}
    if approach:
        pose["approach"] = approach
    move_ee_to_pose("right", pose)


def straight(z_from, z_to):
    """Down or up over the bucket in 2 cm steps, the fingers held down, so the hand stays on the vertical."""
    step = -0.02 if z_to < z_from else 0.02
    z = z_from
    while abs(z_to - z) > 0.005:
        z = round(z + step, 3) if abs(z_to - z) > 0.02 else z_to
        to(X, Y, z, DOWN)


reset_robot()
set_gripper_width("right", TRAVEL)
to(*BACK)                                  # up and out to the right, behind the bucket, first
to(*SIDE)                                  # clear approach path
to(X, Y, ABOVE, DOWN)                      # over the bucket, fingers pointing down
set_gripper_width("right", 1.0)            # open wide above the rim
straight(ABOVE, CANDY)                     # straight down to grasp candy
set_gripper_width("right", 0.0)            # clamp candy
wait(PAUSE_GRASP_S)                        # brief pause for grasp to settle firmly
straight(CANDY, LIFT_ABOVE)                # straight back up higher out of bucket, keeping gripper down
to(*SIDE)                                  # swing out to the right, clear of the bucket
to(*DROP_OFF, STRAIGHT_DOWN)               # forward and to the right, pointing perpendicular to the ground
wait(PAUSE_BEFORE_DROP_S)                  # stay completely still (2.0s) so the child can position hand
set_gripper_width("right", 1.0)            # release candy straight down into hand
wait(PAUSE_AFTER_DROP_S)                   # stay completely still (2.5s) so the child withdraws hand
to(*SIDE)                                  # retract back towards the side
move_arm_to_named_pose("right", "behind_bucket")  # safe posture behind bucket avoiding stage collision
reset_robot()
