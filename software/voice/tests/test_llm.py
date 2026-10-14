#!/usr/bin/env python3
"""The local brain (workplan 1.5): its pinned serving profile, how the service calls it, the readiness
check and warm-up against a fake model server, the start-up retry, history kept within the context,
and the exact command run-llm.sh would start vLLM with. No model, no GPU, no docker.

    python3 software/voice/tests/test_llm.py
"""
import asyncio
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from fakes import connect, make_voice, say, settle, speech_clip

import brains
import fetch_models
import llm_profile
import server
from brains import FakeLLM

HERE = os.path.dirname(os.path.abspath(__file__))
RUN_LLM = os.path.join(HERE, "..", "image", "run-llm.sh")


class Profile(unittest.TestCase):
    def test_the_decided_model_is_the_default(self):
        saved = os.environ.pop("VOICE_LLM_PROFILE", None)
        try:
            p = llm_profile.load()
        finally:
            if saved is not None:
                os.environ["VOICE_LLM_PROFILE"] = saved
        self.assertEqual(p["name"], "qwen3-30b-a3b-w4a16")
        self.assertEqual(p["repo"], "RedHatAI/Qwen3-30B-A3B-Instruct-2507-quantized.w4a16")
        self.assertEqual((p["served_name"], p["tool_parser"], p["max_model_len"]), ("qwen3-30b-a3b", "hermes", 8192))
        self.assertEqual(p["sampling"], {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "min_p": 0.0})

    def test_the_fallback_is_one_setting(self):
        os.environ["VOICE_LLM_PROFILE"] = "qwen3-4b"
        try:
            self.assertEqual(llm_profile.load()["served_name"], "qwen3-4b")
        finally:
            del os.environ["VOICE_LLM_PROFILE"]

    def test_unknown_and_tampered_profiles_are_refused(self):
        with self.assertRaises(llm_profile.ProfileError):
            llm_profile.load("parakeet")              # a model, but not a language model
        with open(llm_profile.LOCK) as f:
            lock = json.load(f)
        m = next(x for x in lock["models"] if x["name"] == "qwen3-4b")
        for field, bad in (("tool_parser", "hermes; rm -rf /"), ("served_name", "a b"), ("max_model_len", "8192")):
            m2 = json.loads(json.dumps(m))
            m2["serve"][field] = bad
            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
                json.dump({"models": [m2]}, f)
            try:
                with self.assertRaises(llm_profile.ProfileError, msg=field):
                    llm_profile.load("qwen3-4b", path=f.name)
            finally:
                os.unlink(f.name)

    def test_language_models_are_pinned_and_never_fetched_by_default(self):
        llms = [m for m in fetch_models.load_lock() if "serve" in m]
        self.assertEqual({m["name"] for m in llms}, {"qwen3-30b-a3b-w4a16", "qwen3-4b"})
        for m in llms:
            self.assertTrue(m["optional"])
            self.assertEqual(m["license"], "Apache-2.0")
            self.assertTrue(m["dest"].startswith("llm/"))
            self.assertTrue(any(f["path"].endswith(".safetensors") and "sha256" in f for f in m["files"]))
            self.assertTrue(any(f["path"] == "chat_template.jinja" or f["path"] == "tokenizer_config.json" for f in m["files"]))
        seen = []
        real = fetch_models.fetch
        fetch_models.fetch = lambda dest, models, **kw: seen.append([m["name"] for m in models]) or 0
        try:
            fetch_models.main(["--dest", tempfile.mkdtemp()])
            fetch_models.main(["--dest", tempfile.mkdtemp(), "--only", "qwen3-4b"])
        finally:
            fetch_models.fetch = real
        self.assertEqual(seen[0], ["parakeet", "kokoro", "g2p-fallback"])
        self.assertEqual(seen[1], ["qwen3-4b"])


