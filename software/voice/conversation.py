"""Turns: who holds the conversation, what state it is in, and one push-to-talk turn end to end.

One `Voice` per robot. Pages (face pages, over /ws/voice) join and leave; at most one holds the
conversation at a time, and a press from another page while a turn is in flight is answered `busy`.

    idle --press--> listen --release (or the 12 s cap)--> think --first audio--> speak --played--> idle
                      ^                                     |                       |
                      +----------- press (barge-in) --------+-----------------------+

Rules carried from the head voice plan and the voice note:

- **Half-duplex.** Uplink frames are taken only in `listen`; anything else is dropped and counted.
- **Bounded utterances.** The clip runs from the press, not the speech onset (the first plosive is
  not lost), and is cut at MAX_UTTERANCE_S.
- **NO_SPEECH never reaches a model.** A clip without speech, or an empty transcript, gets a
  canned "say that again" and status `unintelligible`.
- **The face never waits on the pipeline.** `think` is sent at release, before STT starts.
- **Barge-in by button.** A press during think or speak cuts the reply (the page drops queued
  audio on `stop`) and starts a new utterance.
- **Voice is off during shows.** When the gate closes, a turn in flight is cut and presses are
  answered `off`.
- **Nothing is kept.** No audio is stored. The conversation history lives in memory and is cleared
  when the conversation ends (a goodbye, CONVO_IDLE_S of quiet, or the page leaving). Transcripts
  are not logged unless OPENDUBS_VOICE_LOG_TEXT=1 (desk debugging only).
"""
from __future__ import annotations

import asyncio
import itertools
import json
import logging
import os
import time

import audio
import brains
import safe_tools
import vad
import wakeword
import wire
from sentences import Splitter

log = logging.getLogger("voice.turn")

MAX_UTTERANCE_S = 12.0
CONVO_IDLE_S = 20.0       # [E] voice note §5.5; gate V-6 (mic reopen time) settles it
SLOW_S = 6.0              # no reply yet: "give me a moment"
GIVE_UP_S = 10.0          # still nothing: apologise and drop the turn
TURN_CAP_S = 30.0         # a brain that starts and then stalls is cut here
PLAYED_GRACE_S = 2.0      # after the audio's length, idle even if the page never says `played`
MAX_ROUNDS = 3            # model rounds per turn (a tool result, then the rest of the answer)
MAX_HISTORY_TURNS = 6     # questions kept in a conversation; the local brain's context is 8,192 tokens

SAY_AGAIN = "Sorry, could you say that again?"
DECLINE = "I'd rather not talk about that."
MOMENT = "Give me a moment."
LOST = "Sorry, I lost my train of thought."
OFFLINE = "Sorry, I can't think right now."


class Page:
    """One connected page. `link` has async send_text(str) and send_bytes(bytes)."""
    _ids = itertools.count(1)

    def __init__(self, link):
        self.id = next(self._ids)
        self.link = link
        self.hello = False
        self.dropped = 0          # uplink frames outside `listen`
        self.gone = False

    async def text(self, s: str) -> None:
        if self.gone:
            return
        try:
            await self.link.send_text(s)
        except Exception:
            self.gone = True

    async def bytes(self, b: bytes) -> None:
        if self.gone:
            return
        try:
            await self.link.send_bytes(b)
        except Exception:
            self.gone = True


def _robot_status(dashboard: str) -> dict | None:
    from gate import http_get_json
    code, body = http_get_json(dashboard.rstrip("/") + "/api/choreo/robot")
    return body if code == 200 else None


