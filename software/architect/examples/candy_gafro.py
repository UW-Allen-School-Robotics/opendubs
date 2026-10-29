# Summary: look down at the candy bucket, take a candy from it with my right hand, look for a hand held out to me, and put the candy in it.
# The Halloween hand-off on tobi's arm service (the gafro backend, gafro_robot.py): each call is one goal
# to the arm service as the program runs, not a recorded show. The bucket is where bucket_offer.py
# found it on opendubs2 (2026-10-28): the handle-down bucket on the bucket stage, against its RIGHT
# edge and 1 cm forward of its centre, its rim at z 0.553, the candy on a false bottom about 2 cm
# below it. The pick is that show's, move for move: out to the right and behind the bucket first, over
# it 11 cm above the rim, straight down and up with the fingers held 20 degrees off vertical. The arm
# service's own collision check does not know the bucket, the head or the camera mount: this program is
# checked on the twin with them (README).
DOWN = {"x": 0.242, "y": 0.242, "z": -0.940}  # the fingers' direction: 20 deg off vertical, forward and left
X, Y = 0.70, -0.09                           # over the bucket: 2 cm in front of and 3 cm right of its centre
ABOVE, CANDY = 0.66, 0.535                   # 11 cm above the rim; the candy, 2 cm below it
OPEN = 0.4                                   # 40% open in the bucket: fully open, a finger met its wall
SIDE = (0.62, -0.40, 0.95)                   # out to the right: the way in and out, clear of the bucket
BACK = (0.50, -0.40, 0.90)                   # out to the right and behind the bucket: from ready
LOOK_AT_BUCKET = (0, -25)                    # pan, tilt (+ up): the head tipped down at the bucket (at 30 the
                                             # right shoulder link passes 29.5 mm from it, under 30)
LOOK_FOR_HAND = (0, -15)                     # a little down: a hand held out is low in a level picture


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


reset_robot()                              # the right arm at ready, where the arm service starts it
set_head_pose(*LOOK_AT_BUCKET)
set_gripper_width("right", OPEN)
to(*BACK)                                  # out to the right, behind the bucket
to(*SIDE)
to(X, Y, ABOVE, DOWN)                      # over the bucket, fingers down
straight(ABOVE, CANDY)                     # straight down to the candy
set_gripper_width("right", 0.0)            # close on it
straight(CANDY, ABOVE)                     # straight back up out of the bucket
to(*SIDE)                                  # out to the right, above the rim
set_head_pose(*LOOK_FOR_HAND)
place_in_hand("right", timeout_s=20.0)     # a hand held out and still: reach toward it, pause, open, ready
set_head_pose(0, 0)
