"""Robot-level + subsystem FSM derivation for the MASTER STATUS page.

Maps live telemetry onto the hierarchical FSM defined in docs/state-machines.md.
The robot-level state is a *derived view* of the authoritative inputs — the RC
authority switch (CH5: OFF/RC/AUTO), the deadman (CH6), the safety latch
(software e-stop + e-stop button + contactor), RC-link health, and subsystem
faults. This is an INDICATOR only; safety authority stays in hardware + StamPLC
(docs/control-authority.md). The same state→color/animation map is the intended
single source of truth for the PRISM LED scheme.

`derive()` is a pure function so it's trivially testable; status.collect() gathers
the live inputs and calls it.
"""
from __future__ import annotations

# Robot-level states (docs/state-machines.md)
ROBOT_STATES = ["INIT", "SAFE_IDLE", "MANUAL_CONTROL",
                "AUTONOMOUS_CONTROL", "DEGRADED", "E_STOP_LATCH"]

# state -> presentation (color/animation = the LED scheme; sev = worst-wins rank)
STATE_STYLE = {
    "INIT":               {"label": "INIT",        "color": "white", "anim": "sweep",   "sev": 1},
    "SAFE_IDLE":          {"label": "SAFE IDLE",    "color": "green", "anim": "breathe", "sev": 2},
    "MANUAL_CONTROL":     {"label": "MANUAL",       "color": "white", "anim": "solid",   "sev": 3},
    "AUTONOMOUS_CONTROL": {"label": "AUTONOMOUS",   "color": "cyan",  "anim": "solid",   "sev": 4},
    "DEGRADED":           {"label": "DEGRADED",     "color": "amber", "anim": "solid",   "sev": 8},
    "E_STOP_LATCH":       {"label": "E-STOP LATCH", "color": "red",   "anim": "flash",   "sev": 9},
}

# Partial-enablement matrix (docs/state-machines.md). Value = whether a subsystem
# is permitted to be enabled in that robot mode ("on"), conditionally ("cond"),
# or forced safe ("off").
ENABLE = {
    #                     base    mast    left_arm right_arm
    "INIT":              ("off",  "off",  "off",  "off"),
    "SAFE_IDLE":         ("off",  "off",  "off",  "off"),
    "MANUAL_CONTROL":    ("on",   "cond", "off",  "off"),
    "AUTONOMOUS_CONTROL":("on",   "on",   "cond", "cond"),
    "DEGRADED":          ("cond", "cond", "cond", "cond"),
    "E_STOP_LATCH":      ("off",  "off",  "off",  "off"),
}
SUBSYS_KEYS = ["base", "mast", "left_arm", "right_arm"]
SUBSYS_LABELS = {"base": "MOBILE BASE", "mast": "MAST",
                 "left_arm": "LEFT ARM", "right_arm": "RIGHT ARM"}


def interpret_authority(ch, rng):
    """CH5 (index 4) 3-position switch: OFF (low) / RC_MANUAL (mid) / AUTONOMOUS (high)."""
    if not ch or len(ch) < 5:
        return None
    raw = ch[4]
    c = rng["center"]
    q = (rng["max"] - rng["min"]) * 0.25
    if raw < c - q:
        return "OFF"
    if raw > c + q:
        return "AUTONOMOUS"
    return "RC_MANUAL"


def interpret_deadman(ch, rng):
    """CH6 (index 5): armed when above center."""
    if not ch or len(ch) < 6:
        return None
    return ch[5] > rng["center"]


def _subsys(robot, idx, has_fault, active=False):
    """Map robot mode + fault + activity → subsystem FSM state
    (DISABLED / READY / ACTIVE / FAULT)."""
    if robot == "E_STOP_LATCH":
        return "DISABLED"          # forced safe
    if has_fault:
        return "FAULT"
    perm = ENABLE.get(robot, ("off",) * 4)[idx]
    if perm == "off":
        return "DISABLED"
    if perm == "cond":
        return "READY"             # conditionally permitted; not actively driven
    return "ACTIVE" if active else "READY"


