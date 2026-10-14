#!/usr/bin/env python3
"""The local brain's serving profile: one place for how a local model is served and called.

    llm_profile.py --shell qwen3-30b-a3b-w4a16     KEY=value lines for image/run-llm.sh
    llm_profile.py --list

Each local model in models.lock.json carries a `serve` block: the name vLLM serves it under, its
tool-call parser, its context length, how many sequences at once, and the sampling settings its
model card recommends. run-llm.sh starts vLLM from it, and the voice service (brains.LocalLLM)
calls the model with it, so the two cannot disagree. Switching to the L-3 fallback is one setting
(VOICE_LLM_PROFILE) on each side, not a code change.

The project team chose Qwen3-30B-A3B-Instruct-2507 as the local brain, with Qwen3-4B-Instruct-2507
as the fallback if gate L-3 fails (workplan V-O3, 2026-10-14). The 30B runs as Red Hat AI's 4-bit
W4A16 build: the BF16 weights are about 61 GB and the FP8 build 31 GB, neither of which fits the
<= 24 GiB share; W4A16 is 16.7 GB. Whether its quality and speed hold is gate L-1.

Standard library only.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LOCK = os.path.join(HERE, "models.lock.json")
DEFAULT = "qwen3-30b-a3b-w4a16"
SAFE = re.compile(r"^[A-Za-z0-9_./-]+$")
SAMPLING_KEYS = {"temperature", "top_p", "top_k", "min_p", "presence_penalty"}


class ProfileError(ValueError):
    pass


def profiles(path: str = LOCK) -> dict:
    with open(path, encoding="utf-8") as f:
        return {m["name"]: m for m in json.load(f)["models"] if "serve" in m}


def load(name: str | None = None, path: str = LOCK) -> dict:
    """A profile by name (default: VOICE_LLM_PROFILE, else the decided model). Validated: every
    value that reaches a command line is a plain token, and sampling keys are known ones."""
    name = name or os.environ.get("VOICE_LLM_PROFILE") or DEFAULT
    ps = profiles(path)
    if name not in ps:
        raise ProfileError(f"no local model profile {name!r}; known: {', '.join(sorted(ps))}")
    m = ps[name]
    sv = m["serve"]
    for k in ("served_name", "tool_parser"):
        if not isinstance(sv.get(k), str) or not SAFE.match(sv[k]):
            raise ProfileError(f"{name}: serve.{k} is not a plain token")
    for k in ("max_model_len", "max_num_seqs"):
        if not isinstance(sv.get(k), int) or sv[k] <= 0:
            raise ProfileError(f"{name}: serve.{k} must be a positive integer")
    if not SAFE.match(m["dest"]) or ".." in m["dest"]:
        raise ProfileError(f"{name}: dest is not a plain relative path")
    bad = set(sv.get("sampling", {})) - SAMPLING_KEYS
    if bad:
        raise ProfileError(f"{name}: unknown sampling keys {sorted(bad)}")
    return {"name": name, "repo": m["repo"], "revision": m["revision"], "dest": m["dest"], **sv}


def shell(p: dict) -> str:
    return "\n".join([
        f"LLM_PROFILE={p['name']}", f"LLM_DEST={p['dest']}", f"LLM_SERVED_NAME={p['served_name']}",
        f"LLM_TOOL_PARSER={p['tool_parser']}", f"LLM_MAX_MODEL_LEN={p['max_model_len']}",
        f"LLM_MAX_NUM_SEQS={p['max_num_seqs']}",
    ])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--shell", metavar="PROFILE")
    g.add_argument("--list", action="store_true")
    a = ap.parse_args(argv)
    try:
        if a.list:
            for n, m in sorted(profiles().items()):
                print(f"{n}\t{m['repo']}@{m['revision'][:12]}\t{m['what']}")
            return 0
        print(shell(load(a.shell)))
        return 0
    except ProfileError as e:
        print(f"llm_profile: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
