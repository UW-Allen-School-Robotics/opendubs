"""The robot's state in plain words on the face, for people who don't know the colour code.

The face shows a caption it is sent (head protocol: CAPTION <text> | CAPTION OFF). The words are
decided here, where the real reason is known: DEGRADED says WHAT is wrong, in a bystander's words, and
CHARGING says how full the pack is. state_out.py sends the caption with every STATE line.

The Head & face setting `state_words`:
  always            every state has its words
  hide_auto_manual  (default) not while the robot is driven or running by itself, when the words would
                    only repeat what anyone can see; a fault and an e-stop still say so
  off               no words at all
"""
from __future__ import annotations

MODES = ("always", "hide_auto_manual", "off")
DEFAULT_MODE = "hide_auto_manual"
MAX_LEN = 60                    # the protocol's limit: a longer caption is refused, never cut

WORDS = {
    "INIT": "Starting up",
    "SAFE_IDLE": "Ready",
    "MANUAL_CONTROL": "Manual control",
    "AUTONOMOUS_CONTROL": "Running a show",
    "CHARGING": "Charging",
    "E_STOP_LATCH": "Emergency stop",
}
HIDDEN_BY_DEFAULT = ("MANUAL_CONTROL", "AUTONOMOUS_CONTROL")

# robot_state's faults (status.py, supervisor.py), as a bystander would say them. One that is not
# listed gets the generic words rather than its raw name, which is written for the maintainers.
FAULTS = {
    "CONTACTOR WELDED": "power cut-off not working",
    "PDH brownout": "battery voltage dropped",
    "PDH hardware": "power board fault",
    "OVERTEMP": "running too hot",
    "lift module not responding": "lift not responding",
    "TELEMETRY NOT READY": "controller not responding",
}
NO_RC = "RC transmitter not connected"
GENERIC = "needs a check"


def problem(rsd: dict) -> str:
    """What is wrong with a DEGRADED robot, in plain words."""
    inputs = rsd.get("inputs") or {}
    faults = [f for f in inputs.get("faults") or [] if isinstance(f, str)]
    if faults:
        said = []
        for f in faults:
            w = FAULTS.get(f, GENERIC)
            if w not in said:
                said.append(w)
        text = "Problem: " + ", ".join(said)
        return text if len(text) <= MAX_LEN else "Problem: " + said[0]
    if not inputs.get("link", True) or inputs.get("authority") is None:
        return "Problem: " + NO_RC
    return "Problem: " + GENERIC


# How to get out of an e-stop, by what caused it: the troubleshooting registry's words (ESTOP_SOFTWARE,
# ESTOP_BUTTON, ESTOP_REARM_CH5 in docs/reference/registry/safety.yaml, #430), for the dashboard, and a
# caption within the protocol's 60 characters for the face. A stale controller link has no recovery an
# operator can do from here, so it keeps the plain words.
RECOVERY = {
    "SOFTWARE": ("To re-enable: switch CH5 (mode) to OFF, then re-arm from the dashboard.",
                 "E-stop: switch CH5 to OFF, then re-arm on the dashboard"),
    "STAMPLC": ("To re-enable: switch CH5 (mode) to OFF, then re-arm from the dashboard.",
                "E-stop: switch CH5 to OFF, then re-arm on the dashboard"),
    "BUTTON": ("An e-stop button is pressed: twist it to release. Then switch CH5 (mode) to OFF and "
               "re-arm from the dashboard.",
               "E-stop button pressed: release it, CH5 OFF, re-arm"),
}


def recovery(rsd: dict) -> tuple[str, str] | None:
    """(dashboard sentence, caption) for an E_STOP_LATCH, or None. A pressed button comes first: it has
    to be released before a re-arm can do anything. A stale link: None."""
    sources = (rsd.get("inputs") or {}).get("estop_sources") or []
    if rsd.get("state") != "E_STOP_LATCH" or "STALE TELEMETRY" in sources:
        return None
    for s in ("BUTTON", "SOFTWARE", "STAMPLC"):
        if s in sources:
            return RECOVERY[s]
    return None


def caption(shown: str | None, rsd: dict, soc, mode: str = DEFAULT_MODE) -> str | None:
    """The words for `shown` (state_out.present()), or None for none.

    `rsd`: robot_state's rollup, for DEGRADED's reason. `soc`: the pack's charge, 0..100, or None."""
    if mode not in MODES:
        mode = DEFAULT_MODE
    if mode == "off" or not shown:
        return None
    if mode == "hide_auto_manual" and shown in HIDDEN_BY_DEFAULT:
        return None
    if shown == "DEGRADED":
        return problem(rsd or {})
    if shown == "E_STOP_LATCH":
        r = recovery(rsd or {})
        if r:
            return r[1]
    if shown == "CHARGING":
        try:
            pct = int(round(float(soc)))
        except (TypeError, ValueError):
            pct = None
        return f"Charging {pct}%" if pct is not None and 0 <= pct <= 100 else "Charging"
    return WORDS.get(shown)


def line(text: str | None) -> str:
    """The head line for a caption: `CAPTION <text>` or `CAPTION OFF`."""
    return f"CAPTION {text}" if text else "CAPTION OFF"
