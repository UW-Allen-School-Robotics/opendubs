import math, sys
import numpy as np
import pinocchio as pin
m = pin.buildModelFromUrdf(sys.argv[1]); d = m.createData()
fid = m.getFrameId("openarm_left_hand_tcp")
J = [m.getJointId(f"openarm_left_joint{i}") for i in range(1, 8)]
A = np.array([0, 0, 0, 0, 0, 0, 0.0]); B = np.array([-165, -15, 0, 10, 0, 0, 0.0])
lo = hi = None
for s in np.linspace(0, 1, 201):
    q = pin.neutral(m)
    for jid, a in zip(J, A + (B - A) * s):
        q[m.joints[jid].idx_q] = math.radians(a)
    pin.framesForwardKinematics(m, d, q)
    p = d.oMf[fid].translation.copy()
    lo = p if lo is None else np.minimum(lo, p); hi = p if hi is None else np.maximum(hi, p)
print("path TCP min", np.round(lo, 3), "max", np.round(hi, 3))
