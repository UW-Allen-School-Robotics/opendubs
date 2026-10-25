# Summary: reach up and over into the candy bucket in front of me, take a candy, lift it out, swing it out to the right and forward, and let go.
# The Halloween hand-off, run on opendubs2 from a spoken "trick or treat" on 2026-10-24 (show mode).
# A 9 in bucket in front of the arm column, its back edge a few cm in front of it, on a chair and the
# base: centre about [0.60, 0.0] in the chassis frame, rim 25 in off the floor (chassis z 0.541; the
# twin's floor is at z -0.094), candy an inch or two below it. Needs this robot's arm config to let the
# right hand there; on opendubs2 it was
#   right: {x: [0.48, 0.80], y: [-0.45, 0.0], z: [0.48, 1.10]}      plus the template's "offer" pose.
# The bucket is not in the twin, so nothing but this program keeps the hand off it: it lifts clear to
# a joint-space pose first, comes over the rim 12 cm above it, and goes straight down. See the README.
def to(x, y, z):
    move_ee_to_pose("right", {"position": {"x": x, "y": y, "z": z}, "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0}})

reset_robot()
set_gripper_width("right", 1.0)
move_arm_to_named_pose("right", "offer")   # lift clear first: forward and up, out in front of the bucket
to(0.62, -0.06, 0.66)      # then over the rim (6 cm right of and 2 cm in front of the bucket's centre:
to(0.62, -0.06, 0.58)      # nearer the centre line the wrist folds to 17.7 mm from the arm column)
to(0.62, -0.06, 0.50)      # the candy, an inch or two below the rim
set_gripper_width("right", 0.0)
to(0.62, -0.06, 0.58)      # straight back up out of it
to(0.62, -0.06, 0.66)
to(0.62, -0.40, 0.95)      # out to the right, above the rim the whole way
to(0.75, -0.25, 1.05)      # and forward to offer
set_gripper_width("right", 1.0)
reset_robot()
