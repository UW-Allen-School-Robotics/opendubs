"""The only things a language model may ask the robot to do.

**A model gets the face, not the body.** This is the hard line of notes/design/face-design.md ("No
LLM authority over motion") and a decision of the project team: motion is never commanded by a
language model, and routines by voice stay off. So there is no motion tool here to switch on, and
no code path that reaches one.

The effects, all of them:

  set_expression  -> `EMOTE <NAME>`   to the face (eyes on a screen)
  face_gesture    -> `MOVE <name>`    to the face (eyes on a screen; the head does not turn)
  robot_status    -> GET /api/choreo/robot on the dashboard, READ-ONLY, so the robot can say
                     truthfully whether it is e-stopped
  end_conversation-> the voice service ends the conversation

1. **Closed vocabularies, from the protocol's own copy.** Names come from
   software/head-protocol/head_protocol.py, so this list cannot drift from what the face accepts.
   A value outside an enum is refused, never passed through, which also closes the route of a
   newline smuggled into a UDP line ("SOFT\\nGAZE 0 0").
2. **Every reachable effect is enumerable** (`every_possible_effect`), and the tests fail if any
   head verb outside ALLOWED_HEAD_VERBS, or any HTTP method but GET, can be produced.
3. **Rate-limited.** A model that loops on a face tool cannot strobe the face: at most FACE_BURST
   face lines per FACE_WINDOW_S.
4. **Never raises on bad input.** A model's output is untrusted; a bad call becomes a refusal that
   goes back to the model as an error result.

Ported from the voice study's prototype (tools/voice-bench/safe_tools.py, PR #217), without its
optional routine request: the team has kept routines by voice off.
"""
from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, field

try:
    import head_protocol as hp          # beside this file in the container image
except ImportError:                     # a checkout: the protocol package two levels up
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "head-protocol"))
    import head_protocol as hp  # noqa: E402

ALLOWED_HEAD_VERBS = frozenset({"EMOTE", "MOVE"})
ALLOWED_HTTP = frozenset({("GET", "/api/choreo/robot")})
FACE_BURST, FACE_WINDOW_S = 4, 2.0

EMOTE_NAMES = [e.lower() for e in hp.EMOTES] + ["auto"]
MOVE_NAMES = list(hp.MOVES)


@dataclass
class Action:
    """What a tool call causes. Exactly one effect field is set, or `refused`."""
    head_line: str | None = None        # one head-protocol line, to the face door
    http: tuple | None = None           # (method, path) on the dashboard; GET only
    end_conversation: bool = False
    refused: str | None = None          # why not; returned to the model as an error result
    result_hint: str = ""               # what to tell the model on success


@dataclass
class Limiter:
    burst: int = FACE_BURST
    window_s: float = FACE_WINDOW_S
    _times: list = field(default_factory=list)

    def ok(self, now: float) -> bool:
        self._times[:] = [t for t in self._times if now - t < self.window_s]
        if len(self._times) >= self.burst:
            return False
        self._times.append(now)
        return True


def tool_definitions() -> list[dict]:
    """The tools in the Claude Messages API shape. `strict` keeps arguments schema-valid; the
    enums are the protocol's own vocabularies. `openai_tools()` converts for a local server."""
    return [
        {
            "name": "set_expression",
            "description": ("Change the expression of your face screen and hold it until changed. "
                            "Use sparingly, to match the mood of what you are saying. 'auto' hands "
                            "the face back to its own behaviour. Affects only the screen."),
            "input_schema": {"type": "object", "additionalProperties": False,
                             "properties": {"expression": {"type": "string", "enum": EMOTE_NAMES}},
                             "required": ["expression"]},
            "strict": True,
        },
        {
            "name": "face_gesture",
            "description": ("Play one short gesture with your eyes on the face screen (for example a "
                            "wink on a joke). Affects only the screen; your head does not move."),
            "input_schema": {"type": "object", "additionalProperties": False,
                             "properties": {"gesture": {"type": "string", "enum": MOVE_NAMES}},
                             "required": ["gesture"]},
            "strict": True,
        },
        {
            "name": "robot_status",
            "description": ("Read whether you are e-stopped, which mode your operator's transmitter "
                            "is in, and any faults. Read-only. Use it to answer questions about "
                            "yourself truthfully."),
            "input_schema": {"type": "object", "additionalProperties": False, "properties": {}},
            "strict": True,
        },
        {
            "name": "end_conversation",
            "description": "Stop listening and go back to idle, for example after a goodbye.",
            "input_schema": {"type": "object", "additionalProperties": False, "properties": {}},
            "strict": True,
        },
    ]


def openai_tools() -> list[dict]:
    """The same tools for an OpenAI-compatible local server (vLLM, llama.cpp)."""
    return [{"type": "function",
             "function": {"name": t["name"], "description": t["description"],
                          "parameters": t["input_schema"]}} for t in tool_definitions()]


def plan(name, args, limiter: Limiter, now: float | None = None) -> Action:
    """A model's tool call -> the one effect it may have, or a refusal."""
    now = time.monotonic() if now is None else now
    if not isinstance(args, dict):
        return Action(refused="arguments must be an object")

    if name == "set_expression":
        v = args.get("expression")
        if not isinstance(v, str) or v.lower() not in EMOTE_NAMES or set(args) != {"expression"}:
            return Action(refused=f"expression must be one of {', '.join(EMOTE_NAMES)}")
        if not limiter.ok(now):
            return Action(refused="too many face changes; carry on talking")
        return Action(head_line=f"EMOTE {v.upper()}", result_hint=f"face now {v.lower()}")

    if name == "face_gesture":
        v = args.get("gesture")
        if not isinstance(v, str) or v.lower() not in MOVE_NAMES or set(args) != {"gesture"}:
            return Action(refused=f"gesture must be one of {', '.join(MOVE_NAMES)}")
        if not limiter.ok(now):
            return Action(refused="too many face changes; carry on talking")
        return Action(head_line=f"MOVE {v.lower()}", result_hint="done")

    if name == "robot_status":
        if args:
            return Action(refused="robot_status takes no arguments")
        return Action(http=("GET", "/api/choreo/robot"))

    if name == "end_conversation":
        if args:
            return Action(refused="end_conversation takes no arguments")
        return Action(end_conversation=True, result_hint="the conversation will end after this reply")

    return Action(refused=f"unknown tool {name!r}; you can only use your face tools")


def every_possible_effect() -> list[Action]:
    """Every effect reachable through this module: each enum value of each tool, called once."""
    out = []
    for e in EMOTE_NAMES:
        out.append(plan("set_expression", {"expression": e}, Limiter(), now=0))
    for m in MOVE_NAMES:
        out.append(plan("face_gesture", {"gesture": m}, Limiter(), now=0))
    out.append(plan("robot_status", {}, Limiter()))
    out.append(plan("end_conversation", {}, Limiter()))
    return out


def status_summary(robot: dict | None) -> str:
    """The dashboard's /api/choreo/robot view, as a sentence a model can repeat. Unknown is said
    as unknown, never as fine: the view uses None for anything it cannot vouch for."""
    if not isinstance(robot, dict):
        return "Your status cannot be read right now."

    def say(v, yes, no):
        return "unknown" if v is None else (yes if v else no)
    parts = [
        f"e-stop: {say(robot.get('estop'), 'pressed, so you are stopped', 'not pressed')}",
        f"transmitter mode: {robot.get('ch5') or 'unknown'}",
        f"faults: {robot.get('fault') or 'none reported'}",
        f"arms: {robot.get('arms') or 'unknown'}",
    ]
    return "; ".join(parts) + ". Your operator controls all movement."
