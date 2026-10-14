#!/usr/bin/env python3
"""The voice-bench tools, checked without a Thor, a model or a network.

    python3 tools/voice-bench/test_voice_bench.py        (or pytest)

What this proves: the benchmark parses a streamed OpenAI-compatible reply and times it correctly
(against a fake server whose timing is known), and the tool allowlist can only ever produce face
lines and the two dashboard requests it names. What it does NOT prove: anything about a real model
or a real Thor.
"""
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import llm_bench  # noqa: E402
import safe_tools as st  # noqa: E402

# ---- the benchmark, against a fake streaming server -------------------------------------------

FIRST_DELAY_S, TOKEN_GAP_S = 0.15, 0.02
REPLY = ["Hello", " there", "!", " I", " am", " a", " robot", "."]


class FakeOpenAI(BaseHTTPRequestHandler):
    with_usage = True

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        assert body["stream"] is True and body["messages"][0]["role"] == "system"
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        time.sleep(FIRST_DELAY_S)
        for i, piece in enumerate(REPLY):
            if i:
                time.sleep(TOKEN_GAP_S)
            ev = {"choices": [{"index": 0, "delta": {"content": piece}}]}
            self.wfile.write(f"data: {json.dumps(ev)}\n\n".encode())
            self.wfile.flush()
        if self.with_usage:
            ev = {"choices": [], "usage": {"prompt_tokens": 450, "completion_tokens": len(REPLY)}}
            self.wfile.write(f"data: {json.dumps(ev)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")


def _serve(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_bench_times_a_stream():
    srv = _serve(FakeOpenAI)
    try:
        r = llm_bench.stream_once(f"http://127.0.0.1:{srv.server_port}/v1", "fake", "sys", "hi", 50, 5)
    finally:
        srv.shutdown()
    assert r["text"] == "Hello there! I am a robot."
    assert r["tokens"] == "usage" and r["completion_tokens"] == len(REPLY) and r["prompt_tokens"] == 450
    assert FIRST_DELAY_S * 1000 <= r["ttft_ms"] < FIRST_DELAY_S * 1000 + 150, r
    # the first sentence ends at "!", the third piece: two gaps after the first token
    assert r["ttfs_ms"] >= r["ttft_ms"] + 2 * TOKEN_GAP_S * 1000 - 5, r
    assert r["ttfs_ms"] < r["total_ms"]
    # 7 gaps of 20 ms between 8 tokens: about 50 tok/s, allowing for scheduling jitter
    assert 30 < r["decode_tps"] <= 55, r


def test_bench_counts_chunks_when_the_server_sends_no_usage():
    class NoUsage(FakeOpenAI):
        with_usage = False
    srv = _serve(NoUsage)
    try:
        r = llm_bench.stream_once(f"http://127.0.0.1:{srv.server_port}/v1", "fake", "sys", "hi", 50, 5)
    finally:
        srv.shutdown()
    assert r["tokens"] == "chunks" and r["completion_tokens"] == len(REPLY)


def test_summary_percentiles():
    rows = [{"ttft_ms": v, "ttfs_ms": v, "total_ms": v, "decode_tps": v, "completion_tokens": 1,
             "tokens": "usage"} for v in (10, 20, 30, 40, 50)]
    s = llm_bench.summarise(rows)
    assert s["ttft_ms"]["p50"] == 30 and s["ttft_ms"]["p90"] == 46


# ---- the allowlist ------------------------------------------------------------------------------

MOTION_VERBS = ("GAZE", "TRACK", "TOUCH", "PARK", "MAST", "DRIVE", "ESTOP", "RELAY", "OTA", "REBOOT",
                "CFG", "STATE", "THEME", "SKIN", "SPIN", "CUE", "VOICE")


def test_every_reachable_effect_is_a_face_line_or_a_named_request():
    pol = st.Policy(routines=["hello", "wave-and-nod"])
    for a in st.every_possible_effect(pol):
        assert a.refused is None, a
        if a.head_line is not None:
            verb = a.head_line.split()[0]
            assert verb in st.ALLOWED_HEAD_VERBS, a
            assert "\n" not in a.head_line and "\r" not in a.head_line and len(a.head_line) <= 255
        elif a.http is not None:
            assert a.http in {("GET", "/api/choreo/robot"), ("POST", "/api/choreo/run/hello"),
                              ("POST", "/api/choreo/run/wave-and-nod")}, a
        else:
            assert a.end_conversation


def test_no_motion_verb_can_be_produced_from_any_tool_and_any_string():
    pol = st.Policy(routines=["hello"])
    hostile = ["SOFT\nGAZE 0 0", "soft gaze", "GAZE 90 90", "MAST CMD 1", "../../api/estop",
               "hello; DRIVE 1 0 0", "", " ", "x" * 400, "NEUTRAL\r\nPARK"]
    for tool, key in (("set_expression", "expression"), ("face_gesture", "gesture"),
                      ("request_routine", "routine")):
        for h in hostile:
            a = st.plan(tool, {key: h}, pol, now=0)
            assert a.refused, (tool, h, a)
    for args in (None, [], "SOFT", {"expression": "soft", "extra": 1}):
        assert st.plan("set_expression", args, pol, now=0).refused
    for name in ("move_base", "set_gaze", "GAZE", "estop", "run_shell"):
        assert st.plan(name, {}, pol, now=0).refused


def test_routines_are_absent_unless_the_operator_lists_them():
    off = st.Policy()
    assert "request_routine" not in {t["name"] for t in st.tool_definitions(off)}
    assert st.plan("request_routine", {"routine": "hello"}, off).refused
    on = st.Policy.from_env({"OPENDUBS_VOICE_ROUTINES": "hello, Bad Id!, wave-and-nod"})
    assert on.routines == ["hello", "wave-and-nod"]          # an id that is not an id is dropped
    assert st.plan("request_routine", {"routine": "christmas"}, on).refused


def test_face_changes_are_rate_limited():
    pol = st.Policy()
    got = [st.plan("face_gesture", {"gesture": "wink_l"}, pol, now=0.1 * i) for i in range(6)]
    assert [a.refused is None for a in got] == [True] * st.FACE_BURST + [False] * (6 - st.FACE_BURST)
    later = st.plan("face_gesture", {"gesture": "wink_l"}, pol, now=st.FACE_WINDOW_S + 1)
    assert later.refused is None


def test_tool_schemas_are_strict_and_use_the_protocol_vocabulary():
    tools = st.tool_definitions(st.Policy(routines=["hello"]))
    for t in tools:
        assert t["strict"] is True and t["input_schema"]["additionalProperties"] is False
    by = {t["name"]: t for t in tools}
    assert set(by["face_gesture"]["input_schema"]["properties"]["gesture"]["enum"]) == set(st.hp.MOVES)
    assert {e.upper() for e in by["set_expression"]["input_schema"]["properties"]["expression"]["enum"]} \
        == set(st.hp.EMOTES) | {"AUTO"}
    assert len(st.openai_tools(st.Policy())) == 4


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for n, f in tests:
        try:
            f()
            print(f"PASS {n}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"FAIL {n}: {e!r}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
