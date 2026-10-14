"""The language model behind the voice: one interface, three backends, and a router.

    async for event in brain.stream(history, system): ...
        TextDelta(text)                       spoken text, as it arrives
        ToolCall(id, name, args)              a request for a face tool (safe_tools checks it)
        Done(stop_reason, served_by)          "end_turn" | "tool_use" | "refusal" | "max_tokens" | ...

History is neutral, so a turn can move between backends (the `auto` fallback):

    {"role": "user", "text": "..."}
    {"role": "assistant", "text": "...", "tool_calls": [{"id", "name", "args"}]}
    {"role": "tool", "results": [{"id", "content", "is_error"}]}

Backends, chosen by VOICE_BRAIN (`local` | `cloud` | `auto`; voice note D4):

  FakeLLM     scripted replies; tests and a desk demo
  LocalLLM    the local brain: vLLM on the Thor at VOICE_LLM_URL, serving the model of the profile
              VOICE_LLM_PROFILE names (llm_profile.py; Qwen3-30B-A3B by default, workplan V-O3)
  ClaudeLLM   the Claude API (Haiku 5.5), opt-in per robot. TEXT ONLY leaves the robot: the
              transcript and the persona, never audio. The key is read from the environment, which
              on a robot is the root-owned /etc/opendubs/voice.env, never the repo or the browser.

The router's `auto` mode uses the cloud while it is healthy, gives it FIRST_TEXT_S to start
answering, and otherwise sends the same history to the local model and marks the cloud unhealthy
for UNHEALTHY_S. Once any text has been spoken it never switches: words cannot be unsaid.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

import safe_tools

log = logging.getLogger("voice.brain")

FIRST_TEXT_S = 2.5        # [E] voice note §8.2; gate C-1 measures what the uplink really gives
UNHEALTHY_S = 60.0
MAX_TOKENS = 1024         # a spoken reply is two or three sentences; this is a runaway cap


@dataclass
class TextDelta:
    text: str


@dataclass
class ToolCall:
    id: str
    name: str
    args: object          # usually a dict; anything else is refused by safe_tools.plan


@dataclass
class Done:
    stop_reason: str
    served_by: str = ""


class BrainOffline(RuntimeError):
    """No answer could be had (no server, no network, an API error)."""


# ---- fake ---------------------------------------------------------------------------------------
@dataclass
class FakeLLM:
    """Replies from a script: each item is a str (spoken), or a ToolCall, or a list of them for
    one model round. When the script runs out it says `default`. delay_s delays the first event."""
    script: list = field(default_factory=list)
    default: str = "Hello! I'm a robot on a desk, so my answers are canned for now."
    delay_s: float = 0.0
    stop: str = ""                    # force a stop_reason, e.g. "refusal"
    name: str = "fake"
    calls: list = field(default_factory=list)

    async def stream(self, history, system):
        self.calls.append(list(history))
        await asyncio.sleep(self.delay_s)
        item = self.script.pop(0) if self.script else self.default
        items = item if isinstance(item, list) else [item]
        tools = False
        for it in items:
            if isinstance(it, ToolCall):
                tools = True
                yield it
            else:
                for word in str(it).split(" "):
                    yield TextDelta(word + " ")
        yield Done(self.stop or ("tool_use" if tools else "end_turn"), self.name)


# ---- local: OpenAI-compatible -------------------------------------------------------------------
def to_openai(history, system: str) -> list[dict]:
    out = [{"role": "system", "content": system}]
    for h in history:
        if h["role"] == "user":
            out.append({"role": "user", "content": h["text"]})
        elif h["role"] == "assistant":
            m = {"role": "assistant", "content": h.get("text") or ""}
            if h.get("tool_calls"):
                m["tool_calls"] = [{"id": c["id"], "type": "function",
                                    "function": {"name": c["name"], "arguments": json.dumps(c["args"])}}
                                   for c in h["tool_calls"]]
            out.append(m)
        elif h["role"] == "tool":
            for r in h["results"]:
                out.append({"role": "tool", "tool_call_id": r["id"], "content": r["content"]})
    return out


class LocalLLM:
    """Streams chat completions from an OpenAI-compatible server (vLLM) on loopback.

    The model's name and sampling come from its serving profile (llm_profile.py), the same one
    run-llm.sh starts vLLM from. A model that thinks before its first word turns 50 ms into seconds
    (voice note §3.3); the chosen Qwen3-2507 Instruct models never think, and a profile that needs
    thinking switched off says so in `chat_template_kwargs`. `profile` is a seam for the tests."""

    def __init__(self, url: str | None = None, model: str | None = None, timeout_s: float = 10.0,
                 profile: dict | None = None):
        if profile is None:
            import llm_profile
            profile = llm_profile.load()
        self.profile = profile
        self.url = (url or os.environ.get("VOICE_LLM_URL", "http://127.0.0.1:8000")).rstrip("/")
        self.model = model or os.environ.get("VOICE_LLM_MODEL") or profile["served_name"]
        self.sampling = dict(profile.get("sampling") or {})
        self.timeout_s = timeout_s
        self.name = f"local:{self.model}"

    def _body(self, history, system, max_tokens: int = MAX_TOKENS) -> bytes:
        body = {"model": self.model, "stream": True, "max_tokens": max_tokens,
                "messages": to_openai(history, system), "tools": safe_tools.openai_tools(),
                **self.sampling}
        if self.profile.get("chat_template_kwargs"):
            body["chat_template_kwargs"] = self.profile["chat_template_kwargs"]
        return json.dumps(body).encode()

    def _models(self) -> list[str]:
        with urllib.request.urlopen(f"{self.url}/v1/models", timeout=3) as r:
            return [m.get("id") for m in json.loads(r.read()).get("data", [])]

    async def ready(self) -> tuple[bool, str | None]:
        """(True, None) once the server answers and serves THIS model; otherwise why not. A server
        serving some other model is not ready: the robot would be talking with the wrong brain."""
        try:
            ids = await asyncio.get_running_loop().run_in_executor(None, self._models)
        except (urllib.error.URLError, OSError, ValueError) as e:
            return False, f"no model server at {self.url} ({type(e).__name__})"
        if self.model not in ids:
            return False, f"the server at {self.url} serves {ids}, not {self.model!r}"
        return True, None

    async def warm_up(self, system: str) -> float:
        """One short request with the real persona and tools, so the first visitor does not pay for
        a cold start, and the persona's prefix is cached. Returns ms to the first event."""
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        first = None
        async for _ in self.stream([{"role": "user", "text": "Hello."}], system, max_tokens=4):
            first = first if first is not None else loop.time()
        return ((first or loop.time()) - t0) * 1000

    def _pump(self, body: bytes, put, cancelled: threading.Event) -> None:
        """In a thread: read the SSE stream, hand each `data:` payload to put()."""
        req = urllib.request.Request(f"{self.url}/v1/chat/completions", data=body,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
                for raw in r:
                    if cancelled.is_set():
                        return
                    line = raw.decode("utf-8", "replace").strip()
                    if line.startswith("data:"):
                        put(line[5:].strip())
        except (urllib.error.URLError, OSError) as e:
            put(BrainOffline(f"local model at {self.url}: {e}"))
        finally:
            put(None)

    async def stream(self, history, system, max_tokens: int = MAX_TOKENS):
        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue()
        cancelled = threading.Event()
        threading.Thread(target=self._pump, daemon=True,
                         args=(self._body(history, system, max_tokens),
                               lambda x: loop.call_soon_threadsafe(q.put_nowait, x), cancelled)).start()
        calls: dict[int, dict] = {}
        finish = None
        try:
            while True:
                item = await q.get()
                if item is None:
                    break
                if isinstance(item, Exception):
                    raise item
                if item == "[DONE]":
                    continue
                try:
                    choice = (json.loads(item).get("choices") or [{}])[0]
                except ValueError:
                    continue
                delta = choice.get("delta") or {}
                if delta.get("content"):
                    yield TextDelta(delta["content"])
                for tc in delta.get("tool_calls") or []:
                    c = calls.setdefault(tc.get("index", 0), {"id": "", "name": "", "args": ""})
                    c["id"] = tc.get("id") or c["id"]
                    fn = tc.get("function") or {}
                    c["name"] = fn.get("name") or c["name"]
                    c["args"] += fn.get("arguments") or ""
                finish = choice.get("finish_reason") or finish
        finally:
            cancelled.set()
        for i, c in sorted(calls.items()):
            try:
                args = json.loads(c["args"] or "{}")
            except ValueError:
                args = c["args"]                       # refused by safe_tools.plan, not raised
            yield ToolCall(c["id"] or f"call_{i}", c["name"], args)
        reason = {"stop": "end_turn", "tool_calls": "tool_use", "length": "max_tokens"}.get(finish, finish or "end_turn")
        yield Done(reason, self.name)


# ---- cloud: the Claude API ----------------------------------------------------------------------
# With thinking off, the model can write a tool call as text instead of calling it; this sentence
# is the API's own recommended guard for a thinking-off route.
THINKING_OFF_GUARD = ("When you use a tool, you may say a brief sentence first. If no tool can "
                      "express what the user asked for, say so instead of guessing. Do not include "
                      "internal or system XML tags in your response.")


def to_claude(history) -> list[dict]:
    out = []
    for h in history:
        if h["role"] == "user":
            out.append({"role": "user", "content": h["text"]})
        elif h["role"] == "assistant":
            blocks = [{"type": "text", "text": h["text"]}] if h.get("text") else []
            blocks += [{"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["args"]}
                       for c in h.get("tool_calls") or []]
            out.append({"role": "assistant", "content": blocks or [{"type": "text", "text": "…"}]})
        elif h["role"] == "tool":
            out.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": r["id"], "content": r["content"],
                 **({"is_error": True} if r.get("is_error") else {})} for r in h["results"]]})
    return out


