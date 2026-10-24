# A parallel-jaw grasp, in the frame of the header it comes with (the camera's optical frame:
# x right, y down, z forward). GraspNet convention for the orientation: the pose's x axis is the
# approach direction, y the closing direction, z = x cross y. Position is the grasp centre.
geometry_msgs/Pose pose
float32 width                # jaw opening, metres
float32 score                # AnyGrasp's raw score
float32 upright_score        # 1 = side grasp with the fingers closing horizontally; NaN if up was unknown
float32 tilt_from_floor_deg  # approach angle from the floor: 0 side, 90 top-down; NaN if up was unknown
