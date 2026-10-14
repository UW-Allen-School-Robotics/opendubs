#!/usr/bin/env python3
"""The only things a language model may ask the robot to do — a prototype of the allowlist.

PROTOTYPE, NOT WIRED TO ANYTHING. Written 2026-10-12 for the voice study
(notes/design/voice-llm-research.md, Q5 and "The safety boundary"). It runs no robot and sends
nothing: `plan()` turns a model's tool call into the exact head-protocol line or dashboard request
it would cause, or a refusal, and the tests in test_voice_bench.py hold it to the rules below.

The rules, from notes/design/face-design.md ("No LLM authority over motion") and the robot's
existing gates:

1. **A model gets the face, not the body.** By default the only effects are `EMOTE` and `MOVE` on
   the face (head protocol, notes/design/head-protocol.md §3) — eyes on a screen — plus a read-only
   status query and "end the conversation". Never `GAZE`, `TRACK`, `TOUCH`, `PARK` (the neck),
   never `MAST`, `DRIVE` or anything for the StamPLC, never an arm. The test enumerates every line
   this module can produce and fails if any verb outside {EMOTE, MOVE} appears.
2. **Closed vocabularies, taken from the protocol's own copy.** Names come from
   `software/head-protocol/head_protocol.py`, so the allowlist cannot drift from what the face
   accepts. A value outside the enum is refused, never passed through — which also closes the
   injection route of a newline smuggled into a UDP line ("SOFT\\nGAZE 0 0").
3. **Routines are off unless the operator lists them.** `request_routine` exists only when
   OPENDUBS_VOICE_ROUTINES names palette routines, and even then it only POSTs the dashboard's
   existing `/api/choreo/run/<id>`. The runner's own preflight still refuses unless CH5 is in AUTO,
   the deadman is held and the arms are active; an e-stop or a released deadman still ends it. The
   model can ask; it cannot make the robot move without a person holding the deadman. Whether to
   allow even that is a decision for the team (the study's open decision D3).
4. **Rate-limited.** A model that calls face tools in a loop cannot strobe the face: at most
   FACE_BURST lines per FACE_WINDOW_S.

Standard library only.
"""
from __future__ import annotations

import os
import re
import sys
import time
from dataclasses import dataclass, field

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(REPO, "software", "head-protocol"))
import head_protocol as hp  # noqa: E402  (the protocol as data: EMOTES, MOVES)

ALLOWED_HEAD_VERBS = frozenset({"EMOTE", "MOVE"})
FACE_BURST, FACE_WINDOW_S = 4, 2.0
ROUTINE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")

EMOTE_NAMES = [e.lower() for e in hp.EMOTES] + ["auto"]
MOVE_NAMES = list(hp.MOVES)


@dataclass
class Action:
    """What a tool call would cause. Exactly one of the effect fields is set, or `refused`."""
    head_line: str | None = None        # one head-protocol line, to UDP 127.0.0.1:4242 (the face door)
    http: tuple | None = None           # (method, path) on the dashboard, 127.0.0.1:8080
    end_conversation: bool = False
    refused: str | None = None          # why not; returned to the model as an is_error tool_result
    result_hint: str = ""               # what to tell the model on success


@dataclass
class Policy:
    routines: list = field(default_factory=list)      # palette ids the operator allows by voice
    _face_times: list = field(default_factory=list)

    @classmethod
    def from_env(cls, env=os.environ) -> "Policy":
        ids = [r.strip() for r in env.get("OPENDUBS_VOICE_ROUTINES", "").split(",") if r.strip()]
        return cls(routines=[r for r in ids if ROUTINE_ID.match(r)])


