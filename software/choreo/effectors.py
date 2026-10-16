#!/usr/bin/env python3
"""The tool registry: what can be fitted at the end of an arm (or parked on the base), what a show may
ask of it, and where a crawling hand goes.

    python3 effectors.py list                  # every tool type, its channels, poses and modes
    python3 effectors.py check [FILE]          # check a robot's fitted-tools file (default: the robot's)
    python3 effectors.py template              # print an empty fitted-tools file to fill in

A TOOL TYPE is one YAML file in effectors/: its channels (each a 0..1 range or a set of named states, with
how long a full stroke takes), named poses, finger-dance patterns, where it can be fitted (`mounts`:
left / right are the arm flanges, base is a floor-level dock on the chassis), what it can do there
(`modes`: equipped, crawl), its twin model, and its driver (`output`; null = none yet, twin only).
A type may be another one MIRRORED (`mirror_of`): the left Surge hand is the right one reflected.

WHAT IS FITTED is a property of each robot, so it lives ON THE ROBOT, like the gripper calibration:
`$OPENDUBS_TOOLS`, else /etc/opendubs/tools.yaml. No file means today's robot: the OpenArm gripper on
both arms and nothing on the base, so every show written before the registry means what it meant.

A SHOW says what it needs with `tools:` ({left|right|base: <type> or {type, mode}}). GRIP_L / GRIP_R are
"the tool on that side": `open:` and `do: open|close` work on every tool (each type maps them through its
`open_map`), and `pose:` / `set:` reach the rest of its channels. A crawling hand is driven by the CRAWL
slot (cue.py), which plays ONLY IN THE TWIN; this module predicts its path for the validator, the
composer's floor plan and the render.
"""
from __future__ import annotations

import copy
import math
import os
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
REG_DIR = os.path.join(HERE, "effectors")
DEFAULT_FITTED_PATH = "/etc/opendubs/tools.yaml"
MOUNTS = ("left", "right", "base")
FLANGES = ("left", "right")
MODES = ("equipped", "crawl")
KINDS = ("gripper", "hand", "suction", "pneumatic")
SLOT_MOUNT = {"GRIP_L": "left", "GRIP_R": "right"}
DEFAULT_FITTED = {"left": "openarm_gripper", "right": "openarm_gripper"}

TEMPLATE = """\
# The tools fitted to THIS robot. Measured on it (look at it); never copied from another robot.
# Types are the files in software/choreo/effectors/ (`effectors.py list`). Check with: effectors.py check
robot: <name>
checked: <YYYY-MM-DD>
left: openarm_gripper       # the left arm's flange
right: openarm_gripper      # the right arm's flange
# base: surge_right         # a tool parked at the chassis's floor dock (a crawling hand), if any
"""


class RegistryError(ValueError):
    pass


