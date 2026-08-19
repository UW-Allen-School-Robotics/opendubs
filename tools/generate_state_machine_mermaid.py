#!/usr/bin/env python3
"""Generate Mermaid state-machine diagrams from docs/state-machines.md.

This script creates:
- docs/diagrams/state_machine_robot.mmd
- docs/diagrams/state_machine_subsystem_pattern.mmd
- docs/figma/board_state_machine_payload.md

The payload file is intended for copy/paste into a Mermaid-capable Figma Board widget.
"""

from __future__ import annotations

import argparse
import pathlib
import re
from dataclasses import dataclass
from typing import List


@dataclass
class Transition:
    source: str
    target: str
    trigger: str
    guard: str


def repo_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parent.parent


def extract_robot_states(text: str) -> List[str]:
    marker = "## Robot-Level FSM (Draft)"
    start = text.find(marker)
    if start < 0:
        raise ValueError("Could not find 'Robot-Level FSM (Draft)' section")

    sub = text[start:]
    states_header = "States:"
    sidx = sub.find(states_header)
    if sidx < 0:
        raise ValueError("Could not find 'States:' list")

    after = sub[sidx + len(states_header) :]
    lines = after.splitlines()

    states: List[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("### "):
            break
        m = re.match(r"^-\s+`([^`]+)`\s*$", stripped)
        if m:
            states.append(m.group(1))

    if not states:
        raise ValueError("No robot states parsed from states list")
    return states


def extract_robot_transitions(text: str) -> List[Transition]:
    marker = "### Robot-Level Transition Intent"
    start = text.find(marker)
    if start < 0:
        raise ValueError("Could not find transition intent section")

    sub = text[start:]
    lines = sub.splitlines()

    # Find table rows containing at least 4 columns.
    transitions: List[Transition] = []
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("|"):
            # Stop once table likely ends.
            if transitions:
                break
            continue
        # Skip header + divider
        if stripped.startswith("| From | To | Trigger | Guard |"):
            continue
        if re.match(r"^\|[-|\s]+\|$", stripped):
            continue

        parts = [p.strip() for p in stripped.strip("|").split("|")]
        if len(parts) < 4:
            continue
        source, target, trigger, guard = parts[:4]
        if not source or not target:
            continue
        transitions.append(
            Transition(source=source, target=target, trigger=trigger, guard=guard)
        )

    if not transitions:
        raise ValueError("No transitions parsed from transition table")
    return transitions


def sanitize_state_name(name: str) -> str:
    # Mermaid identifier-friendly label; preserve readable display name separately.
    return re.sub(r"[^A-Za-z0-9_]", "_", name)


def build_robot_mermaid(states: List[str], transitions: List[Transition]) -> str:
    ids = {s: sanitize_state_name(s) for s in states}

    out: List[str] = ["stateDiagram-v2"]
    out.append("    [*] --> INIT")

    for state in states:
        sid = ids[state]
        # Create explicit mapping so labels remain stable even if identifiers are normalized.
        if sid != state:
            out.append(f"    state \"{state}\" as {sid}")

    for tr in transitions:
        label = tr.trigger
        if tr.guard and tr.guard.lower() != "none":
            label = f"{tr.trigger} / {tr.guard}"

        if tr.source == "ANY":
            # Expand ANY to each known state except target; keeps intent explicit in mermaid.
            for s in states:
                if s == tr.target:
                    continue
                out.append(f"    {ids[s]} --> {ids[tr.target]} : {label}")
        else:
            if tr.source not in ids or tr.target not in ids:
                # Ignore unknown states rather than crash generation.
                continue
            out.append(f"    {ids[tr.source]} --> {ids[tr.target]} : {label}")

    return "\n".join(out) + "\n"


def build_subsystem_pattern_mermaid() -> str:
    return "\n".join(
        [
            "stateDiagram-v2",
            "    [*] --> DISABLED",
            "    DISABLED --> READY : enable / parent allows",
            "    READY --> ACTIVE : activate",
            "    ACTIVE --> READY : deactivate",
            "    READY --> DISABLED : disable",
            "    ACTIVE --> FAULT : local fault",
            "    READY --> FAULT : precheck fail",
            "    FAULT --> DISABLED : reset + safe",
            "",
            "    note right of ACTIVE",
            "      Allowed only when robot-level",
            "      mode permits this subsystem",
            "    end note",
        ]
    ) + "\n"


def build_board_payload(robot_mermaid: str, subsystem_mermaid: str) -> str:
    return "\n".join(
        [
            "# Figma Board Mermaid Payload",
            "",
            "Use these in a Mermaid-capable Figma Board widget/plugin.",
            "",
            "## Robot-Level FSM",
            "",
            "```mermaid",
            robot_mermaid.rstrip(),
            "```",
            "",
            "## Subsystem FSM Pattern",
            "",
            "```mermaid",
            subsystem_mermaid.rstrip(),
            "```",
            "",
            "## Regeneration",
            "",
            "Run:",
            "",
            "```bash",
            "python tools/generate_state_machine_mermaid.py",
            "```",
        ]
    ) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Mermaid from state machine docs")
    parser.add_argument(
        "--source",
        default="docs/state-machines.md",
        help="Path to state machine markdown source",
    )
    args = parser.parse_args()

    root = repo_root()
    source = (root / args.source).resolve()
    text = source.read_text(encoding="utf-8")

    states = extract_robot_states(text)
    transitions = extract_robot_transitions(text)

    robot_mermaid = build_robot_mermaid(states, transitions)
    subsystem_mermaid = build_subsystem_pattern_mermaid()

    out_robot = root / "docs/diagrams/state_machine_robot.mmd"
    out_subsystem = root / "docs/diagrams/state_machine_subsystem_pattern.mmd"

    out_robot.parent.mkdir(parents=True, exist_ok=True)

    out_robot.write_text(robot_mermaid, encoding="utf-8")
    out_subsystem.write_text(subsystem_mermaid, encoding="utf-8")

    print(f"Wrote {out_robot}")
    print(f"Wrote {out_subsystem}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