class Voice:
    def __init__(self, stt, tts, brain, face, gate, persona: str, *,
                 robot_status=None, clock=time.monotonic,
                 max_utterance_s=MAX_UTTERANCE_S, convo_idle_s=CONVO_IDLE_S, slow_s=SLOW_S,
                 give_up_s=GIVE_UP_S, turn_cap_s=TURN_CAP_S, played_grace_s=PLAYED_GRACE_S,
                 wake: bool = False, make_vad=vad.make_vad):
        self.stt, self.tts, self.brain, self.face, self.gate = stt, tts, brain, face, gate
        self.persona = persona
        dash = getattr(gate, "url", "http://127.0.0.1:8080").split("/api/")[0]
        self.robot_status = robot_status or (lambda: _robot_status(dash))
        self.clock = clock
        self.max_utterance_s, self.convo_idle_s = max_utterance_s, convo_idle_s
        self.slow_s, self.give_up_s, self.turn_cap_s = slow_s, give_up_s, turn_cap_s
        self.played_grace_s = played_grace_s
        self.log_text = os.environ.get("OPENDUBS_VOICE_LOG_TEXT") == "1"

        self.pages: dict[int, Page] = {}
        self.owner: Page | None = None
        self.state = "idle"
        self.turn = 0
        self.history: list = []
        self.limiter = safe_tools.Limiter()
        self._buf = bytearray()
        self._expect_seq = 0
        self._lost = 0
        self._t_press = 0.0
        self._task: asyncio.Task | None = None       # the pipeline of the turn in think/speak
        self._cap: asyncio.Task | None = None        # the utterance cap timer
        self._quiet: asyncio.Task | None = None      # the conversation window
        self._played = asyncio.Event()
        self._end_after_turn = False
        self._mark = 0
        self.wake_enabled = wake               # the tablet may start turns with "hey dubs" (wakeword.py)
        self.make_vad = make_vad
        self._source = "talk"                  # how the turn in hand began: "talk" or "wake"
        self._endpointer = None                # vad.Endpointer, for a wake turn
        self.brain_state = "unknown"              # server.warm_brain: warming | ready | unavailable
        gate.listeners.append(self.on_gate)

    # ---- pages ----------------------------------------------------------------------------------
    async def join(self, link) -> Page:
        p = Page(link)
        self.pages[p.id] = p
        return p

    async def leave(self, p: Page) -> None:
        self.pages.pop(p.id, None)
        p.gone = True
        if self.owner is p:
            if self.state != "idle":
                await self._cut("the page left", notify=False)
            await self._end_conversation("the page left", notify=False)

    async def on_text(self, p: Page, text: str) -> None:
        try:
            msg = wire.parse_control(text)
        except wire.WireError as e:
            await p.text(wire.control("error", code="VOICE_BAD_MESSAGE", message=str(e)))
            return
        kind = msg["type"]
        if kind == "hello":
            why = wire.check_hello(msg)
            if why:
                await p.text(wire.control("error", code="VOICE_BAD_HELLO", message=why))
                return
            p.hello = True
            await p.text(wire.ready(self.gate.on, self.gate.why, self.max_utterance_s,
                                    wake={"keyword": wakeword.PHRASE} if self.wake_enabled else None))
            return
        if not p.hello:
            await p.text(wire.control("error", code="VOICE_NO_HELLO", message="send hello first"))
            return
        if kind == "press":
            await self.press(p)
        elif kind == "wake":
            await self.wake(p, msg)
        elif kind == "release":
            await self.release(p)
        elif kind == "barge_in":
            if self.owner is p and self.state in ("think", "speak"):
                await self._cut("barge_in")
        elif kind == "played":
            if self.owner is p and msg.get("turn") == self.turn:
                self._played.set()
        elif kind == "bye":
            await self.leave(p)

    async def on_bytes(self, p: Page, data: bytes) -> None:
        if self.state != "listen" or self.owner is not p:
            p.dropped += 1
            return
        try:
            seq, _page_ms, pcm = wire.parse_up(data)
        except wire.WireError as e:
            await p.text(wire.control("error", code="VOICE_BAD_FRAME", message=str(e)))
            return
        if seq > self._expect_seq:
            self._lost += seq - self._expect_seq
        self._expect_seq = max(self._expect_seq, seq + 1)
        self._buf += pcm
        if self._endpointer is not None:
            ended = self._endpointer.feed(pcm)
            if ended == "no_speech":
                await self._not_addressed(p, "nothing was said after the wake word")
                return
            if ended == "end":
                await self.release(p)
                return
        if len(self._buf) >= self.max_utterance_s * wire.UP_RATE * 2:
            await self.release(p)

    # ---- the gate -------------------------------------------------------------------------------
    async def on_gate(self, on: bool, why) -> None:
        for p in list(self.pages.values()):
            await p.text(wire.control("voice", on=on, why=why))
        if not on and self.state != "idle":
            await self._cut(why or "voice is off")

    # ---- a turn ---------------------------------------------------------------------------------
    async def wake(self, p: Page, msg: dict) -> None:
        """The tablet heard "hey dubs". Like a press, except the service ends the utterance, and a
        wake never cuts a reply: the page does not listen while the robot speaks, so a wake then is
        the robot hearing itself."""
        if not self.wake_enabled:
            await p.text(wire.control("error", code="VOICE_NO_WAKE",
                                      message="this service does not take wake turns; use Talk"))
            return
        why = wire.check_wake(msg)
        if why:
            await p.text(wire.control("error", code="VOICE_BAD_MESSAGE", message=why))
            return
        if self.state != "idle":
            return
        await self.press(p, source="wake", pre_roll_ms=msg.get("pre_roll_ms", 0))

    async def press(self, p: Page, source: str = "talk", pre_roll_ms: float = 0) -> None:
        if not self.gate.on:
            await p.text(wire.control("turn", turn=None, status="off", why=self.gate.why))
            return
        if self.owner is not None and self.owner is not p and self.state != "idle":
            await p.text(wire.control("turn", turn=None, status="busy",
                                      why="another page is talking to the robot"))
            return
        if self.owner is p and self.state == "listen":
            return
        if self.state in ("think", "speak"):
            await self._cut("barge_in")
        if self.owner is not p:
            self.history = []                       # a different page is a different conversation
        self.owner = p
        self._cancel(self._quiet)
        self.turn += 1
        self._buf = bytearray()
        self._expect_seq = self._lost = 0
        self._t_press = self.clock()
        self._source = source
        self._endpointer = vad.Endpointer(self.make_vad(), pre_roll_ms) if source == "wake" else None
        await self._set_state("listen")
        self._cap = asyncio.create_task(self._cap_timer(p, self.turn))

    async def _cap_timer(self, p: Page, turn: int) -> None:
        await asyncio.sleep(self.max_utterance_s + 0.5)
        if self.turn == turn and self.state == "listen":
            await self.release(p)

    async def release(self, p: Page) -> None:
        if self.state != "listen" or self.owner is not p:
            return
        self._cancel(self._cap)
        clip = bytes(self._buf)
        self._buf = bytearray()
        self._endpointer = None
        self._played.clear()
        self._mark = len(self.history)            # this turn's entries start here
        await self._set_state("think")
        self._task = asyncio.create_task(self._run_turn(p, self.turn, clip, self._source))

    async def _not_addressed(self, p: Page, why: str, turn: int | None = None, ms: dict | None = None) -> None:
        """End a wake turn that was not one, SILENTLY: nothing spoken, nothing kept in the history."""
        self._cancel(self._cap)
        self._buf = bytearray()
        self._endpointer = None
        turn = self.turn if turn is None else turn
        await p.text(wire.control("turn", turn=turn, status="not_addressed", source="wake", why=why,
                                  transcript="", reply="", ms=ms or {}))
        log.info("turn %d not addressed: %s", turn, why)
        await self._set_state("idle")

    async def _run_turn(self, p: Page, turn: int, clip: bytes, source: str = "talk") -> None:
        t_release = self.clock()
        ms = {"utterance": round(audio.duration_ms(clip, wire.UP_RATE))}
        out = {"status": "ok", "transcript": "", "reply": "", "source": source}
        speaker = Speaker(self, p, turn)
        try:
            if source == "wake":
                # A wake turn proceeds only if Parakeet heard the wake phrase at its start; the rest
                # is the question. Anything else ends silently: a false wake never speaks.
                rest = None
                if audio.has_speech(clip, wire.UP_RATE):
                    tr = await self.stt.transcribe(clip)
                    ms["stt"] = round((self.clock() - t_release) * 1000)
                    rest = wakeword.strip(tr.text)
                if not rest:
                    speaker.cancel()
                    await self._not_addressed(p, "no wake phrase in the transcript" if rest is None
                                              else "only the wake phrase was said", turn, ms)
                    return
                out["transcript"] = rest
                await p.text(wire.control("heard", turn=turn, transcript=rest))
                await self._answer(out, speaker, t_release, ms)
            elif not audio.has_speech(clip, wire.UP_RATE):
                out["status"] = "unintelligible"
                speaker.say(SAY_AGAIN)
            else:
                tr = await self.stt.transcribe(clip)
                ms["stt"] = round((self.clock() - t_release) * 1000)
                out["transcript"] = tr.text.strip()
                if not out["transcript"]:
                    out["status"] = "unintelligible"
                    speaker.say(SAY_AGAIN)
                else:
                    await p.text(wire.control("heard", turn=turn, transcript=out["transcript"]))
                    await self._answer(out, speaker, t_release, ms)
            audio_ms = await speaker.finish()
            if speaker.t_first_audio is not None:
                ms["first_audio"] = round((speaker.t_first_audio - t_release) * 1000)
            ms["turn"] = round((self.clock() - t_release) * 1000)
            await p.text(wire.control("tts_end", turn=turn, audio_ms=round(audio_ms)))
            await p.text(wire.control("turn", turn=turn, **out, ms=ms))
            self._log(turn, out, ms, p)
            if audio_ms > 0:
                try:
                    await asyncio.wait_for(self._played.wait(), audio_ms / 1000 + self.played_grace_s)
                except asyncio.TimeoutError:
                    pass
            await self._set_state("idle")
            if self._end_after_turn:
                await self._end_conversation("goodbye")
            else:
                self._quiet = asyncio.create_task(self._quiet_timer(turn))
        except asyncio.CancelledError:
            speaker.cancel()
            raise
        except Exception:
            log.exception("turn %d failed", turn)
            speaker.cancel()
            await p.text(wire.control("turn", turn=turn, status="offline", transcript=out["transcript"],
                                      reply="", ms=ms))
            await self._set_state("idle")
        finally:
            if self._task is asyncio.current_task():
                self._task = None

    async def _answer(self, out: dict, speaker: "Speaker", t_release: float, ms: dict) -> None:
        """Ask the brain, speaking each sentence as it completes. Fills out[status, reply]."""
        self._trim_history()
        self._mark = len(self.history)
        self.history.append({"role": "user", "text": out["transcript"]})
        think = asyncio.create_task(self._think(out, speaker, t_release, ms))
        try:
            done, _ = await asyncio.wait({think}, timeout=self.slow_s)
            if not done and not speaker.brain_spoke:
                speaker.say(MOMENT)
            if not done:
                done, _ = await asyncio.wait({think}, timeout=max(0.0, self.give_up_s - self.slow_s))
            if not done and not speaker.brain_spoke:
                out["status"] = "timeout"
                speaker.say(LOST)
                self._drop_turn()
                return
            if not done:
                done, _ = await asyncio.wait({think}, timeout=max(0.0, self.turn_cap_s - self.give_up_s))
                if not done:
                    out["status"] = "timeout"
                    self._drop_turn()
                    return
            try:
                think.result()
            except brains.BrainOffline as e:
                log.warning("no brain: %s", e)
                out["status"] = "offline"
                speaker.say(OFFLINE)
                self._drop_turn()
        finally:
            if not think.done():
                think.cancel()
                await asyncio.gather(think, return_exceptions=True)

    async def _think(self, out: dict, speaker: "Speaker", t_release: float, ms: dict) -> None:
        reply = []
        for _round in range(MAX_ROUNDS):
            split, text, calls, effects = Splitter(), [], [], []
            stop = "end_turn"
            async for ev in self.brain.stream(self.history, self.persona):
                if isinstance(ev, brains.TextDelta):
                    if "first_text" not in ms:
                        ms["first_text"] = round((self.clock() - t_release) * 1000)
                    text.append(ev.text)
                    for s in split.feed(ev.text):
                        speaker.say(s, from_brain=True)
                elif isinstance(ev, brains.ToolCall):
                    calls.append(ev)
                    effects.append(asyncio.create_task(self._tool(ev)))
                elif isinstance(ev, brains.Done):
                    stop = ev.stop_reason
            for s in split.flush():
                speaker.say(s, from_brain=True)
            said = "".join(text).strip()
            if stop == "refusal":
                out["status"] = "declined"
                speaker.say(DECLINE)
                for t in effects:
                    t.cancel()
                self._drop_turn()              # a refused question is not carried forward
                out["reply"] = " ".join(reply + [DECLINE])
                return
            reply.append(said)
            results = await asyncio.gather(*effects) if effects else []
            self.history.append({"role": "assistant", "text": said,
                                 "tool_calls": [{"id": c.id, "name": c.name, "args": c.args} for c in calls]})
            if calls:
                self.history.append({"role": "tool", "results": list(results)})
            if stop != "tool_use" or not calls:
                break
        out["reply"] = " ".join(r for r in reply if r)

    async def _tool(self, call: brains.ToolCall) -> dict:
        act = safe_tools.plan(call.name, call.args, self.limiter, now=self.clock())
        if act.refused:
            return {"id": call.id, "content": act.refused, "is_error": True}
        if act.head_line:
            reply = await self.face.send(act.head_line)
            if reply.startswith("ERR"):
                return {"id": call.id, "content": f"the face did not take it ({reply})", "is_error": True}
            return {"id": call.id, "content": act.result_hint or "done", "is_error": False}
        if act.http:
            robot = await asyncio.get_running_loop().run_in_executor(None, self.robot_status)
            return {"id": call.id, "content": safe_tools.status_summary(robot), "is_error": False}
        if act.end_conversation:
            self._end_after_turn = True
            return {"id": call.id, "content": act.result_hint, "is_error": False}
        return {"id": call.id, "content": "nothing happened", "is_error": True}

    # ---- ending ---------------------------------------------------------------------------------
    async def _cut(self, why: str, notify: bool = True) -> None:
        """Stop the turn in flight now. The page drops queued audio on `stop`."""
        turn, owner = self.turn, self.owner
        self._cancel(self._cap)
        self._endpointer = None
        task, self._task = self._task, None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        if task:
            self._drop_turn()                  # an unanswered question is not carried forward
        if notify and owner:
            await owner.text(wire.control("stop", turn=turn, why=why))
            await owner.text(wire.control("turn", turn=turn, status="cut", why=why))
        log.info("turn %d cut: %s", turn, why)
        await self._set_state("idle")

    async def _quiet_timer(self, turn: int) -> None:
        await asyncio.sleep(self.convo_idle_s)
        if self.turn == turn and self.state == "idle":
            await self._end_conversation("quiet")

    async def _end_conversation(self, why: str, notify: bool = True) -> None:
        self._cancel(self._quiet)
        self.history = []
        self._end_after_turn = False
        owner, self.owner = self.owner, None
        if notify and owner:
            await owner.text(wire.control("conversation_end", why=why))

    # ---- helpers --------------------------------------------------------------------------------
    async def _set_state(self, s: str) -> None:
        self.state = s
        if self.owner:
            await self.owner.text(wire.control("state", turn=self.turn, convo=s))
        asyncio.create_task(self.face.convo(s))      # never wait on the face door

    def _trim_history(self) -> None:
        """Keep the last MAX_HISTORY_TURNS - 1 questions and everything after them, cutting only at
        a question, so a tool call is never separated from its result."""
        users = [i for i, h in enumerate(self.history) if h["role"] == "user"]
        if len(users) >= MAX_HISTORY_TURNS:
            del self.history[:users[len(users) - MAX_HISTORY_TURNS + 1]]

    def _drop_turn(self) -> None:
        del self.history[self._mark:]

    @staticmethod
    def _cancel(t: asyncio.Task | None) -> None:
        if t and not t.done() and t is not asyncio.current_task():
            t.cancel()

    def _log(self, turn, out, ms, p: Page) -> None:
        rec = {"turn": turn, "status": out["status"], "source": out.get("source", "talk"), "ms": ms, "lost_frames": self._lost,
               "dropped_frames": p.dropped, "brain": getattr(self.brain, "last_served_by", "")}
        if self.log_text:
            rec.update(transcript=out["transcript"], reply=out["reply"])
        log.info("turn %s", json.dumps(rec))