# ---- the registry -------------------------------------------------------------------------------------
class Tool:
    """One tool type, as its YAML describes it (a mirrored type already resolved)."""

    def __init__(self, doc: dict):
        self.doc = doc
        self.id = doc["id"]
        self.name = doc.get("name", self.id)
        self.kind = doc.get("kind")
        self.mounts = tuple(doc.get("mounts") or ())
        self.modes = tuple(doc.get("modes") or ())
        self.output = doc.get("output")
        self.channels = doc.get("channels") or {}
        self.poses = doc.get("poses") or {}
        self.patterns = doc.get("patterns") or {}
        self.open_map = doc.get("open_map") or {}
        self.sim = doc.get("sim") or {}
        self.crawl = doc.get("crawl")
        self.mirrored = bool(doc.get("_mirrored"))
        self.hand = doc.get("hand")

    def __repr__(self):
        return f"Tool({self.id})"

    # -- channels ---------------------------------------------------------------------------------
    def is_states(self, ch: str) -> bool:
        return "states" in self.channels[ch]

    def check_value(self, ch: str, v) -> str | None:
        """None if `v` is a value channel `ch` can take, else why not."""
        spec = self.channels.get(ch)
        if spec is None:
            return f"{self.id} has no channel {ch!r} (it has {', '.join(self.channels)})"
        if "states" in spec:
            return None if v in spec["states"] else f"{ch} must be one of {', '.join(spec['states'])}, not {v!r}"
        lo, hi = spec.get("range", (0, 1))
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not (lo <= v <= hi):
            return f"{ch} {v!r} must be within {lo:g}..{hi:g}"
        return None

    def rest(self) -> dict:
        """Every channel's value before any cue: the `stance` pose for a crawler, else the open pose."""
        name = (self.crawl or {}).get("stance") if self.crawl else None
        base = self.poses.get(name) or self.poses.get("open") or {}
        out = {}
        for ch, spec in self.channels.items():
            out[ch] = base.get(ch, spec["states"][0] if "states" in spec else spec.get("range", (0, 1))[0])
        return out

    def open_channels(self, x: float) -> dict | str:
        """`open: x` (and do: open|close) on this tool, as channel values — or why it cannot be said."""
        om = self.open_map
        if "channel" in om:
            ch = om["channel"]
            if "states" in om:
                key = "1" if x >= 1.0 - 1e-9 else "0" if x <= 1e-9 else None
                if key is None:
                    return f"{self.id} is open or closed, nothing between: open {x:g} cannot be done (0 or 1)"
                return {ch: om["states"][key]}
            return {ch: (1.0 - x) if om.get("invert") else x}
        if "from" in om and "to" in om:
            a, b = self.poses[om["from"]], self.poses[om["to"]]
            return {ch: round(a.get(ch, 0.0) + (b.get(ch, 0.0) - a.get(ch, 0.0)) * x, 4) for ch in set(a) | set(b)}
        return f"{self.id} has no open_map: say pose: or set: instead of open:"

    def stroke_s(self, ch: str) -> float:
        return float(self.channels[ch].get("full_stroke_s", 0.5))

    def travel_s(self, a: dict, b: dict) -> float:
        """How long the slowest channel takes to get from values `a` to `b` (state channels: a full stroke)."""
        worst = 0.0
        for ch, v in b.items():
            if ch not in self.channels or ch not in a:
                continue
            if self.is_states(ch):
                d = 0.0 if a[ch] == v else 1.0
            else:
                lo, hi = self.channels[ch].get("range", (0, 1))
                d = abs(float(v) - float(a[ch])) / ((hi - lo) or 1.0)
            worst = max(worst, d * self.stroke_s(ch))
        return worst


def _mirror(src: dict, doc: dict) -> dict:
    """A left-handed type from a right-handed one (or back): everything carried over except what the
    mirror changes — the hand's side, the two pivots swapped, signed drifts negated, the heading loop's
    gain sign flipped. The geometry is mirrored by sim/hand.py."""
    out = copy.deepcopy(src)
    out.update({k: v for k, v in doc.items() if k != "mirror_of"})
    out["_mirrored"] = True
    out["_mirror_of"] = src["id"]
    cr = out.get("crawl")
    if cr:
        piv = cr.get("pivots") or {}
        if piv:
            cr["pivots"] = {"left": piv.get("right"), "right": piv.get("left")}
            cr["pivots"] = {k: v for k, v in cr["pivots"].items() if v}
        for g in (cr.get("gaits") or {}).values():
            hc = (g.get("sim_params") or {}).get("heading_ctrl")
            if hc:
                hc["plant_sign"] = -float(hc.get("plant_sign", -1.0))
    return out