def derive(*, booting, estop_sources, authority, deadman, link, faults,
           motion_enabled=None, contactor=None, welded=False,
           mast_fault=False, base_fault=False, charging=False):
    """Pure FSM rollup. Returns the robot-level state + presentation + subsystem
    states + the inputs that drove it. `estop_sources` is the list of active
    e-stop CAUSES (e.g. ["SOFTWARE"], ["BUTTON"], or both); `faults` is a list of
    human-readable fault strings (empty = none)."""
    faults = faults or []
    estop_sources = estop_sources or []
    estopped = bool(estop_sources)

    # Transition priority: safety first, then boot, then faults, then mode select.
    if estopped:
        state = "E_STOP_LATCH"
        reason = "e-stop latched · source: " + " + ".join(estop_sources)
        if welded:   # most dangerous case: commanded off, but power is NOT actually cut
            reason += "  ⚠ CONTACTOR WELDED — power NOT cut"
    elif booting:
        state = "INIT"
        reason = "boot — waiting for telemetry / ready checks"
    elif faults:
        state = "DEGRADED"
        reason = "fault: " + ", ".join(faults)
    elif charging:
        # Charger connected → motion interlock. A soft inhibit (not an
        # e-stop latch): you don't drive a plugged-in robot, but the brain stays live.
        state = "SAFE_IDLE"
        reason = "charging — drive inhibited"
    elif authority == "OFF":
        # Operator intentionally set the authority switch OFF → benign idle.
        state = "SAFE_IDLE"
        reason = "authority switch OFF"
    elif not link or authority is None:
        # UNEXPECTED loss of the control link (RC NOLINK / FAILSAFE) — an alarm, not benign
        # idle (the StamPLC already police-flashes it). NOTE: this only fires if the receiver
        # actually SIGNALS the loss; a receiver that holds the last frames keeps the robot in
        # its prior state — fix that in the TX/RX failsafe config (park CH5→OFF, sticks→neutral).
        state = "DEGRADED"
        reason = "RC link lost / failsafe"
    elif authority == "RC_MANUAL":
        state = "MANUAL_CONTROL"
        reason = "RC manual" + ("" if deadman else " — DEADMAN NOT ARMED (outputs gated)")
    elif authority == "AUTONOMOUS":
        state = "AUTONOMOUS_CONTROL"
        reason = "autonomy engaged"
    else:
        state = "SAFE_IDLE"
        reason = "default safe"

    style = STATE_STYLE[state]

    # Subsystem rollup. Base motion in MANUAL also requires deadman; treat as
    # ACTIVE only when permitted AND deadman armed (we lack per-subsystem motion
    # feedback yet, so ACTIVE is best-effort).
    base_active = state in ("MANUAL_CONTROL", "AUTONOMOUS_CONTROL") and bool(deadman)
    subs = {
        "base":      {"state": _subsys(state, 0, base_fault, active=base_active)},
        "mast":      {"state": _subsys(state, 1, mast_fault)},
        "left_arm":  {"state": _subsys(state, 2, False)},
        "right_arm": {"state": _subsys(state, 3, False)},
    }
    for k in SUBSYS_KEYS:
        subs[k]["label"] = SUBSYS_LABELS[k]

    return {
        "state": state,
        "label": style["label"],
        "color": style["color"],
        "anim": style["anim"],
        "sev": style["sev"],
        "reason": reason,
        "states": ROBOT_STATES,
        "inputs": {
            "authority": authority, "deadman": deadman, "link": link,
            "estopped": estopped, "estop_sources": estop_sources, "faults": faults,
            "contactor": contactor, "motion_enabled": motion_enabled, "welded": welded,
            "charging": charging,
        },
        "subsystems": subs,
    }