class Speaker:
    """Sentences in, audio out to one page, in order, while the brain is still writing."""

    def __init__(self, voice: Voice, page: Page, turn: int):
        self.voice, self.page, self.turn = voice, page, turn
        self.q: asyncio.Queue = asyncio.Queue()
        self.seq = 0
        self.audio_bytes = 0
        self.t_first_audio: float | None = None
        self.brain_spoke = False
        self.task = asyncio.create_task(self._run())

    def say(self, text: str, from_brain: bool = False) -> None:
        self.brain_spoke = self.brain_spoke or from_brain
        self.q.put_nowait(text)

    async def _run(self) -> None:
        while True:
            s = await self.q.get()
            if s is None:
                return
            async for pcm in self.voice.tts.speak(s):
                if self.t_first_audio is None:
                    self.t_first_audio = self.voice.clock()
                    await self.voice._set_state("speak")
                await self.page.bytes(wire.pack_down(self.turn, self.seq, pcm))
                self.seq += 1
                self.audio_bytes += len(pcm)

    async def finish(self) -> float:
        """Wait until everything said has been sent; the audio's length in ms."""
        self.q.put_nowait(None)
        await self.task
        return self.audio_bytes / 2 / wire.DOWN_RATE * 1000

    def cancel(self) -> None:
        self.task.cancel()