class ClaudeLLM:
    """Claude Haiku 5.5, streamed, thinking off, effort low. Haiku has no server-side refusal
    fallback, so a refusal comes back as Done("refusal") and the service speaks a canned decline."""

    def __init__(self, model: str | None = None, client=None):
        self.model = model or os.environ.get("VOICE_CLOUD_MODEL", "claude-haiku-5-5")
        self.name = f"cloud:{self.model}"
        if client is None:
            if not (os.environ.get("ANTHROPIC_API_KEY") or "").strip():
                raise BrainOffline("no ANTHROPIC_API_KEY: on a robot it lives only in the root-owned "
                                   "/etc/opendubs/voice.env (notes/workplan-voice.md)")
            import anthropic
            client = anthropic.AsyncAnthropic(max_retries=0, timeout=10.0)   # the router falls back; retries would only delay it
        self.client = client

    async def stream(self, history, system):
        try:
            import anthropic
            api_errors = (anthropic.APIError,)
        except ImportError:                  # a test's fake client, with no SDK installed
            api_errors = ()
        try:
            async with self.client.messages.stream(
                model=self.model,
                max_tokens=MAX_TOKENS,
                system=[{"type": "text", "text": system + "\n\n" + THINKING_OFF_GUARD,
                         "cache_control": {"type": "ephemeral"}}],
                tools=safe_tools.tool_definitions(),
                messages=to_claude(history),
                thinking={"type": "disabled"},
                output_config={"effort": "low"},
            ) as stream:
                async for event in stream:
                    if event.type == "content_block_delta" and event.delta.type == "text_delta":
                        yield TextDelta(event.delta.text)
                    elif event.type == "content_block_stop" and event.content_block.type == "tool_use":
                        # as soon as the call is complete, so the face changes while it speaks.
                        # If the turn is then refused mid-stream, a call already made stays made;
                        # safe_tools allowed it, so it was a face change at most.
                        b = event.content_block
                        yield ToolCall(b.id, b.name, b.input)
                final = await stream.get_final_message()
        except api_errors as e:
            raise BrainOffline(f"Claude API: {type(e).__name__}: {e}") from None
        yield Done(final.stop_reason or "end_turn", final.model)