def tool_definitions(policy: Policy) -> list[dict]:
    """The tools, in the Claude Messages API shape. `strict` keeps arguments schema-valid; the
    enums are the protocol's own vocabularies. `openai_tools()` converts for a local server."""
    tools = [
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
    if policy.routines:
        tools.append({
            "name": "request_routine",
            "description": ("Ask to perform one of your pre-approved routines. It only runs if your "
                            "operator has the transmitter in AUTO with the deadman held; otherwise it "
                            "is refused and you should say that your operator controls movement."),
            "input_schema": {"type": "object", "additionalProperties": False,
                             "properties": {"routine": {"type": "string", "enum": list(policy.routines)}},
                             "required": ["routine"]},
            "strict": True,
        })
    return tools


def openai_tools(policy: Policy) -> list[dict]:
    """The same tools for an OpenAI-compatible local server (vLLM, llama.cpp, Ollama)."""
    return [{"type": "function",
             "function": {"name": t["name"], "description": t["description"],
                          "parameters": t["input_schema"]}} for t in tool_definitions(policy)]


def _face_ok(policy: Policy, now: float) -> bool:
    policy._face_times[:] = [t for t in policy._face_times if now - t < FACE_WINDOW_S]
    if len(policy._face_times) >= FACE_BURST:
        return False
    policy._face_times.append(now)
    return True


def plan(name: str, args, policy: Policy, now: float | None = None) -> Action:
    """A model's tool call -> the one effect it may have, or a refusal. Never raises on bad input:
    whatever a model emits is untrusted output."""
    now = time.monotonic() if now is None else now
    if not isinstance(args, dict):
        return Action(refused="arguments must be an object")

    if name == "set_expression":
        v = args.get("expression")
        if not isinstance(v, str) or v.lower() not in EMOTE_NAMES or set(args) != {"expression"}:
            return Action(refused=f"expression must be one of {', '.join(EMOTE_NAMES)}")
        if not _face_ok(policy, now):
            return Action(refused="too many face changes; carry on talking")
        return Action(head_line=f"EMOTE {v.upper()}", result_hint=f"face now {v.lower()}")

    if name == "face_gesture":
        v = args.get("gesture")
        if not isinstance(v, str) or v.lower() not in MOVE_NAMES or set(args) != {"gesture"}:
            return Action(refused=f"gesture must be one of {', '.join(MOVE_NAMES)}")
        if not _face_ok(policy, now):
            return Action(refused="too many face changes; carry on talking")
        return Action(head_line=f"MOVE {v.lower()}", result_hint="done")

    if name == "robot_status":
        if args:
            return Action(refused="robot_status takes no arguments")
        return Action(http=("GET", "/api/choreo/robot"))

    if name == "end_conversation":
        if args:
            return Action(refused="end_conversation takes no arguments")
        return Action(end_conversation=True)

    if name == "request_routine":
        v = args.get("routine")
        if not policy.routines:
            return Action(refused="routines are not enabled for voice on this robot")
        if not isinstance(v, str) or v not in policy.routines or not ROUTINE_ID.match(v) \
                or set(args) != {"routine"}:
            return Action(refused=f"routine must be one of {', '.join(policy.routines)}")
        return Action(http=("POST", f"/api/choreo/run/{v}"),
                      result_hint="requested; the robot's own checks decide whether it runs")

    return Action(refused=f"unknown tool {name!r}")


def every_possible_effect(policy: Policy) -> list[Action]:
    """Every effect reachable through this module, for the safety test: each enum value of each
    tool, called once."""
    out = []
    for e in EMOTE_NAMES:
        out.append(plan("set_expression", {"expression": e}, Policy(policy.routines), now=0))
    for m in MOVE_NAMES:
        out.append(plan("face_gesture", {"gesture": m}, Policy(policy.routines), now=0))
    out.append(plan("robot_status", {}, policy))
    out.append(plan("end_conversation", {}, policy))
    for r in policy.routines:
        out.append(plan("request_routine", {"routine": r}, policy))
    return out


if __name__ == "__main__":
    import json
    pol = Policy.from_env()
    print(json.dumps(tool_definitions(pol), indent=2))
