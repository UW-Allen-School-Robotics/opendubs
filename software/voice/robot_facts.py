"""What the robot may say about itself: the dashboard's own status, read, never acted on.

The project team's rules (2026-10-20, notes/workplan-voice.md, V-D6 to V-D10):

- **Voice may say anything the dashboard shows, and never do anything the dashboard does.**
- **One source of truth.** The `robot_status` tool reads one snapshot, GET /api/status/spoken on
  the dashboard, built by the dashboard from the same functions its pages render and in their
  words. There is no separate picture of the robot here.
- **Fault explanations** come from the troubleshooting registry ("what it means", "what to do"),
  through the generated `fault_help.json` beside this file. Words only.
- **Honest.** Only live data is said. A snapshot older than STALE_S, a missing one, or a field the
  dashboard left null is said as unknown, and the model is told to say "I can't tell right now"
  for it and never to give a number that is not here.
- **The same answers for everyone.** Only the fields below are read, every string is capped and
  scrubbed, and nothing that only an operator needs is passed on: no IP or MAC addresses, network
  names, device paths or IDs, serials, emails, links, or account and people's names.

The model gets `summary()`'s text as the tool's result; nothing else of the snapshot reaches it.
"""
from __future__ import annotations

import json
import os
import re
import time

STATUS_PATH = "/api/status/spoken"
STALE_S = 5.0                       # older than this, the snapshot is not live
MAX_WORDS_LEN = 60                  # the dashboard's own caption limit (state_words.MAX_LEN)
CANT_TELL = "I can't tell right now"
HELP_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fault_help.json")

RULES = (f"Answer only from these facts. For anything marked unknown, say \"{CANT_TELL}\". Never "
         "give a number that is not here. Never offer to fix or change anything: your operator does "
         "that from the dashboard.")

# What an operator needs and a visitor must not hear. Scrubbed from every string, whatever field it
# arrives in.
_SECRETS = [
    re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?(?:/\d+)?\b"),                 # IPv4, with port or mask
    re.compile(r"\b(?:[0-9a-f]{1,4}:){2,7}[0-9a-f]{0,4}\b", re.I),                # IPv6
    re.compile(r"\b(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}\b", re.I),                   # MAC
    re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"),                              # email
    re.compile(r"\b(?:https?|wss?|ssh)://\S+", re.I),                            # links
    re.compile(r"(?<!\w)/(?:dev|sys|proc|etc|home|opt|var|tmp|run)/\S*"),        # device and file paths
    re.compile(r"\b(?:ssid|wifi|wi-fi|network name)\s*[:=]?\s*\S+", re.I),       # a network name
    re.compile(r"\b(?:user|account|login|owner)\s*[:=]\s*\S+", re.I),            # an account name
    re.compile(r"\b(?=[0-9a-f-]*\d)(?=[0-9a-f-]*[a-f])[0-9a-f]{8,}(?:-[0-9a-f]{4,})*\b", re.I),  # hex IDs, UUIDs
    re.compile(r"\b(?:sn|s/n|no|id)\s*[:#=.]?\s*[\w-]*\d[\w-]*", re.I),          # part numbers and IDs
]
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")                                      # [text](link) -> text
_MARKS = re.compile(r"[*_`#>|]+")


def scrub(text) -> str | None:
    """A string from the snapshot as it may be spoken, or None if nothing sayable is left."""
    if not isinstance(text, str):
        return None
    s = _LINK.sub(r"\1", text)
    for pat in _SECRETS:
        s = pat.sub(" ", s)
    s = _MARKS.sub("", s)
    s = re.sub(r"\s+", " ", s).strip(" ,;:-")
    if not s or not any(ch.isalpha() for ch in s):
        return None
    return s if len(s) <= MAX_WORDS_LEN else s[:MAX_WORDS_LEN].rsplit(" ", 1)[0]


