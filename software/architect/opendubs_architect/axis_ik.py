"""A reach that also holds the fingers' direction: the tool point on a target AND its +z axis (out
through the fingers) along a given direction, e.g. straight down into a bucket.

ik.Kin (software/choreo, the Choreography lane's) solves either the point (`reach`) or the axis
(`point_at`), not both. This is the same damped least squares on its model, with the two stacked:
position (3 rows) and the axis error z x a (3 rows, rank 2). The roll about the axis is left free,
so a 7-joint arm has two joints of slack, spent staying near the starting pose. The joint bounds,
the shoulder envelopes and the seeds are Kin's own, so a solution is one its validator accepts.
"""
from __future__ import annotations

import math

AXIS_TOL_DEG = 2.0       # as ik.POINT_TOL_DEG: an axis that misses by more is refused
AXIS_WEIGHT = 0.3        # m per radian: how an axis error counts against a position error


def unit(v) -> list:
    n = math.sqrt(sum(float(c) ** 2 for c in v))
    if n < 1e-9:
        raise ValueError(f"an approach direction must not be zero, got {list(v)}")
    return [float(c) / n for c in v]


def _descend(kin, site, goal, axis, qadr, dadr, bounds, q_start, q_rest, iters=1500):
    mj, np = kin.mj, kin.np
    for k, a in enumerate(qadr):
        kin.d.qpos[a] = q_start[k]
    jp, jr = np.zeros((3, kin.m.nv)), np.zeros((3, kin.m.nv))
    for _ in range(iters):
        mj.mj_forward(kin.m, kin.d)
        mj.mj_jacSite(kin.m, kin.d, jp, jr, site)
        ep = goal - kin.d.site_xpos[site]
        z = kin.d.site_xmat[site].reshape(3, 3)[:, 2]
        er = np.cross(z, axis)
        e = np.concatenate([ep, AXIS_WEIGHT * er])
        J = np.vstack([jp[:, dadr], AXIS_WEIGHT * jr[:, dadr]])
        Jpinv = J.T @ np.linalg.inv(J @ J.T + 1e-4 * np.eye(6))
        N = np.eye(7) - Jpinv @ J
        q = np.array([kin.d.qpos[a] for a in qadr])
        if np.linalg.norm(ep) < 2e-4 and float(z @ axis) > math.cos(math.radians(0.1)):
            break
        dq = Jpinv @ e + N @ (0.05 * (q_rest - q))
        q = q + np.clip(dq, -0.08, 0.08)
        for k, a in enumerate(qadr):
            kin.d.qpos[a] = min(max(q[k], bounds[k][0]), bounds[k][1])
    mj.mj_forward(kin.m, kin.d)
    z = kin.d.site_xmat[site].reshape(3, 3)[:, 2]
    miss_m = float(np.linalg.norm(goal - kin.d.site_xpos[site]))
    miss_deg = math.degrees(math.acos(max(-1.0, min(1.0, float(z @ axis)))))
    return np.array([kin.d.qpos[a] for a in qadr]), miss_m, miss_deg


def solve(kin, side: str, goal, axis, reach_tol_m: float, axis_tol_deg: float = AXIS_TOL_DEG):
    """`side`'s 7 joints with its tool point on `goal` and its +z along `axis` (world frame, both),
    from the pose last set and as near it as the target allows. Returns (outward degrees {1..7},
    position miss in m, axis miss in degrees): the best found, which the caller checks."""
    mj, np = kin.mj, kin.np
    import cue
    import ik
    site = mj.mj_name2id(kin.m, mj.mjtObj.mjOBJ_SITE, ik.TOOL_SITE.format(side=side))
    jids = [kin._jid(side, j) for j in range(1, 8)]
    qadr = [kin.m.jnt_qposadr[i] for i in jids]
    dadr = [kin.m.jnt_dofadr[i] for i in jids]
    bounds = [(lo + math.radians(0.5), hi - math.radians(0.5)) for lo, hi in kin._bounds(side)]
    goal, axis = np.asarray(goal, float), np.asarray(unit(axis), float)
    q0 = np.array([kin.d.qpos[a] for a in qadr])
    best = None
    for env in kin._envelopes(bounds):
        mid = np.array([(lo + hi) / 2 for lo, hi in env])
        for seed in (q0, 0.5 * (q0 + mid), mid):
            seed = np.array([min(max(v, lo), hi) for v, (lo, hi) in zip(seed, env)])
            q, miss_m, miss_deg = _descend(kin, site, goal, axis, qadr, dadr, env, seed, q0)
            ok = miss_m <= reach_tol_m and miss_deg <= axis_tol_deg
            score = (not ok, float(np.abs(q - q0).sum()) if ok else miss_m + miss_deg / 100.0)
            if best is None or score < best[0]:
                best = (score, q, miss_m, miss_deg)
    _, q, miss_m, miss_deg = best
    for k, a in enumerate(qadr):
        kin.d.qpos[a] = q[k]
    mj.mj_forward(kin.m, kin.d)
    sign = {j: (-1 if side == "left" and j in cue.MIRRORED_JOINTS else 1) for j in range(1, 8)}
    return {j: round(math.degrees(q[j - 1]) * sign[j], 2) for j in range(1, 8)}, miss_m, miss_deg
