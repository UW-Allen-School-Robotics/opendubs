"""The people guard: refuse autonomous base motion toward anything close to the robot.

drive_bridge.py asks it before each DRIVE. A command is refused (the bridge sends zero) when a LIDAR
return lies within `reach` of the robot's outline AND on the side the robot is moving toward; when
the robot is turning, anything within reach on any side blocks, because a turning chassis sweeps its
corners. The robot's own returns are removed first (scan_body_filter.BOX, chassis plus 10 cm).

It FAILS CLOSED: if any watched scan is older than `stale_s`, every non-zero command is refused, so a
LIDAR that stops publishing stops autonomous motion rather than blinding the guard.

This is not the safety system. The firmware still gates host DRIVE on CH5 AUTO, a held deadman and a
0.35 ceiling. The guard keeps software motion off the people a show draws in. It sees legs and
furniture at LIDAR height (about 0.33 m) and does NOT see glass, nor anything above or below
that plane.

Pure Python: no ROS, so it can be tested on a laptop.
"""
import math

from scan_body_filter import BOX, mask

REACH_M = 0.5        # how close is too close, from the robot's outline
STALE_S = 0.5        # a scan older than this blinds the guard
EPS = 1e-3           # below this a command axis counts as zero


def scan_points(ranges, angle_min, angle_inc, tx, ty, yaw, box=BOX):
    """Finite returns of one scan as (x, y) in base_link, the robot's own returns removed."""
    pts = []
    for i, r in enumerate(mask(ranges, angle_min, angle_inc, tx, ty, yaw, box)):
        if math.isfinite(r) and r > 0:
            a = angle_min + i * angle_inc + yaw
            pts.append((tx + r * math.cos(a), ty + r * math.sin(a)))
    return pts


def blocking(points, fwd, strafe, rot, box=BOX, reach=REACH_M):
    """The nearest point that should stop this command, as (x, y, distance from the outline), or None.

    fwd/strafe/rot are the command's axes in any units; only their signs and mix matter."""
    if abs(fwd) < EPS and abs(strafe) < EPS and abs(rot) < EPS:
        return None
    x0, x1, y0, y1 = box
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    turning = abs(rot) >= EPS
    n = math.hypot(fwd, strafe)
    dx, dy = (fwd / n, strafe / n) if n >= EPS else (0.0, 0.0)
    best = None
    for px, py in points:
        ox, oy = max(x0 - px, 0.0, px - x1), max(y0 - py, 0.0, py - y1)
        d = math.hypot(ox, oy)                       # distance from the outline (0 inside it)
        if d > reach:
            continue
        ahead = (px - cx) * dx + (py - cy) * dy > 0
        if (turning or ahead) and (best is None or d < best[2]):
            best = (px, py, d)
    return best


def verdict(scans, now, fwd, strafe, rot, reach=REACH_M, stale_s=STALE_S):
    """(allowed, reason) for a command, given {topic: (stamp_s or None, points)} for every watched scan."""
    if abs(fwd) < EPS and abs(strafe) < EPS and abs(rot) < EPS:
        return True, ""
    for topic, (stamp, _pts) in sorted(scans.items()):
        if stamp is None or now - stamp > stale_s:
            return False, f"{topic} is not current: the guard is blind, so nothing moves"
    pts = [p for _stamp, ps in scans.values() for p in ps]
    hit = blocking(pts, fwd, strafe, rot, reach=reach)
    if hit:
        return False, f"something {hit[2]:.2f} m from the robot at ({hit[0]:.2f}, {hit[1]:.2f}) in base_link"
    return True, ""
