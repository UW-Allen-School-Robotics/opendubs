"""Rolling battery usage history + time-remaining projection.

Samples SoC (and pack power) over a window and projects time-to-empty (while
discharging) or time-to-full (while charging) from a smoothed **SoC slope** — a
least-squares fit over recent actual usage, which is a running average that
rejects momentary current spikes far better than an instantaneous
current÷capacity estimate. Everything degrades to None until there's enough
spread to trust a projection.

Lives in the backend (not the browser) so history accumulates across dashboard
reloads and while the POWER page isn't even open.
"""
from __future__ import annotations

import time
from collections import deque

WINDOW_S = 1800.0     # keep ~30 min of history
SAMPLE_MIN_S = 2.0    # throttle: at most one sample every 2 s (collect_fast runs ~10 Hz)
MIN_SPAN_S = 90.0     # need at least this much time spread before projecting
MIN_SLOPE = 0.05      # %/hr below this = treat as flat (idle) — no projection
SPARK_POINTS = 60     # downsampled points for the sparkline


class BatteryHistory:
    def __init__(self):
        self._s = deque()          # (t_monotonic, soc, power_w_signed)
        self._last_t = 0.0

    def sample(self, soc, power_w=None):
        """Record one point. `soc` is 0–100 (None is ignored). Safe to call at the
        full status rate — internally throttled to SAMPLE_MIN_S."""
        if soc is None:
            return
        now = time.monotonic()
        if now - self._last_t < SAMPLE_MIN_S:
            return
        self._last_t = now
        self._s.append((now, float(soc), power_w))
        cutoff = now - WINDOW_S
        while self._s and self._s[0][0] < cutoff:
            self._s.popleft()

    def _slope_pct_per_hr(self):
        """Least-squares dSoC/dt over the window in %/hr (negative = discharging)."""
        s = self._s
        if len(s) < 3:
            return None
        t0 = s[0][0]
        xs = [t - t0 for (t, _s, _p) in s]
        ys = [soc for (_t, soc, _p) in s]
        if xs[-1] - xs[0] < MIN_SPAN_S:
            return None
        n = len(xs)
        mx, my = sum(xs) / n, sum(ys) / n
        den = sum((x - mx) ** 2 for x in xs)
        if den == 0:
            return None
        slope_per_s = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den
        return slope_per_s * 3600.0

    def _downsample(self):
        s = list(self._s)
        if not s:
            return []
        base = s[0][0]
        if len(s) <= SPARK_POINTS:
            return [{"t": round(t - base), "soc": round(soc, 1)} for (t, soc, _p) in s]
        span = (s[-1][0] - base) or 1.0
        out = []
        for i in range(SPARK_POINTS):
            lo, hi = base + span * i / SPARK_POINTS, base + span * (i + 1) / SPARK_POINTS
            bucket = [soc for (t, soc, _p) in s if lo <= t < hi]
            if bucket:
                out.append({"t": round(lo - base), "soc": round(sum(bucket) / len(bucket), 1)})
        return out

    def snapshot(self):
        s = self._s
        if not s:
            return {"points": [], "rate_pct_per_hr": None, "time_remaining_s": None,
                    "eta": None, "avg_power_w": None, "power_w": None,
                    "wh_in": 0.0, "wh_out": 0.0, "span_s": 0}
        soc_now = s[-1][1]
        slope = self._slope_pct_per_hr()
        pw = [abs(p) for (_t, _s, p) in s if p is not None]
        avg_power = round(sum(pw) / len(pw), 1) if pw else None
        # Live power = most recent sample (signed: + charging / - discharging).
        power_now = next((p for (_t, _s, p) in reversed(s) if p is not None), None)
        # Energy over the window: trapezoidal integral of signed power → Wh in/out.
        wh_in = wh_out = 0.0
        pt = pp = None
        for (t, _s, p) in s:
            if pt is not None and pp is not None and p is not None:
                dwh = (p + pp) / 2.0 * (t - pt) / 3600.0
                if dwh >= 0:
                    wh_in += dwh
                else:
                    wh_out += -dwh
            pt, pp = t, p
        trs = eta = None
        if slope is not None and abs(slope) >= MIN_SLOPE:
            if slope < 0:                       # discharging → time to 0%
                trs, eta = soc_now / -slope * 3600.0, "empty"
            else:                               # charging → time to 100%
                trs, eta = (100.0 - soc_now) / slope * 3600.0, "full"
        return {
            "points": self._downsample(),
            "rate_pct_per_hr": round(slope, 2) if slope is not None else None,
            "time_remaining_s": round(trs) if trs is not None else None,
            "eta": eta, "avg_power_w": avg_power,
            "power_w": round(power_now, 1) if power_now is not None else None,
            "wh_in": round(wh_in, 1), "wh_out": round(wh_out, 1),
            "span_s": round(s[-1][0] - s[0][0]),
        }


history = BatteryHistory()
