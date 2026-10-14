#!/usr/bin/env python3
"""Measure what a conversational robot feels: time to first token, time to first sentence, decode rate.

    python3 llm_bench.py --url http://127.0.0.1:8000/v1 --model <served name> --runs 20

NOT YET RUN ON A THOR. Written 2026-10-12 for the voice study (notes/design/voice-llm-research.md)
and checked only against the fake server in test_voice_bench.py. Its first real use is the
non-robot Thor, through thor_llm_bench.sh. Never point it at opendubs2's GPU: that Thor is never
the test bed (notes/workplan-thor-stack.md).

Talks to any OpenAI-compatible streaming endpoint (`POST <url>/chat/completions`, `stream: true`),
which vLLM, the llama.cpp server, SGLang, TensorRT-LLM's `trtllm-serve` and Ollama all expose, so
one script compares runtimes. Standard library only, so it runs inside any container.

What it reports, per run and as p50/p90 over the runs:

  ttft_ms    request sent -> first visible text. What the face's "thinking" state has to cover.
  ttfs_ms    request sent -> end of the first sentence. What TTS needs before it can start
             speaking, so this, not TTFT, is the number the latency budget uses.
  decode_tps (completion tokens - 1) / (last token time - first token time). Speech consumes
             roughly 3 to 4 tokens a second, so anything above ~20 is headroom, not latency.
  total_ms   request sent -> stream finished.

Token counts come from the server's `usage` (asked for with `stream_options.include_usage`). A
server that sends none is counted by stream chunks instead, and the run is marked `tokens=chunks`:
close for llama.cpp and vLLM, which send about one token per chunk, but an estimate.
"""
from __future__ import annotations

import argparse
import http.client
import json
import re
import statistics
import sys
import time
import urllib.parse

# A robot persona of realistic size: the system prompt a voice turn would carry, with its rules and
# the face tools described in prose. About 450 tokens, so prefill is exercised, not skipped.
PERSONA = """You are OpenDubs, a friendly research robot in a university robotics lab. You speak out
loud through a small speaker, so every reply is spoken, not read.

How you talk:
- Answer in one to three short sentences. Never use lists, markdown, emoji or code.
- Put the most useful words in the first sentence; people may interrupt you.
- If you did not catch what was said, say so plainly and ask them to repeat it.
- If you do not know, say you do not know. Never invent facts about the lab or about people.
- You cannot recognise anyone and must never pretend to.

What you are: a mobile base with a lift, two arms, and a tablet face with animated eyes. A person
with a radio transmitter is responsible for all of your movement. You cannot move yourself, and you
must never say you will move, drive, reach, grab or follow. If asked to move, explain that your
operator controls your movement.

What you can do while talking: change your expression (neutral, alert, squint, soft, sleepy) and
make a small face gesture (wink, blink, look left or right, eye roll, bob, sparkle, side eye,
double take, startle). These are only on your face screen.

Your lab builds low-cost robots so that more people can do robotics research. You run on an NVIDIA
Jetson computer. You like dancing shows, tidy cables and people who say thank you.

Latency-sensitive: begin your spoken answer immediately."""

# Short, conversational turns of the kind a visitor asks. Mixed so that some answers are one
# sentence and some are three.
PROMPTS = [
    "Hi! What's your name?",
    "What can you do?",
    "Can you come over here?",
    "What are your arms for?",
    "Tell me a fun fact about robots.",
    "Why do you have a tablet for a face?",
    "What's the weather like today?",
    "Can you dance?",
    "Who built you?",
    "Say something nice to my friend.",
]

SENTENCE_END = re.compile(r"[.!?](\s|$)")


def stream_once(url: str, model: str, system: str, user: str, max_tokens: int, timeout: float,
                extra: dict | None = None) -> dict:
    """One streamed chat completion, timed. Returns the measurements and the text."""
    u = urllib.parse.urlsplit(url.rstrip("/") + "/chat/completions")
    conn_cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
    conn = conn_cls(u.hostname, u.port or (443 if u.scheme == "https" else 80), timeout=timeout)
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "max_tokens": max_tokens,
        "stream": True,
        "stream_options": {"include_usage": True},
        "temperature": 0.7,
    }
    body.update(extra or {})
    t0 = time.perf_counter()
    conn.request("POST", u.path, body=json.dumps(body),
                 headers={"Content-Type": "application/json", "Accept": "text/event-stream"})
    resp = conn.getresponse()
    if resp.status != 200:
        raise RuntimeError(f"HTTP {resp.status}: {resp.read()[:300]!r}")

    text, chunks, usage = [], 0, None
    t_first = t_sentence = t_last = None
    while True:
        line = resp.readline()
        if not line:
            break
        line = line.strip()
        if not line.startswith(b"data:"):
            continue
        data = line[5:].strip()
        if data == b"[DONE]":
            break
        try:
            ev = json.loads(data)
        except ValueError:          # a keep-alive or a partial line: not a token
            continue
        if ev.get("usage"):
            usage = ev["usage"]
        for ch in ev.get("choices") or []:
            piece = (ch.get("delta") or {}).get("content") or ""
            if not piece:
                continue
            now = time.perf_counter()
            chunks += 1
            t_last = now
            if t_first is None:
                t_first = now
            text.append(piece)
            if t_sentence is None and SENTENCE_END.search("".join(text)):
                t_sentence = now
    t_end = time.perf_counter()
    conn.close()

    full = "".join(text)
    if t_first is None:
        raise RuntimeError("the stream carried no text (a reasoning model thinking silently? "
                           "turn its reasoning off for voice; see the study, Q1)")
    if t_sentence is None:          # one sentence with no closing punctuation: the whole reply
        t_sentence = t_last
    n_out = (usage or {}).get("completion_tokens") or chunks
    n_in = (usage or {}).get("prompt_tokens")
    span = (t_last - t_first) if t_last and t_last > t_first else None
    return {
        "ttft_ms": round((t_first - t0) * 1000, 1),
        "ttfs_ms": round((t_sentence - t0) * 1000, 1),
        "total_ms": round((t_end - t0) * 1000, 1),
        "decode_tps": round((n_out - 1) / span, 1) if span and n_out > 1 else None,
        "completion_tokens": n_out,
        "prompt_tokens": n_in,
        "tokens": "usage" if usage else "chunks",
        "text": full,
    }


