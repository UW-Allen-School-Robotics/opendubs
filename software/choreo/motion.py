"""The shape of an arm move between keyframes — ONE definition, used by the robot and the twins alike.

Until 2026-10-08 a show's arm path was straight lines between keyframes: constant speed, with the
speed flipping INSTANTLY at every reversal. The speed test (plan 6.1) found the joints overrun the
commanded speed by 1.5-1.6x coming out of such a corner, and the wrists fall more than 5 deg behind
there on a beat. So the path is now smoothed — per joint, a shape-preserving cubic through the
keyframes (monotone cubic Hermite with minmod slopes):

  * every keyframe is still REACHED EXACTLY AT ITS TIME — the format's timing is unchanged;
  * where a joint reverses, or starts or ends a hold, it slows to zero and starts again smoothly,
    instead of flipping speed in an instant;
  * where a joint passes through a keyframe still going the same way, it keeps moving (no stop);
  * it never overshoots: between two keyframes a joint stays between their two angles;
  * the speed is continuous, and a segment's peak is at most 1.5x its AVERAGE (exactly 1.5x for a
    move that starts and ends at rest) — inside the 1.875x that cue.py's trip refusal assumes, so
    that refusal stays exactly as strict as it was.

The validator's speed checks are on segment AVERAGES and are unchanged; what changes is only the
shape inside a segment.
"""
from __future__ import annotations

PEAK_TO_AVERAGE = 1.5          # the most a smoothed segment's speed exceeds its average


def knots(keyframes: list, key: str, start=None) -> list:
    """[(time, [7 deg])] from keyframes {start, t, <key>: {"1".."7": deg}}, beginning at `start`
    (default zero) at time 0. A keyframe whose `start` is after the previous arrival adds a knot for
    the hold, so the joint rests there and sets off at `start`."""
    prev = list(start) if start else [0.0] * 7
    out = [(0.0, list(prev))]
    for kf in keyframes:
        pose = [float(kf[key][str(j)]) for j in range(1, 8)]
        if kf["start"] > out[-1][0] + 1e-9:
            out.append((float(kf["start"]), list(prev)))
        if kf["t"] > out[-1][0] + 1e-9:
            out.append((float(kf["t"]), pose))
        else:                                   # arrives where the last knot is: replace it
            out[-1] = (out[-1][0], pose)
        prev = pose
    return out


def _minmod(a: float, b: float) -> float:
    """The knot's speed: zero at a reversal or the edge of a hold, else the gentler neighbour."""
    if a * b <= 0.0:
        return 0.0
    return a if abs(a) < abs(b) else b


def slopes(ks: list) -> list:
    """Per knot, per joint: deg/s. Zero at the first and last knot."""
    n = len(ks)
    out = [[0.0] * 7 for _ in range(n)]
    for i in range(1, n - 1):
        (t0, a), (t1, b), (t2, c) = ks[i - 1], ks[i], ks[i + 1]
        for j in range(7):
            d1 = (b[j] - a[j]) / (t1 - t0) if t1 > t0 else 0.0
            d2 = (c[j] - b[j]) / (t2 - t1) if t2 > t1 else 0.0
            out[i][j] = _minmod(d1, d2)
    return out


class Path:
    """A smoothed arm path: `at(t)` -> [7 deg], `speed(t)` -> [7 deg/s]."""

    def __init__(self, ks: list):
        self.ks, self.m = ks, slopes(ks)

    def _seg(self, t):
        ks = self.ks
        if t <= ks[0][0]:
            return None, 0.0
        if t >= ks[-1][0]:
            return None, 1.0
        lo, hi = 0, len(ks) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if ks[mid][0] <= t:
                lo = mid
            else:
                hi = mid
        return lo, (t - ks[lo][0]) / (ks[lo + 1][0] - ks[lo][0])

    def at(self, t: float) -> list:
        i, s = self._seg(t)
        if i is None:
            return list(self.ks[0][1] if s == 0.0 else self.ks[-1][1])
        (t0, p0), (t1, p1) = self.ks[i], self.ks[i + 1]
        h = t1 - t0
        h00, h10, h01, h11 = 2*s**3 - 3*s**2 + 1, s**3 - 2*s**2 + s, -2*s**3 + 3*s**2, s**3 - s**2
        return [h00 * p0[j] + h10 * h * self.m[i][j] + h01 * p1[j] + h11 * h * self.m[i + 1][j]
                for j in range(7)]

    def speed(self, t: float) -> list:
        i, s = self._seg(t)
        if i is None:
            return [0.0] * 7
        (t0, p0), (t1, p1) = self.ks[i], self.ks[i + 1]
        h = t1 - t0
        d00, d10, d01, d11 = 6*s**2 - 6*s, 3*s**2 - 4*s + 1, -6*s**2 + 6*s, 3*s**2 - 2*s
        return [(d00 * p0[j] + d01 * p1[j]) / h + d10 * self.m[i][j] + d11 * self.m[i + 1][j]
                for j in range(7)]

    @property
    def end(self) -> float:
        return self.ks[-1][0]