def load_help(path: str = HELP_FILE) -> dict:
    """The registry's view for voice, {code: {title, means, do: [...]}}; empty if it is not there."""
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _help_text(entry: dict) -> str | None:
    """'what it means' and 'what to do' as plain sentences, scrubbed, without the length cap."""
    def plain(t):
        if not isinstance(t, str):
            return None
        s = _LINK.sub(r"\1", t)
        for pat in _SECRETS:
            s = pat.sub(" ", s)
        return re.sub(r"\s+", " ", _MARKS.sub("", s)).strip() or None
    means = plain(entry.get("means"))
    do = [d for d in (plain(x) for x in entry.get("do") or []) if d]
    if not means and not do:
        return None
    return " ".join(filter(None, [f"What it means: {means}" if means else None,
                                  "What to do: " + " ".join(do) if do else None]))


def _known(v) -> bool:
    return v is not None


def _uptime(s) -> str | None:
    if not isinstance(s, (int, float)) or isinstance(s, bool) or s < 0:
        return None
    h, m = int(s // 3600), int(s % 3600 // 60)
    if h >= 48:
        return f"{h // 24} days"
    return f"{h} hours {m} minutes" if h else f"{m} minutes"


def facts(snapshot, now: float | None = None) -> dict | None:
    """The fields voice may use, each a short phrase or None for unknown. None for no live snapshot."""
    now = time.time() if now is None else now
    if not isinstance(snapshot, dict):
        return None
    ts = snapshot.get("ts")
    if not isinstance(ts, (int, float)) or isinstance(ts, bool) or not 0 <= now - ts <= STALE_S:
        return None
    d = lambda k: snapshot.get(k) if isinstance(snapshot.get(k), dict) else {}
    state, bat, arms, show, net = d("state"), d("battery"), d("arms"), d("show"), d("network")

    pct = bat.get("percent")
    pct = int(round(pct)) if isinstance(pct, (int, float)) and not isinstance(pct, bool) and 0 <= pct <= 100 else None
    charging = bat.get("charging") if isinstance(bat.get("charging"), bool) else None
    faults = []
    for f in snapshot.get("faults") or []:
        if isinstance(f, dict) and (w := scrub(f.get("words"))):
            faults.append({"words": w, "code": f.get("code") if isinstance(f.get("code"), str) else None})
    running = show.get("running") if isinstance(show.get("running"), bool) else None
    return {
        "state": scrub(state.get("words")),
        "battery": None if pct is None else f"{pct} percent",
        "charging": None if charging is None else ("charging" if charging else "not charging"),
        "faults": faults if isinstance(snapshot.get("faults"), list) else None,
        "left arm": scrub(arms.get("left")),
        "right arm": scrub(arms.get("right")),
        "show": None if running is None else (
            ("running the show " + (scrub(show.get("name")) or "")).strip() if running else "no show running"),
        "network uplink": scrub(net.get("uplink")),
        "uptime": _uptime(snapshot.get("uptime_s")),
        "controller": scrub(snapshot.get("controller")),
    }


def summary(snapshot, now: float | None = None, help: dict | None = None) -> str:
    """The tool result the model gets: the facts, then the rules for using them."""
    f = facts(snapshot, now)
    if f is None:
        return (f"Your status cannot be read right now, so for any question about yourself say "
                f"\"{CANT_TELL}\". " + RULES)
    help = load_help() if help is None else help
    parts = []
    for k in ("state", "battery", "charging", "left arm", "right arm", "show", "network uplink",
              "uptime", "controller"):
        if k == "controller" and f[k] is None:
            continue                                   # not reported until the modes work lands
        parts.append(f"{k}: {f[k] if f[k] is not None else 'unknown'}")
    if f["faults"] is None:
        parts.append("faults: unknown")
    elif not f["faults"]:
        parts.append("faults: none")
    else:
        for fault in f["faults"]:
            line = f"fault: {fault['words']}"
            entry = help.get(fault["code"]) if fault["code"] else None
            if isinstance(entry, dict) and (h := _help_text(entry)):
                line += f" ({h})"
            parts.append(line)
    return "; ".join(parts) + ". " + RULES