def pct(xs, p):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    k = (len(xs) - 1) * p / 100
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return round(xs[lo] + (xs[hi] - xs[lo]) * (k - lo), 1)


def summarise(rows: list[dict]) -> dict:
    out = {"runs": len(rows)}
    for key in ("ttft_ms", "ttfs_ms", "total_ms", "decode_tps", "completion_tokens"):
        vals = [r[key] for r in rows]
        out[key] = {"p50": pct(vals, 50), "p90": pct(vals, 90),
                    "mean": round(statistics.fmean([v for v in vals if v is not None]), 1)
                    if any(v is not None for v in vals) else None}
    out["prompt_tokens"] = rows[0].get("prompt_tokens") if rows else None
    out["tokens"] = "usage" if all(r["tokens"] == "usage" for r in rows) else "chunks"
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--url", required=True, help="OpenAI-compatible base URL, e.g. http://127.0.0.1:8000/v1")
    ap.add_argument("--model", required=True, help="the model name the server answers to")
    ap.add_argument("--label", default="", help="free text recorded with every row (runtime, quant, power mode)")
    ap.add_argument("--runs", type=int, default=20)
    ap.add_argument("--warmup", type=int, default=2, help="untimed runs first (CUDA graphs, caches)")
    ap.add_argument("--max-tokens", type=int, default=120)
    ap.add_argument("--timeout", type=float, default=120.0)
    ap.add_argument("--history-tokens", type=int, default=0,
                    help="pad the system prompt with ~N tokens of earlier conversation, to see prefill cost")
    ap.add_argument("--extra", default="{}",
                    help='JSON merged into the request, e.g. \'{"chat_template_kwargs": {"enable_thinking": false}}\'')
    ap.add_argument("--out", help="append one JSON line per run here (keep it OUTSIDE the repo)")
    a = ap.parse_args(argv)

    system = PERSONA
    if a.history_tokens > 0:
        filler = ("Visitor: what is that cable for? Robot: it carries power to my arms. ") * (a.history_tokens // 18 + 1)
        system += "\n\nEarlier in this conversation:\n" + filler
    extra = json.loads(a.extra)

    for i in range(a.warmup):
        stream_once(a.url, a.model, system, PROMPTS[i % len(PROMPTS)], a.max_tokens, a.timeout, extra)

    rows = []
    out = open(a.out, "a") if a.out else None
    try:
        for i in range(a.runs):
            prompt = PROMPTS[i % len(PROMPTS)]
            r = stream_once(a.url, a.model, system, prompt, a.max_tokens, a.timeout, extra)
            r.update({"label": a.label, "model": a.model, "prompt": prompt, "run": i,
                      "history_tokens": a.history_tokens})
            rows.append(r)
            print(f"[{i + 1}/{a.runs}] ttft {r['ttft_ms']:7.1f} ms  ttfs {r['ttfs_ms']:7.1f} ms  "
                  f"{r['decode_tps'] or 0:6.1f} tok/s  {r['completion_tokens']:4d} tok  | {r['text'][:60]!r}",
                  file=sys.stderr)
            if out:
                out.write(json.dumps(r) + "\n")
                out.flush()
    finally:
        if out:
            out.close()

    s = summarise(rows)
    s.update({"label": a.label, "model": a.model, "history_tokens": a.history_tokens})
    print(json.dumps(s))
    print(f"\n| model | label | TTFT p50/p90 ms | first sentence p50/p90 ms | decode tok/s p50 | tokens |\n"
          f"|---|---|---|---|---|---|\n"
          f"| {a.model} | {a.label} | {s['ttft_ms']['p50']} / {s['ttft_ms']['p90']} | "
          f"{s['ttfs_ms']['p50']} / {s['ttfs_ms']['p90']} | {s['decode_tps']['p50']} | {s['tokens']} |",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