def _check_doc(d: dict, path: str) -> list[str]:
    p = []
    w = os.path.basename(path)
    if d.get("kind") not in KINDS:
        p.append(f"{w}: kind {d.get('kind')!r} not one of {', '.join(KINDS)}")
    for m in d.get("mounts") or []:
        if m not in MOUNTS:
            p.append(f"{w}: mount {m!r} not one of {', '.join(MOUNTS)}")
    for m in d.get("modes") or []:
        if m not in MODES:
            p.append(f"{w}: mode {m!r} not one of {', '.join(MODES)}")
    if "crawl" in (d.get("modes") or []) and not d.get("crawl"):
        p.append(f"{w}: can crawl but has no crawl: block (speeds and their provenance)")
    if d.get("crawl") and "SIM" not in str(d["crawl"].get("provenance", "")).upper():
        p.append(f"{w}: crawl numbers must carry their provenance (and say they are from simulation)")
    chans = d.get("channels") or {}
    if not chans:
        p.append(f"{w}: no channels")
    for name, pose in (d.get("poses") or {}).items():
        for ch in pose:
            if ch not in chans:
                p.append(f"{w}: pose {name!r} names {ch!r}, not a channel")
    for name, pat in (d.get("patterns") or {}).items():
        if pat.get("base") and pat["base"] not in (d.get("poses") or {}):
            p.append(f"{w}: pattern {name!r} starts from {pat['base']!r}, not a pose")
        for st in pat.get("steps") or []:
            for ch in st:
                if ch not in chans:
                    p.append(f"{w}: pattern {name!r} names {ch!r}, not a channel")
    return p


_REG: dict | None = None


def registry(reg_dir: str | None = None) -> dict[str, Tool]:
    """{id: Tool} from every YAML in the registry folder; raises RegistryError on a bad file."""
    global _REG
    if _REG is not None and reg_dir is None:
        return _REG
    folder = reg_dir or REG_DIR
    raw = {}
    for f in sorted(os.listdir(folder)):
        if not f.endswith(".yaml"):
            continue
        with open(os.path.join(folder, f), encoding="utf-8") as fh:
            d = yaml.safe_load(fh) or {}
        if d.get("id") != f[:-5]:
            raise RegistryError(f"{f}: id {d.get('id')!r} must match the file name")
        raw[d["id"]] = (d, os.path.join(folder, f))
    out, problems = {}, []
    for tid, (d, path) in raw.items():
        if "mirror_of" in d:
            src = raw.get(d["mirror_of"])
            if not src or "mirror_of" in src[0]:
                problems.append(f"{tid}: mirror_of {d['mirror_of']!r} is not a plain tool type")
                continue
            d = _mirror(src[0], d)
        problems += _check_doc(d, path)
        out[tid] = Tool(d)
    if problems:
        raise RegistryError("; ".join(problems))
    if reg_dir is None:
        _REG = out
    return out


# ---- what is fitted on a robot --------------------------------------------------------------------------
def fitted_path() -> str:
    return os.environ.get("OPENDUBS_TOOLS") or DEFAULT_FITTED_PATH


def load_fitted(path: str | None = None) -> dict:
    """{mount: type id} for this robot. No file: today's robot (DEFAULT_FITTED)."""
    p = path or fitted_path()
    if not os.path.isfile(p):
        return dict(DEFAULT_FITTED)
    with open(p, encoding="utf-8") as fh:
        d = yaml.safe_load(fh) or {}
    reg = registry()
    out = {}
    for m in MOUNTS:
        v = d.get(m)
        if v is None:
            continue
        if v not in reg:
            raise RegistryError(f"{p}: {m}: {v!r} is not a tool type ({', '.join(reg)})")
        if m not in reg[v].mounts:
            raise RegistryError(f"{p}: {v} cannot be fitted at {m} (its mounts: {', '.join(reg[v].mounts)})")
        out[m] = v
    return out