# ---- the router ---------------------------------------------------------------------------------
class Router:
    def __init__(self, mode: str, local=None, cloud=None, first_text_s: float = FIRST_TEXT_S,
                 unhealthy_s: float = UNHEALTHY_S, clock=time.monotonic):
        if mode not in ("local", "cloud", "auto"):
            raise ValueError(f"VOICE_BRAIN={mode!r}: use local, cloud or auto")
        if mode in ("local", "auto") and local is None:
            raise ValueError(f"VOICE_BRAIN={mode} needs a local brain")
        if mode == "cloud" and cloud is None:
            raise ValueError("VOICE_BRAIN=cloud needs a cloud brain (and its key)")
        self.mode, self.local, self.cloud = mode, local, cloud
        self.first_text_s, self.unhealthy_s, self.clock = first_text_s, unhealthy_s, clock
        self.cloud_bad_until = 0.0
        self.last_served_by = ""

    async def warm_up(self, system: str):
        """Warm the local brain, if this router uses one. Returns (ready, why, first-event ms)."""
        if self.mode == "cloud" or not hasattr(self.local, "ready"):
            return True, None, None
        ok, why = await self.local.ready()
        if not ok:
            return False, why, None
        return True, None, await self.local.warm_up(system)

    def cloud_healthy(self) -> bool:
        return self.cloud is not None and self.clock() >= self.cloud_bad_until

    async def stream(self, history, system):
        if self.mode == "local" or (self.mode == "auto" and not self.cloud_healthy()):
            async for e in self._tag(self.local.stream(history, system)):
                yield e
            return
        if self.mode == "cloud":
            async for e in self._tag(self.cloud.stream(history, system)):
                yield e
            return
        # auto, cloud believed healthy: give it first_text_s to produce its first event
        agen = self.cloud.stream(history, system).__aiter__()
        try:
            first = await asyncio.wait_for(agen.__anext__(), self.first_text_s)
        except (asyncio.TimeoutError, BrainOffline, StopAsyncIteration) as e:
            log.warning("cloud brain fell back to local: %s", type(e).__name__)
            self.cloud_bad_until = self.clock() + self.unhealthy_s
            await agen.aclose()
            async for e2 in self._tag(self.local.stream(history, system)):
                yield e2
            return
        yield self._note(first)
        async for e in agen:                 # committed: errors now end the turn, never switch
            yield self._note(e)

    def _note(self, e):
        if isinstance(e, Done):
            self.last_served_by = e.served_by
        return e

    async def _tag(self, agen):
        async for e in agen:
            yield self._note(e)


def make_router(mode: str | None = None) -> Router:
    mode = mode or os.environ.get("VOICE_BRAIN", "local")
    llm = os.environ.get("VOICE_LLM", "local")
    local = FakeLLM() if llm == "fake" else LocalLLM()
    cloud = None
    if mode in ("cloud", "auto"):
        try:
            cloud = ClaudeLLM()
        except BrainOffline as e:
            if mode == "cloud":
                raise
            log.warning("auto brain without the cloud: %s", e)
    return Router(mode, local=local, cloud=cloud)
