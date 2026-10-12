#!/usr/bin/env python3
"""Time a voice turn against the Claude API: time to first text, to first sentence, and cost.

    ANTHROPIC_API_KEY=... uv run --with anthropic python3 tools/voice-bench/claude_bench.py --runs 10

NOT YET RUN. Written 2026-10-12 for the voice study (notes/design/voice-llm-research.md, Q5); the
session that wrote it had no API key. Run it from the robot's network, on the robot's uplink, to get
the numbers that matter: the same script from a laptop on a fast campus link measures the model,
not the robot.

THE KEY. Never in the repository. On a laptop, export ANTHROPIC_API_KEY in your shell or use the
git-ignored ops/ directory. On a robot, a root-owned 0600 file such as /etc/opendubs/voice.env
(per-robot values stay on the robot), read by the voice service's unit. This script only reads
the environment and never prints the key.

What it compares: the three current models (claude-haiku-5-5, claude-sonnet-5-5, claude-opus-5-5)
with the same persona, the same face tools (safe_tools.py), and the same visitor questions as
llm_bench.py, so the cloud and local numbers sit in one table. Effort is `low` by default, which is
where a short spoken reply belongs; `--thinking off` turns thinking off where the model allows it
(Haiku 5.5: disabled; Sonnet 5.5: between_tools; Opus 5.5 cannot, and is skipped with a note).

Opus 5.5 and Sonnet 5.5 requests carry the server-side refusal fallback (`fallbacks: "default"`),
so a declined turn is re-run on another model instead of leaving the robot silent; Haiku 5.5 has no
server-side fallback, and a refusal is reported as such.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import llm_bench  # noqa: E402  (PERSONA, PROMPTS, pct)
import safe_tools  # noqa: E402

# USD per million tokens, from the Claude API skill's model table (cached 2026-10-06). Check the
# live pricing page before budgeting on these. cache_read for Haiku is 0.1x input.
PRICES = {
    "claude-haiku-5-5": {"in": 0.10, "out": 0.50, "cache_read": 0.01, "cache_write": 0.125},
    "claude-sonnet-5-5": {"in": 2.00, "out": 10.00, "cache_read": 0.20, "cache_write": 2.50},
    "claude-opus-5-5": {"in": 4.00, "out": 20.00, "cache_read": 0.20, "cache_write": 5.00},
}
FALLBACK_MODELS = {"claude-sonnet-5-5", "claude-opus-5-5"}
THINKING_OFF = {"claude-haiku-5-5": {"type": "disabled"}, "claude-sonnet-5-5": {"type": "between_tools"}}
TOOLS_WHEN_THINKING_OFF = ("When you use a tool, you may say a brief sentence first. If no tool can "
                           "express what the user asked for, say so instead of guessing. Do not "
                           "include internal or system XML tags in your response.")
SENTENCE_END = re.compile(r"[.!?](\s|$)")


def cost(model: str, usage) -> float:
    p = PRICES[model]
    return ((usage.input_tokens or 0) * p["in"] + (usage.output_tokens or 0) * p["out"]
            + (getattr(usage, "cache_read_input_tokens", 0) or 0) * p["cache_read"]
            + (getattr(usage, "cache_creation_input_tokens", 0) or 0) * p["cache_write"]) / 1e6


def one_turn(client, model: str, prompt: str, thinking_off: bool, effort: str, max_tokens: int) -> dict:
    system = llm_bench.PERSONA + ("\n\n" + TOOLS_WHEN_THINKING_OFF if thinking_off else "")
    kw = dict(
        model=model,
        max_tokens=max_tokens,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        tools=safe_tools.tool_definitions(safe_tools.Policy()),
        messages=[{"role": "user", "content": prompt}],
        output_config={"effort": effort},
        thinking=THINKING_OFF[model] if thinking_off else {"type": "adaptive"},
    )
    if model in FALLBACK_MODELS:
        kw["betas"] = ["server-side-fallback-2026-07-01"]
        kw["fallbacks"] = "default"
    t0 = time.perf_counter()
    t_first = t_sentence = None
    text = []
    with client.beta.messages.stream(**kw) as stream:
        for event in stream:
            if event.type == "content_block_delta" and event.delta.type == "text_delta":
                now = time.perf_counter()
                if t_first is None:
                    t_first = now
                text.append(event.delta.text)
                if t_sentence is None and SENTENCE_END.search("".join(text)):
                    t_sentence = now
        msg = stream.get_final_message()
    t_end = time.perf_counter()
    tools_called = [b.name for b in msg.content if b.type == "tool_use"]
    return {
        "model": model, "prompt": prompt, "stop_reason": msg.stop_reason,
        "ttft_ms": round((t_first - t0) * 1000, 1) if t_first else None,
        "ttfs_ms": round(((t_sentence or t_first) - t0) * 1000, 1) if t_first else None,
        "total_ms": round((t_end - t0) * 1000, 1),
        "output_tokens": msg.usage.output_tokens,
        "input_tokens": msg.usage.input_tokens,
        "cache_read": getattr(msg.usage, "cache_read_input_tokens", 0) or 0,
        "usd": round(cost(model, msg.usage), 6),
        "tools": tools_called,
        "served_by": msg.model,
        "text": "".join(text),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--models", default="claude-haiku-5-5,claude-sonnet-5-5,claude-opus-5-5")
    ap.add_argument("--runs", type=int, default=10, help="turns per model (cycles through the prompts)")
    ap.add_argument("--effort", default="low", choices=["low", "medium", "high"])
    ap.add_argument("--thinking", default="adaptive", choices=["adaptive", "off"])
    ap.add_argument("--max-tokens", type=int, default=400)
    ap.add_argument("--out", help="append one JSON line per turn here (keep it OUTSIDE the repo)")
    a = ap.parse_args(argv)

    if not os.environ.get("ANTHROPIC_API_KEY") and not os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        print("no ANTHROPIC_API_KEY in the environment; see the docstring for where the key lives",
              file=sys.stderr)
        return 2
    import anthropic
    client = anthropic.Anthropic()

    out = open(a.out, "a") if a.out else None
    table = []
    for model in [m.strip() for m in a.models.split(",") if m.strip()]:
        if a.thinking == "off" and model not in THINKING_OFF:
            print(f"skip {model}: thinking cannot be turned off on this model", file=sys.stderr)
            continue
        rows = []
        for i in range(a.runs):
            try:
                r = one_turn(client, model, llm_bench.PROMPTS[i % len(llm_bench.PROMPTS)],
                             a.thinking == "off", a.effort, a.max_tokens)
            except anthropic.RateLimitError as e:
                print(f"{model}: rate limited ({e}); stopping this model", file=sys.stderr)
                break
            except anthropic.APIStatusError as e:
                print(f"{model}: API error {e.status_code}: {e}", file=sys.stderr)
                break
            except anthropic.APIConnectionError as e:
                print(f"{model}: no connection ({e}) -- the offline case the robot must handle",
                      file=sys.stderr)
                break
            rows.append(r)
            print(f"{model} [{i + 1}] ttft {r['ttft_ms']} ms  ttfs {r['ttfs_ms']} ms  "
                  f"{r['output_tokens']} tok  ${r['usd']:.5f}  {r['stop_reason']}  {r['tools']}  "
                  f"| {r['text'][:60]!r}", file=sys.stderr)
            if out:
                out.write(json.dumps(r) + "\n")
                out.flush()
        if rows:
            table.append((model, rows))
    if out:
        out.close()

    print("\n| model | effort / thinking | TTFT p50/p90 ms | first sentence p50/p90 ms | "
          "out tokens p50 | $ per turn (mean) |\n|---|---|---|---|---|---|")
    for model, rows in table:
        print(f"| {model} | {a.effort} / {a.thinking} | "
              f"{llm_bench.pct([r['ttft_ms'] for r in rows], 50)} / {llm_bench.pct([r['ttft_ms'] for r in rows], 90)} | "
              f"{llm_bench.pct([r['ttfs_ms'] for r in rows], 50)} / {llm_bench.pct([r['ttfs_ms'] for r in rows], 90)} | "
              f"{llm_bench.pct([r['output_tokens'] for r in rows], 50)} | "
              f"{sum(r['usd'] for r in rows) / len(rows):.5f} |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