def show_tools(doc_tools) -> tuple[dict, list[str]]:
    """A show's `tools:` -> ({mount: {type, mode}}, problems). Absent: today's grippers, equipped."""
    if doc_tools is None:
        return {m: {"type": t, "mode": "equipped"} for m, t in DEFAULT_FITTED.items()}, []
    if not isinstance(doc_tools, dict):
        return {}, ["tools: must map left / right / base to a tool type"]
    reg, out, p = registry(), {m: {"type": t, "mode": "equipped"} for m, t in DEFAULT_FITTED.items()}, []
    for m, v in doc_tools.items():
        if m not in MOUNTS:
            p.append(f"tools: unknown mount {m!r} (left, right: the arm flanges; base: the chassis dock)")
            continue
        if v is None:
            out.pop(m, None)
            continue
        spec = {"type": v} if isinstance(v, str) else dict(v) if isinstance(v, dict) else None
        if not spec or spec.get("type") not in reg:
            p.append(f"tools: {m}: {v!r} is not a tool type ({', '.join(reg)})")
            continue
        tool = reg[spec["type"]]
        unknown = set(spec) - {"type", "mode"}
        if unknown:
            p.append(f"tools: {m}: unknown key(s) {', '.join(sorted(unknown))} (type, mode)")
        mode = spec.get("mode") or ("crawl" if m == "base" else "equipped")
        if m not in tool.mounts:
            p.append(f"tools: {tool.id} cannot be fitted at {m} (its mounts: {', '.join(tool.mounts)})")
        if mode not in tool.modes:
            p.append(f"tools: {tool.id} has no {mode!r} mode (it has {', '.join(tool.modes)})")
        if mode == "crawl" and m != "base":
            p.append(f"tools: a crawling tool starts from the base dock, not the {m} flange — the arm cannot "
                     "set a hand on the floor (the tool point bottoms out ~0.65 m up)")
        if mode == "equipped" and m == "base":
            p.append("tools: the base dock holds a tool that crawls (mode: crawl); equipped tools go on a flange")
        out[m] = {"type": tool.id, "mode": mode}
    return out, p


def fitted_mismatch(need: dict, fitted: dict) -> list[str]:
    """Where a show's tools differ from what a robot has fitted."""
    p = []
    for m in MOUNTS:
        want = (need.get(m) or {}).get("type")
        have = fitted.get(m)
        if want != have:
            p.append(f"the show needs {want or 'nothing'} at {m}; this robot has {have or 'nothing'} fitted there")
    return p


# ---- the crawling hand: where it goes ---------------------------------------------------------------------
# The robot's footprint for the floor checks: the chassis box from the robot description
# (description/opendubs/dimensions.yaml, CAD x -32..612 mm, y +-324.5 mm), in the frame the base pose is
# given in (sim_preview.base_pose: the robot's starting frame, x forward, y left). The arms are not counted:
# they never come within ~0.65 m of the floor.
CHASSIS_X = (-0.032, 0.612)
CHASSIS_Y = (-0.3245, 0.3245)
BASE_TURN_X = (0.12 + 0.528) / 2.0     # the base turns about its wheels' centre (sim_preview.apply_base)
FOOTPRINT_MARGIN_M = 0.03
LEASH_SLACK_M = 0.15                   # a leash is never asked to go taut: the hand stalls, it cannot pull
DOCK_TOL_M, DOCK_TOL_DEG = 0.08, 15.0
CRAWLER_KEYS = {"dock", "tether", "area", "leash"}
CRAWL_DOS = ("walk", "turn", "pause", "dance")
DEFAULT_DOCK = (CHASSIS_X[1] + 0.20, 0.0, 0.0)   # in front of the chassis (7 cm clear of the hand), facing forward
DEFAULT_ANCHOR = (CHASSIS_X[1], 0.0, 0.10)       # the front face of the chassis, 100 mm above the floor


def base_to_world(base: tuple, px: float, py: float) -> tuple:
    """A point in the chassis frame -> the floor frame, with the base at pose (x, y, yaw deg)."""
    bx, by, yaw = base
    th = math.radians(yaw)
    dx, dy = px - BASE_TURN_X, py
    c, s = math.cos(th), math.sin(th)
    return bx + BASE_TURN_X + c * dx - s * dy, by + s * dx + c * dy


def footprint_gap(base: tuple, x: float, y: float) -> float:
    """Distance (m) from a floor point to the chassis box at a base pose; negative inside."""
    bx, by, yaw = base
    th = -math.radians(yaw)
    rx, ry = x - bx - BASE_TURN_X, y - by
    c, s = math.cos(th), math.sin(th)
    lx, ly = c * rx - s * ry + BASE_TURN_X, s * rx + c * ry
    dx = max(CHASSIS_X[0] - lx, 0.0, lx - CHASSIS_X[1])
    dy = max(CHASSIS_Y[0] - ly, 0.0, ly - CHASSIS_Y[1])
    if dx == 0.0 and dy == 0.0:
        return -min(lx - CHASSIS_X[0], CHASSIS_X[1] - lx, ly - CHASSIS_Y[0], CHASSIS_Y[1] - ly)
    return math.hypot(dx, dy)


