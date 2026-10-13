#!/usr/bin/env python3
"""The brains: the router's fallback rules, the local OpenAI-compatible client against a fake
server, and the Claude backend's request shape against a fake client. No network, no key.

    python3 software/voice/tests/test_brains.py
"""
import asyncio
import json
import os
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace as NS

import fakes  # noqa: F401

import brains
from brains import BrainOffline, Done, FakeLLM, Router, TextDelta, ToolCall


async def collect(agen):
    return [e async for e in agen]


def text_of(events):
    return "".join(e.text for e in events if isinstance(e, TextDelta)).strip()


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


class FailsAfterFirst:
    name = "flaky"

    async def stream(self, history, system):
        yield TextDelta("Hello ")
        raise BrainOffline("dropped mid-answer")


class Offline:
    async def stream(self, history, system):
        raise BrainOffline("no network")
        yield  # noqa


class RouterTests(unittest.IsolatedAsyncioTestCase):
    async def test_auto_falls_back_when_the_cloud_is_slow_and_remembers(self):
        clock = Clock()
        cloud = FakeLLM(["cloud answer"], delay_s=0.3, name="cloud")
        local = FakeLLM(["local answer", "local again"], name="local")
        r = Router("auto", local=local, cloud=cloud, first_text_s=0.05, unhealthy_s=60, clock=clock)
        ev = await collect(r.stream([{"role": "user", "text": "hi"}], "sys"))
        self.assertEqual(text_of(ev), "local answer")
        self.assertEqual(r.last_served_by, "local")
        self.assertFalse(r.cloud_healthy())
        ev = await collect(r.stream([], "sys"))           # straight to local, no wait
        self.assertEqual(len(cloud.calls), 1)
        clock.t += 61
        self.assertTrue(r.cloud_healthy())

    async def test_auto_falls_back_when_the_cloud_is_offline(self):
        r = Router("auto", local=FakeLLM(["local"]), cloud=Offline(), first_text_s=1)
        self.assertEqual(text_of(await collect(r.stream([], "s"))), "local")

    async def test_auto_uses_a_healthy_cloud(self):
        r = Router("auto", local=FakeLLM(["local"]), cloud=FakeLLM(["cloud"], name="c"), first_text_s=1)
        self.assertEqual(text_of(await collect(r.stream([], "s"))), "cloud")
        self.assertEqual(r.last_served_by, "c")

    async def test_never_switches_once_speaking(self):
        r = Router("auto", local=FakeLLM(["local"]), cloud=FailsAfterFirst(), first_text_s=1)
        with self.assertRaises(BrainOffline):
            await collect(r.stream([], "s"))

    async def test_modes(self):
        with self.assertRaises(ValueError):
            Router("hybrid", local=FakeLLM())
        with self.assertRaises(ValueError):
            Router("cloud", local=FakeLLM())
        with self.assertRaises(ValueError):
            Router("auto", cloud=FakeLLM())
        r = Router("cloud", cloud=Offline())
        with self.assertRaises(BrainOffline):
            await collect(r.stream([], "s"))

    async def test_auto_without_a_key_is_local_only(self):
        saved = {k: os.environ.pop(k, None) for k in ("ANTHROPIC_API_KEY", "VOICE_LLM")}
        os.environ["VOICE_LLM"] = "fake"
        try:
            r = brains.make_router("auto")
            self.assertIsNone(r.cloud)
            with self.assertRaises(BrainOffline):
                brains.make_router("cloud")
        finally:
            os.environ.pop("VOICE_LLM")
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v


