"""Per-robot joint limits for the bring-up tools: what this robot's own surroundings allow that the arm's model cannot know (cables behind the arm, a rest, a mount).

It is the file the Choreography lane's validator and IK already read, `OPENDUBS_ARM_LIMITS` (default /etc/opendubs/arm-limits/joint_limits.yaml): a mapping
`openarm_<left|right>_joint<N>: {lower: rad, upper: rad}`. The tools use it to NARROW the model's range and never to widen it. A joint the file does not name keeps the
model's range, so a file may list only the joints that need it (the Choreography tools want every joint listed: a file shared with them should be a whole one).
Absent is not an error here but is not silent either: goto_hand and track_hand print that this robot's limits are unknown to them.
"""
from __future__ import annotations

import os

import numpy as np
import yaml

DEFAULT = os.environ.get("OPENDUBS_ARM_LIMITS", "/etc/opendubs/arm-limits/joint_limits.yaml")


def load(path: str | None = None) -> dict:
    """joint name -> (lower, upper), {} if there is no file. A file that is there but wrong is an error: a limit that silently fails to apply is worse than none."""
    path = path or DEFAULT
    if not os.path.exists(path):
        return {}
    with open(path) as handle:
        raw = yaml.safe_load(handle) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: not a mapping of joint name to {{lower, upper}}")
    out = {}
    for name, entry in raw.items():
        try:
            out[str(name)] = (float(entry["lower"]), float(entry["upper"]))
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"{path}: {name!r} needs numeric lower and upper") from None
    return out


def narrowed(side: str, lower, upper, limits: dict):
    """The model's (lower, upper) for one arm's seven joints, narrowed by `limits`; and the numbers of the joints it narrowed."""
    lower, upper = np.array(lower, dtype=float), np.array(upper, dtype=float)
    changed = []
    for j in range(7):
        entry = limits.get(f"openarm_{side}_joint{j + 1}")
        if entry is None:
            continue
        new_lower, new_upper = max(lower[j], entry[0]), min(upper[j], entry[1])
        if (new_lower, new_upper) != (lower[j], upper[j]):
            changed.append(j + 1)
        lower[j], upper[j] = new_lower, new_upper
    return lower, upper, changed


def apply(side: str, lower, upper, path: str | None = None):
    """(lower, upper, note) for the plan: the model's range narrowed by the per-robot file, and one sentence saying what was applied or that nothing was."""
    path = path or DEFAULT
    found = load(path)
    if not found:
        return np.array(lower, dtype=float), np.array(upper, dtype=float), (
            f"Per-robot joint limits: none (no file at {path}). Cables, a rest or a mount behind or beside this arm are NOT known to this tool; only the model's ranges apply.")
    lower, upper, changed = narrowed(side, lower, upper, found)
    return lower, upper, (f"Per-robot joint limits from {path}: {len(found)} joints named, narrowing joint(s) "
                          f"{', '.join(map(str, changed)) if changed else 'none of this arm'}.")