def crawler_config(doc_crawler, tool: Tool) -> tuple[dict, list[str]]:
    """A show's `crawler:` block, checked and with its defaults filled in."""
    p = []
    d = doc_crawler if isinstance(doc_crawler, dict) else {}
    if doc_crawler is not None and not isinstance(doc_crawler, dict):
        p.append("crawler: must be a mapping (dock, tether, area)")
    for k in sorted(set(d) - CRAWLER_KEYS, key=str):
        p.append(f"crawler: unknown key {k!r} ({', '.join(sorted(CRAWLER_KEYS - {'leash'}))})")
    dock = d.get("dock", list(DEFAULT_DOCK))
    if not (isinstance(dock, list) and len(dock) == 3 and all(isinstance(v, (int, float)) for v in dock)):
        p.append("crawler: dock must be [x m, y m, heading deg] in the robot's starting frame")
        dock = list(DEFAULT_DOCK)
    teth = d.get("tether") or {}
    anchor = teth.get("anchor", "base_front")
    if anchor != "base_front":
        p.append(f"crawler: tether anchor {anchor!r} — only base_front is modelled (the chassis's front face)")
    length = teth.get("length_m")
    if not (isinstance(length, (int, float)) and 0.3 <= length <= 5.0):
        p.append("crawler: tether length_m must be 0.3..5 m — the leash that keeps the hand by the robot")
        length = 1.8
    area = d.get("area")
    box = None
    if area is not None:
        try:
            box = (tuple(float(v) for v in area["x"]), tuple(float(v) for v in area["y"]))
            assert box[0][0] < box[0][1] and box[1][0] < box[1][1]
        except Exception:  # noqa: BLE001
            p.append("crawler: area must be {x: [min, max], y: [min, max]} in metres, in the robot's starting frame")
            box = None
    else:
        p.append("crawler: declare the floor area the hand may use — area: {x: [min, max], y: [min, max]} (m); "
                 "a human clears it before the show")
    gap = footprint_gap((0.0, 0.0, 0.0), dock[0], dock[1])
    r = float(tool.crawl.get("hand_radius_m", 0.13)) if tool.crawl else 0.13
    if gap < r - 0.02:
        p.append(f"crawler: the dock is {gap:.2f} m from the chassis — the hand ({r:g} m) would be inside it")
    return {"dock": tuple(float(v) for v in dock), "anchor": DEFAULT_ANCHOR, "length_m": float(length),
            "trailing": True, "area": box, "hand_r": r}, p


def _gait(tool: Tool, name: str):
    return ((tool.crawl or {}).get("gaits") or {}).get(name)


def speed_range(tool: Tool, gait: str, trailing: bool) -> tuple:
    g = _gait(tool, gait)
    key = "dragging_m_s" if trailing and "dragging_m_s" in g else "speed_m_s"
    lo, hi = g[key]
    return float(lo), float(hi), key


def pivot_range(tool: Tool, side: str, trailing: bool) -> tuple:
    pv = (tool.crawl.get("pivots") or {}).get(side)
    if not pv:
        return None
    key = "dragging_deg_s" if trailing and "dragging_deg_s" in pv else "deg_s"
    lo, hi = pv[key]
    return float(lo), float(hi), key


def _wrap(a: float) -> float:
    return (a + 180.0) % 360.0 - 180.0