# ---- the local client, against a fake OpenAI-compatible server ---------------------------------
SSE = [
    {"choices": [{"delta": {"role": "assistant"}}]},
    {"choices": [{"delta": {"content": "Sure"}}]},
    {"choices": [{"delta": {"content": ", I can."}}]},
    {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_a", "function": {"name": "set_expression", "arguments": '{"expr'}}]}}]},
    {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": 'ession": "soft"}'}}]}}]},
    {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
]


class FakeOpenAI(BaseHTTPRequestHandler):
    bodies: list = []

    def do_POST(self):
        FakeOpenAI.bodies.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for chunk in SSE:
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")

    def log_message(self, *a):
        pass


class LocalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeOpenAI)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"

    async def asyncTearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    async def test_stream_text_and_a_fragmented_tool_call(self):
        llm = brains.LocalLLM(self.url, "qwen3-test")
        hist = [{"role": "user", "text": "smile"},
                {"role": "assistant", "text": "", "tool_calls": [{"id": "x", "name": "face_gesture", "args": {"gesture": "bob"}}]},
                {"role": "tool", "results": [{"id": "x", "content": "done", "is_error": False}]},
                {"role": "user", "text": "again"}]
        ev = await collect(llm.stream(hist, "persona"))
        self.assertEqual(text_of(ev), "Sure, I can.")
        calls = [e for e in ev if isinstance(e, ToolCall)]
        self.assertEqual([(c.id, c.name, c.args) for c in calls], [("call_a", "set_expression", {"expression": "soft"})])
        self.assertEqual(ev[-1], Done("tool_use", "local:qwen3-test"))
        body = FakeOpenAI.bodies[-1]
        self.assertEqual(body["chat_template_kwargs"], {"enable_thinking": False})
        self.assertTrue(body["stream"])
        self.assertEqual(body["messages"][0], {"role": "system", "content": "persona"})
        self.assertEqual([m["role"] for m in body["messages"]], ["system", "user", "assistant", "tool", "user"])
        self.assertEqual({t["function"]["name"] for t in body["tools"]},
                         {"set_expression", "face_gesture", "robot_status", "end_conversation"})

    async def test_no_server_is_offline(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        with self.assertRaises(BrainOffline):
            await collect(brains.LocalLLM(f"http://127.0.0.1:{port}", "m", timeout_s=1).stream([], "p"))


# ---- the Claude backend, against a fake client ----------------------------------------------------
class FakeStream:
    def __init__(self, events, final):
        self.events, self.final = events, final

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def __aiter__(self):
        async def gen():
            for e in self.events:
                yield e
        return gen()

    async def get_final_message(self):
        return self.final


class FakeClient:
    def __init__(self, events, final):
        self.kw = None
        outer = self

        class Messages:
            def stream(self, **kw):
                outer.kw = kw
                return FakeStream(events, final)
        self.messages = Messages()


def text_ev(t):
    return NS(type="content_block_delta", delta=NS(type="text_delta", text=t))


class ClaudeTests(unittest.IsolatedAsyncioTestCase):
    async def test_request_shape_and_events(self):
        tool = NS(type="tool_use", id="toolu_1", name="face_gesture", input={"gesture": "wink_l"})
        events = [NS(type="message_start"), text_ev("Hi! "), text_ev("Nice to meet you."),
                  NS(type="content_block_stop", content_block=NS(type="text")),
                  NS(type="content_block_stop", content_block=tool)]
        client = FakeClient(events, NS(stop_reason="tool_use", model="claude-haiku-5-5", content=[tool]))
        llm = brains.ClaudeLLM(client=client)
        ev = await collect(llm.stream([{"role": "user", "text": "hello"}], "persona"))
        self.assertEqual(text_of(ev), "Hi! Nice to meet you.")
        self.assertEqual([(e.id, e.name, e.args) for e in ev if isinstance(e, ToolCall)],
                         [("toolu_1", "face_gesture", {"gesture": "wink_l"})])
        self.assertEqual(ev[-1], Done("tool_use", "claude-haiku-5-5"))
        kw = client.kw
        self.assertEqual(kw["model"], "claude-haiku-5-5")
        self.assertEqual(kw["thinking"], {"type": "disabled"})
        self.assertEqual(kw["output_config"], {"effort": "low"})
        self.assertEqual(kw["system"][0]["cache_control"], {"type": "ephemeral"})
        self.assertTrue(kw["system"][0]["text"].startswith("persona"))
        self.assertIn(brains.THINKING_OFF_GUARD, kw["system"][0]["text"])
        self.assertTrue(all(t["strict"] for t in kw["tools"]))
        self.assertNotIn("tool_choice", kw)            # auto: forced choice is not used
        self.assertEqual(kw["messages"], [{"role": "user", "content": "hello"}])

    async def test_refusal(self):
        client = FakeClient([], NS(stop_reason="refusal", model="claude-haiku-5-5", content=[]))
        ev = await collect(brains.ClaudeLLM(client=client).stream([{"role": "user", "text": "x"}], "p"))
        self.assertEqual(ev, [Done("refusal", "claude-haiku-5-5")])

    async def test_no_key_no_cloud(self):
        saved = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            with self.assertRaises(BrainOffline):
                brains.ClaudeLLM()
        finally:
            if saved is not None:
                os.environ["ANTHROPIC_API_KEY"] = saved

    def test_history_conversion(self):
        hist = [{"role": "user", "text": "hi"},
                {"role": "assistant", "text": "Hello.", "tool_calls": [{"id": "t", "name": "robot_status", "args": {}}]},
                {"role": "tool", "results": [{"id": "t", "content": "fine", "is_error": False},
                                             {"id": "u", "content": "no", "is_error": True}]}]
        m = brains.to_claude(hist)
        self.assertEqual(m[1]["content"][1], {"type": "tool_use", "id": "t", "name": "robot_status", "input": {}})
        self.assertEqual(m[2]["role"], "user")
        self.assertNotIn("is_error", m[2]["content"][0])
        self.assertTrue(m[2]["content"][1]["is_error"])


if __name__ == "__main__":
    unittest.main()
