"""Left arm hand_tcp z (root frame) over arms-up poses, from the expanded ROS V1 description."""
import math, sys
import numpy as np
import pinocchio as pin
m = pin.buildModelFromUrdf(sys.argv[1]); d = m.createData()
fid = m.getFrameId("openarm_left_hand_tcp")
J = [m.getJointId(f"openarm_left_joint{i}") for i in range(1, 8)]
def z(deg):
    q = pin.neutral(m)
    for jid, a in zip(J, deg):
        q[m.joints[jid].idx_q] = math.radians(a)
    pin.framesForwardKinematics(m, d, q)
    return d.oMf[fid].translation
print("stow (all 0):", np.round(z([0]*7), 4))
print("choreo arms-to-sky (J1 -165, J2 -25, J4 15):", np.round(z([-165, -25, 0, 15, 0, 0, 0]), 4))
for j1 in (-150, -160, -170, -180):
    for j2 in (-10, -20, -30):
        for j4 in (0, 10):
            p = z([j1, j2, 0, j4, 0, 0, 0]); print(f"J1 {j1:5d} J2 {j2:4d} J4 {j4:3d} -> z {p[2]:.4f}  xyz {np.round(p,3)}")
print("--- J2 -15")
for j1 in (-165, -168, -170, -172, -175):
    for j4 in (0, 5, 10):
        p = z([j1, -15, 0, j4, 0, 0, 0]); print(f"J1 {j1:5d} J2 -15 J4 {j4:3d} -> z {p[2]:.4f}  xyz {np.round(p,3)}")