def plan(tool: Tool, cfg: dict, events: list, beat_s: float | None) -> dict:
    """The crawl, leg by leg, from the CRAWL events (time-sorted, already shape-checked): each leg's start,
    its SLOWEST-case duration (what it occupies on the timeline), its nominal speed, and where the hand
    ends. Positions are the hand's centre on the floor, (x, y) m and heading deg, in the robot's starting
    frame. Returns {legs, problems, notices}."""
    x, y, h = cfg["dock"]
    legs, P, N = [], [], []
    trailing = cfg["trailing"]
    for e in events:
        c, t = e["fields"], float(e["t"])
        do, where = c.get("do"), e["where"]
        leg = {"t": t, "do": do, "from": (x, y, h), "where": where}
        if do == "walk":
            direction = c.get("dir", "forward")
            gait = c.get("gait") or (tool.crawl.get("default_gait") if direction == "forward" else "back")
            g = _gait(tool, gait)
            if g is None or g.get("dir") != direction:
                P.append((e, f"{where}: {tool.id} has no {direction} gait {gait!r} "
                              f"(gaits: {', '.join(k + ' (' + v['dir'] + ')' for k, v in tool.crawl['gaits'].items())})"))
                continue
            if trailing and "dragging_m_s" not in g:
                P.append((e, f"{where}: gait {gait!r} has no simulated speed on a trailing leash — use one that does"))
                continue
            lo, hi, key = speed_range(tool, gait, trailing)
            nominal = (lo + hi) / 2.0
            sgn = 1.0 if direction == "forward" else -1.0
            to = c.get("to")
            heading_to = c.get("heading_deg")
            if to is not None:
                tx, ty = (cfg["dock"][0], cfg["dock"][1]) if to == "dock" else (float(to[0]), float(to[1]))
                dx, dy = tx - x, ty - y
                along = sgn * (dx * math.cos(math.radians(h)) + dy * math.sin(math.radians(h)))
                across = -dx * math.sin(math.radians(h)) + dy * math.cos(math.radians(h))
                if along < -0.01:
                    P.append((e, f"{where}: {to if to == 'dock' else 'the target'} is behind a {direction} walk — "
                                  f"walk {'back' if direction == 'forward' else 'forward'}, or pivot first"))
                    continue
                if abs(across) > 0.05:
                    bearing = math.degrees(math.atan2(dy, dx)) if direction == "forward" else math.degrees(math.atan2(-dy, -dx))
                    P.append((e, f"{where}: {'the dock' if to == 'dock' else 'the target'} is {abs(across)*100:.0f} cm off the "
                                  f"hand's line (it faces {h:.0f}°, the target bears {bearing:.0f}°) — turn "
                                  f"{_wrap(bearing - h):+.0f}° first; a crawling hand walks straight"))
                    continue
                dist = max(0.0, along)
            elif "dist_m" in c:
                dist = float(c["dist_m"])
            else:
                dist = float(c["secs"]) * nominal
            secs_slow = dist / lo if "secs" not in c else float(c["secs"])
            if "secs" in c and dist > 0 and to is None and "dist_m" not in c:
                d_lo, d_hi = float(c["secs"]) * lo, float(c["secs"]) * hi
            else:
                d_lo = d_hi = dist
            steer = 0.0
            if heading_to is not None:
                if not g.get("steered"):
                    P.append((e, f"{where}: gait {gait!r} cannot be steered — pivot to turn"))
                    continue
                steer = _wrap(float(heading_to) - h)
                need = abs(steer) / float(g["steer_deg_s"])
                if need > secs_slow + 1e-9:
                    P.append((e, f"{where}: turning {steer:+.0f}° while walking takes {need:.0f}s at "
                                  f"{g['steer_deg_s']:g}°/s (sim) — this walk lasts {secs_slow:.0f}s; pivot instead"))
            # nominal path: heading ramps at steer rate (if asked) over the walk
            x1, y1, h1 = _integrate_walk(x, y, h, sgn * dist, steer)
            leg.update(docks=to == "dock", targeted=to is not None, gait=gait, dir=direction, dist_m=dist, dist_range=(d_lo, d_hi), speed=(lo, hi), speed_key=key,
                       secs=secs_slow, nominal_secs=dist / nominal if nominal else secs_slow, steer=steer,
                       drift_m=float(g.get("drift_m_per_m", 0.05)) * d_hi, to=(x1, y1, h1))
            how = (f"on a trailing leash; SIM, not hardware" if key.startswith("dragging") else "free floor; SIM, not hardware")
            if d_lo != d_hi:
                N.append(f"CRAWL {where}: walk {direction} for {secs_slow:g}s with the {gait} gait at {lo*100:.1f}–{hi*100:.1f} cm/s "
                         f"({how}) — it covers {d_lo*100:.0f}–{d_hi*100:.0f} cm (the twin shows {dist*100:.0f})")
            else:
                N.append(f"CRAWL {where}: walk {direction} {dist*100:.0f} cm with the {gait} gait at {lo*100:.1f}–{hi*100:.1f} cm/s "
                         f"({how}) — it takes {dist/hi:.0f}–{dist/lo:.0f}s; the timeline holds {secs_slow:.0f}s")
            x, y, h = x1, y1, h1
        elif do == "turn":
            by = float(c["by_deg"])
            side = "left" if by > 0 else "right"
            pr = pivot_range(tool, side, trailing)
            if pr is None:
                P.append((e, f"{where}: {tool.id} has no {side} pivot"))
                continue
            lo, hi, key = pr
            secs = abs(by) / lo
            drift = float(tool.crawl["pivots"][side].get("drift_m_per_s", 0.005)) * secs
            leg.update(by=by, side=side, rate=(lo, hi), secs=secs, nominal_secs=abs(by) / ((lo + hi) / 2),
                       drift_m=drift, to=(x, y, _wrap(h + by)))
            N.append(f"CRAWL {where}: pivot {by:+.0f}° ({side}) at {lo:.1f}–{hi:.1f}°/s (SIM, not hardware"
                     f"{'; scaled for the leash' if key.startswith('dragging') else ''}) — {abs(by)/hi:.0f}–{secs:.0f}s, "
                     f"drifting up to {drift*100:.0f} cm")
            h = _wrap(h + by)
        elif do == "pause":
            leg.update(secs=float(c.get("secs", 1.0)), to=(x, y, h), drift_m=0.0)
        elif do == "dance":
            secs = float(c["secs"]) if "secs" in c else float(c["beats"]) * (beat_s or 0.5)
            leg.update(pattern=c["pattern"], secs=secs, to=(x, y, h), drift_m=0.0)
        legs.append(leg)
    return {"legs": legs, "problems": P, "notices": N, "end": (x, y, h)}


