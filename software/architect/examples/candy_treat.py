# Summary: take a candy from the bucket with my right hand, look for a hand held out to me, and put the candy in it.
# The Halloween hand-off as Archit's own sequence (treat_sequence.py on daphne/treat-gripper, through the treat
# services): ready, the candy straight down from ready, the line on the Jabra, his node's head finding the person,
# the hand-off, ready. What is left here is what his sequence does not do: with nobody or no hand found, the candy
# goes back in the bucket the way candy_gafro.py takes it out (the bucket as measured there, 2026-10-28/29), so every
# run ends with the gripper open.
DOWN = {"x": 0.242, "y": 0.242, "z": -0.940}  # the fingers' direction: 20 deg off vertical, forward and left
X, Y, ABOVE = 0.705, -0.07, 0.66             # over the bucket, 11 cm above the rim
SIDE = (0.62, -0.40, 0.95)                   # out to the right, clear of the bucket
BACK = (0.50, -0.40, 0.90)                   # out to the right and behind the bucket: from ready
LOOK_AT_BUCKET = (0, -25)                    # pan, tilt (+ up)


def to(x, y, z, approach=None):
    pose = {"position": {"x": x, "y": y, "z": z}, "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0}}
    if approach:
        pose["approach"] = approach
    move_ee_to_pose("right", pose)


given = treat_sequence(say="Now hold out your hand for your treat!")
if not given["released"]:                  # at ready, still holding the candy: back in the bucket
    set_head_pose(*LOOK_AT_BUCKET)
    to(*BACK)
    to(*SIDE)
    to(X, Y, ABOVE, DOWN)
    set_gripper_width("right", 1.0)        # dropped from 11 cm above the rim
    to(*SIDE)
    reset_robot()
    set_head_pose(0, 0)