class FakeVLLM(BaseHTTPRequestHandler):
    serves = ["qwen3-30b-a3b"]
    bodies: list = []

    def do_GET(self):
        if self.path == "/v1/models":
            body = json.dumps({"data": [{"id": i} for i in self.serves]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

    def do_POST(self):
        FakeVLLM.bodies.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(b'data: {"choices": [{"delta": {"content": "Hi"}}]}\n\n')
        self.wfile.write(b'data: {"choices": [{"delta": {}, "finish_reason": "stop"}]}\n\ndata: [DONE]\n\n')

    def log_message(self, *a):
        pass


class Serving(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        FakeVLLM.serves = ["qwen3-30b-a3b"]
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeVLLM)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.llm = brains.LocalLLM(self.url, profile=llm_profile.load("qwen3-30b-a3b-w4a16"))

    async def asyncTearDown(self):
        if self.srv is not None:
            self.srv.shutdown()
            self.srv.server_close()

    async def test_ready_only_when_serving_this_model(self):
        self.assertEqual(await self.llm.ready(), (True, None))
        FakeVLLM.serves = ["some-other-model"]
        ok, why = await self.llm.ready()
        self.assertFalse(ok)
        self.assertIn("not 'qwen3-30b-a3b'", why)

    async def test_warm_up_uses_the_persona_and_is_short(self):
        ms = await self.llm.warm_up("PERSONA TEXT")
        self.assertGreaterEqual(ms, 0)
        body = FakeVLLM.bodies[-1]
        self.assertEqual(body["model"], "qwen3-30b-a3b")
        self.assertEqual(body["max_tokens"], 4)
        self.assertEqual(body["messages"][0], {"role": "system", "content": "PERSONA TEXT"})
        self.assertTrue(body["tools"])                  # the same prefix a real turn sends, so it caches

    async def test_router_warms_the_local_brain_only(self):
        r = brains.Router("local", local=self.llm)
        ok, why, ms = await r.warm_up("p")
        self.assertTrue(ok)
        self.assertIsNotNone(ms)
        self.assertEqual(await brains.Router("cloud", cloud=FakeLLM()).warm_up("p"), (True, None, None))

    async def test_no_server_is_not_ready(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.srv = None                                  # already stopped; tearDown has nothing to do
        ok, why = await self.llm.ready()
        self.assertFalse(ok)
        self.assertIn("no model server", why)


class Startup(unittest.IsolatedAsyncioTestCase):
    async def test_warm_brain_retries_until_ready(self):
        answers = [(False, "no model server", None), (False, "no model server", None), (True, None, 42.0)]

        class Brain:
            async def warm_up(self, system):
                return answers.pop(0)
        v = make_voice()
        v.brain = Brain()
        sleeps = []

        async def sleep(s):
            sleeps.append(s)
        await server.warm_brain(v, sleep=sleep)
        self.assertEqual(v.brain_state, "ready")
        self.assertEqual(sleeps, [2, 4])

    async def test_a_turn_before_the_brain_is_ready_is_answered_honestly(self):
        from fakes import BrokenBrain
        import conversation
        v = make_voice(llm=BrokenBrain())
        link, page = await connect(v)
        await say(v, page, speech_clip())
        await settle(v)
        self.assertEqual(link.of("turn")[-1]["status"], "offline")
        self.assertEqual(v.tts.spoken, [conversation.OFFLINE])


class Context(unittest.IsolatedAsyncioTestCase):
    async def test_history_is_trimmed_at_a_question(self):
        import conversation
        from brains import ToolCall
        n = conversation.MAX_HISTORY_TURNS + 3
        script = []
        for i in range(n):
            script += [[ToolCall(f"t{i}", "face_gesture", {"gesture": "blink2"}), f"Answer {i}."], f"More {i}."]
        llm = FakeLLM(script)
        v = make_voice(llm=llm)
        link, page = await connect(v)
        for i in range(n):
            v.limiter._times.clear()
            await say(v, page, speech_clip())
            await settle(v)
        users = [h for h in v.history if h["role"] == "user"]
        self.assertEqual(len(users), conversation.MAX_HISTORY_TURNS)
        self.assertEqual(v.history[0]["role"], "user")             # never starts mid tool call
        for i, h in enumerate(v.history):
            if h["role"] == "tool":
                self.assertTrue(v.history[i - 1].get("tool_calls"))


@unittest.skipUnless(shutil.which("bash"), "needs bash")
class RunLLM(unittest.TestCase):
    def run_llm(self, *args, **env):
        e = {**os.environ, "OPENDUBS_VOICE_MODELS": "/var/lib/opendubs/models", **env}
        e.pop("VOICE_LLM_TOOL_PARSER", None)
        return subprocess.run(["bash", RUN_LLM, *args], capture_output=True, text=True, env=e)

    def setUp(self):
        self.stack = tempfile.NamedTemporaryFile("w", suffix=".env", delete=False)
        self.stack.write("VOICE_LLM_IMAGE=example/vllm@sha256:" + "a" * 64 + "\n")
        self.stack.close()

    def tearDown(self):
        os.unlink(self.stack.name)

    def test_the_command_comes_from_the_profile(self):
        r = self.run_llm("qwen3-30b-a3b-w4a16", "--dry-run", OPENDUBS_VERSIONS=self.stack.name, VOICE_LLM_GPU_SHARE="0.20")
        self.assertEqual(r.returncode, 0, r.stderr)
        cmd = r.stdout
        for part in ("-v /var/lib/opendubs/models/llm/qwen3-30b-a3b-w4a16:/model:ro", "-e HF_HUB_OFFLINE=1",
                     "vllm serve /model", "--served-model-name qwen3-30b-a3b", "--host 127.0.0.1",
                     "--gpu-memory-utilization 0.20", "--max-model-len 8192", "--max-num-seqs 2",
                     "--enable-prefix-caching", "--tool-call-parser hermes"):
            self.assertIn(part, cmd)

    def test_refusals(self):
        r = self.run_llm("qwen3-30b-a3b-w4a16", "--dry-run", OPENDUBS_VERSIONS=os.devnull, VOICE_LLM_GPU_SHARE="0.20")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("VOICE_LLM_IMAGE", r.stderr)
        r = self.run_llm("qwen3-30b-a3b-w4a16", "--dry-run", OPENDUBS_VERSIONS=self.stack.name)
        self.assertIn("VOICE_LLM_GPU_SHARE", r.stderr)
        r = self.run_llm("kokoro", "--dry-run", OPENDUBS_VERSIONS=self.stack.name, VOICE_LLM_GPU_SHARE="0.20")
        self.assertIn("no such profile", r.stderr)


if __name__ == "__main__":
    unittest.main()