def _integrate_walk(x, y, h, signed_dist, steer, n=None):
    """Walk `signed_dist` (m; negative backwards) while the heading changes by `steer` deg at a constant rate."""
    n = n or max(1, int(abs(signed_dist) / 0.01))
    for i in range(n):
        hh = math.radians(h + steer * (i + 0.5) / n)
        x += signed_dist / n * math.cos(hh)
        y += signed_dist / n * math.sin(hh)
    return x, y, _wrap(h + steer)


def state_at(legs: list, dock: tuple, t: float, tool: Tool | None = None, beat_s: float | None = None) -> dict:
    """Where the hand is and what it is doing at show time t, on the NOMINAL path (mid-range sim speeds):
    {x, y, heading, moving: None|gait|pivot_left|pivot_right, phase_s, pattern, pattern_t}."""
    x, y, h = dock
    st = {"x": x, "y": y, "heading": h, "moving": None, "phase_s": 0.0, "pattern": None, "pattern_t": 0.0,
          "docked": True}
    for leg in legs:
        if t < leg["t"]:
            break
        st["docked"] = bool(leg.get("docks")) and t >= leg["t"] + leg["nominal_secs"]
        dt = t - leg["t"]
        x0, y0, h0 = leg["from"]
        do = leg["do"]
        if do == "walk":
            dur = leg["nominal_secs"]
            f = min(1.0, dt / dur) if dur > 0 else 1.0
            sgn = 1.0 if leg["dir"] == "forward" else -1.0
            xx, yy, hh = _integrate_walk(x0, y0, h0, sgn * leg["dist_m"] * f, leg["steer"] * f)
            st.update(x=xx, y=yy, heading=hh, moving=leg["gait"] if f < 1.0 else None, phase_s=dt)
        elif do == "turn":
            dur = leg["nominal_secs"]
            f = min(1.0, dt / dur) if dur > 0 else 1.0
            st.update(x=x0, y=y0, heading=_wrap(h0 + leg["by"] * f),
                      moving=("pivot_" + leg["side"]) if f < 1.0 else None, phase_s=dt)
        elif do == "dance":
            st.update(x=x0, y=y0, heading=h0, moving=None)
            if dt < leg["secs"]:
                st.update(pattern=leg["pattern"], pattern_t=dt)
            else:
                st.update(pattern=None)
        else:
            st.update(x=x0, y=y0, heading=h0, moving=None, pattern=None)
    return st


