# Summary: reach down into the candy bucket on the stage in front of me from above, fingers pointing down, take a candy, lift it out, swing it out to the right and forward, pause, and let go.
# The Halloween hand-off. First run on opendubs2 from a spoken "trick or treat" on 2026-10-24 (show
# mode); re-aimed on 2026-10-28 for the bucket on the bucket stage (#602): the handle-down bucket
# (#601), its base on the stage's top centre, chassis [0.713, 0.022, 0.348] with the lift down, so
# its rim is at z 0.553. The candy has to sit about 2 cm below the rim (a false bottom): with its
# fingers near vertical the right hand reaches no lower than about z 0.52 there (the shoulder, J2, is
# at its limit), and exactly vertical not at all (the wrist, J6 and J7, runs out). Deeper, or nearer
# the centre, is refused; this spot keeps the fingers 23 mm off the wall in the twin with the bucket.
# Needs this robot's arm config to let the right hand there; on opendubs2 it is
#   right: {x: [0.48, 0.80], y: [-0.45, 0.10], z: [0.50, 1.10]}      plus the template's "offer" pose.
# The bucket is in the twin only on the description's branch (#601, #602), so this program keeps the
# hand off it by design: it goes up and out to the right first, comes over the bucket 11 cm above the
# rim, and goes straight down and straight back up, the fingers held 25 degrees off vertical, leaning
# forward and left (toward the bucket's centre, away from the robot). See the README.
DOWN = {"x": 0.3, "y": 0.3, "z": -0.906}     # the fingers' direction: 25 deg off vertical, forward and left
X, Y = 0.68, -0.01                           # over the bucket: 3 cm in front of and 3 cm right of its centre
ABOVE, CANDY = 0.66, 0.535                   # 11 cm above the rim; the candy, 2 cm below it
OPEN = 0.4                                   # 40% open in the bucket: wider, a finger meets its wall
SIDE = (0.62, -0.40, 0.95)                   # out to the right: the way in and out, clear of the bucket


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
set_gripper_width("right", OPEN)
to(*SIDE)                                  # up and out to the right first: rest to "offer" passed 2 cm from the bucket
to(X, Y, ABOVE, DOWN)                      # over the bucket, fingers down
straight(ABOVE, CANDY)                     # straight down to the candy
set_gripper_width("right", 0.0)
straight(CANDY, ABOVE)                     # straight back up out of it
to(*SIDE)                                  # out to the right, above the rim the whole way
to(0.75, -0.25, 1.05)                      # and forward to offer
wait(1.5)                                  # a pause, so the hand can be met before letting go
set_gripper_width("right", 1.0)
to(*SIDE)                                  # back out to the right: from here straight to rest crossed the bucket
reset_robot()
