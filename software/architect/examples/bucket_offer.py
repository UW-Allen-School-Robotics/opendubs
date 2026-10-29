# Summary: reach down into the candy bucket on the stage in front of me from above, fingers pointing down and the gripper wide open, take a candy, lift it out, hold it out in front of me, pause, and let go.
# The Halloween hand-off. First run on opendubs2 from a spoken "trick or treat" on 2026-10-24 (show
# mode); re-aimed on 2026-10-28 for the handle-down bucket (#601) on the bucket stage (#602), standing
# on the stage's top against its RIGHT edge and 1 cm forward of its centre (measured at the robot):
# its centre about chassis [0.723, -0.064, 0.348] with the lift down (-0.098 if it is the base, not
# the rim, that meets the edge), its rim at z 0.553. The candy sits on a false bottom about 2 cm below
# the rim: with its fingers near vertical the right hand reaches no lower than about z 0.52 there (the
# shoulder, J2, at its limit), and exactly vertical not at all (the wrist, J6 and J7, runs out).
# 2026-10-29: the gripper opens fully over the bucket (it travels 40% open: fully open from rest, a
# finger passed 10 mm from the bucket on the way in), the hand goes down 2 cm nearer the bucket's
# centre so the open fingers clear its wall, and the candy is offered in front of the chest, nearer the
# middle and further out (it was out to the right), and held there 3 s before letting go. Nearer still
# to the centre (x 0.72) the wrist turns over on the last 2 cm down, at the edge of its reach.
# The arm keeps its posture by going to and from the offer through the old offer point (OUT), and
# comes back behind the bucket to a NAMED pose, the last pose of the show that ran on the robot
# (architect-93cbf2aabeca), whose swing to rest is proven: by IK, a posture a little different there
# swung the hand through the bucket's back wall on the way to rest.
# Needs this robot's arm config to let the right hand there, and the named pose; on opendubs2:
#   right: {x: [0.48, 0.88], y: [-0.45, 0.10], z: [0.50, 1.10]}
#   behind_bucket: {right: [-43.5, -4.5, 29.7, 133.5, -36.9, -39.5, -42.0]}
# The bucket is in the twin only on the description's branches (#601, #602), so this program keeps the
# hand off it by design: it goes out to the right and behind the bucket first and last, comes over it
# 11 cm above the rim, and goes straight down and straight back up, the fingers held 20 degrees off
# vertical, leaning forward and left (toward the bucket's centre, away from the robot). In the twin
# with the bucket at either place above, the hand keeps 23 mm or more off it. See the README.
DOWN = {"x": 0.242, "y": 0.242, "z": -0.940}  # the fingers' direction: 20 deg off vertical, forward and left
X, Y = 0.705, -0.07                          # over the bucket, near its centre: the open fingers clear the wall
ABOVE, CANDY = 0.66, 0.535                   # 11 cm above the rim; the candy, 2 cm below it
TRAVEL = 0.4                                 # 40% open on the way: fully open, a finger passed 10 mm from it
SIDE = (0.62, -0.40, 0.95)                   # out to the right: the way in and out, clear of the bucket
BACK = (0.50, -0.40, 0.90)                   # out to the right and behind the bucket: to and from rest
OUT = (0.75, -0.25, 1.05)                    # forward and out to the right: to and from the offer
OFFER = (0.82, -0.12, 1.05)                  # in front of the chest, a little right of centre, the arm well out
HOLD_S = 3.0                                 # held out this long before the gripper opens


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
to(*SIDE)                                  # (rest to "offer" passed 2 cm from it; rest to SIDE, 28 mm)
to(X, Y, ABOVE, DOWN)                      # over the bucket, fingers down
set_gripper_width("right", 1.0)            # wide open, above the rim
straight(ABOVE, CANDY)                     # straight down to the candy
set_gripper_width("right", 0.0)
straight(CANDY, ABOVE)                     # straight back up out of it
to(*SIDE)                                  # out to the right, above the rim the whole way
to(*OUT)                                   # forward, out to the right
to(*OFFER)                                 # and in to offer it, in front of the chest
wait(HOLD_S)                               # held out, so the hand can be met before letting go
set_gripper_width("right", 1.0)
to(*OUT)                                   # back out to the right
to(*SIDE)                                  # and further: from here straight to rest crossed the bucket
move_arm_to_named_pose("right", "behind_bucket")   # and behind it: from SIDE, the swing to rest clipped its wall
reset_robot()