def pattern_channels(tool: Tool, name: str, t: float, beat_s: float) -> dict:
    """A finger dance's channel values t seconds in: each step held for step_beats, approached at the
    channels' own speed from the step before."""
    pat = tool.patterns[name]
    vals = dict(tool.rest())
    vals.update(tool.poses.get(pat.get("base"), {}))
    step_s = float(pat.get("step_beats", 1)) * beat_s
    steps = pat["steps"]
    k = int(t // step_s) if step_s > 0 else 0
    prev = dict(vals)
    # every step applied in turn up to the current one: a step names only what it changes
    for i in range(k):
        prev.update(steps[i % len(steps)])
    target = dict(prev)
    target.update(steps[k % len(steps)])
    into = t - k * step_s
    out = {}
    for ch, v in target.items():
        a = prev.get(ch, v)
        if isinstance(v, str) or isinstance(a, str):
            out[ch] = v
            continue
        rate = 1.0 / tool.stroke_s(ch)
        d = v - a
        out[ch] = v if abs(d) <= rate * into else a + math.copysign(rate * into, d)
    return out


# ---- CLI -----------------------------------------------------------------------------------------------
def describe() -> str:
    lines = []
    for t in registry().values():
        lines.append(f"{t.id}: {t.name} — {t.kind}; mounts {', '.join(t.mounts)}; modes {', '.join(t.modes)}; "
                     f"driver {t.output or 'NONE (twin only)'}" + (f"; mirrored from {t.doc['_mirror_of']}" if t.mirrored else ""))
        for ch, spec in t.channels.items():
            rng = "|".join(spec["states"]) if "states" in spec else "{:g}..{:g}".format(*spec.get("range", (0, 1)))
            lines.append(f"    channel {ch}: {rng}, full stroke {spec.get('full_stroke_s', '?')} s")
        if t.poses:
            lines.append(f"    poses: {', '.join(t.poses)}")
        if t.patterns:
            lines.append(f"    finger dances: {', '.join(t.patterns)}")
        if t.crawl:
            gaits = t.crawl.get("gaits") or {}
            lines.append("    crawl (SIM, not hardware): " + "; ".join(
                f"{k} {v['dir']} {v['speed_m_s'][0]*100:.1f}-{v['speed_m_s'][1]*100:.1f} cm/s" for k, v in gaits.items())
                + "; pivots " + ", ".join(f"{k} {v['deg_s'][0]:g}-{v['deg_s'][1]:g} deg/s"
                                          for k, v in (t.crawl.get("pivots") or {}).items()))
    return "\n".join(lines)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("cmd", choices=("list", "check", "template"))
    ap.add_argument("path", nargs="?")
    a = ap.parse_args(argv)
    if a.cmd == "template":
        sys.stdout.write(TEMPLATE)
        return 0
    if a.cmd == "list":
        print(describe())
        return 0
    try:
        f = load_fitted(a.path)
    except RegistryError as e:
        print(f"FAIL: {e}")
        return 1
    src = a.path or fitted_path()
    print(("no file at " + src + " — today's robot: " if not os.path.isfile(src) else src + ": ")
          + ", ".join(f"{m} {t}" for m, t in f.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
