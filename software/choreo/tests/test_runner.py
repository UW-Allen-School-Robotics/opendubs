#!/usr/bin/env python3
"""Checks on the show runner's core. No robot, no network, no waiting: the clock is simulated, and
the fake robot state is changed mid-show by the simulated sleep.

    python3 tests/test_runner.py
"""
import io
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import runner as R  # noqa: E402

SHOWS = os.path.join(HERE, "..", "shows")
LIMITS = os.path.join(HERE, "joint_limits_fixture.yaml")
fails = []


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'}  {name}")
    if not cond:
        fails.append(f"{name}: {detail}")


class SimClock:
    """A clock that only moves when the runner sleeps — and can flip the robot's state at a time."""

    def __init__(self, robot=None, at=None, change=None):
        self.now, self.robot, self.at, self.change, self.fired = 1000.0, robot, at, change, False

    def clock(self):
        return self.now

    def sleep(self, s):
        self.now += s
        if self.at is not None and not self.fired and self.now >= self.at:
            self.fired = True
            self.robot.set(**self.change)


def run(show, robot=None, flip_after=None, change=None, stop_after=None):
    robot = robot or R.FakeRobotState()
    sc = SimClock(robot, None if flip_after is None else 1000.0 + flip_after, change)
    log = io.StringIO()
    r = R.Runner(os.path.join(SHOWS, show) if not os.path.isabs(show) else show, LIMITS, robot,
                 log=log, clock=sc.clock, sleep=sc.sleep, start_lead=0.5)
    if stop_after is not None:
        orig = sc.sleep

        def sleep(s):
            orig(s)
            if sc.now >= 1000.0 + stop_after:
                r.request_stop()
        r.sleep = sleep
    end = r.run()
    lines = [json.loads(x) for x in log.getvalue().splitlines()]
    return r, end, lines


print("runner: a show from start to finish")
r, end, lines = run("hello.yaml")
statuses = [x["status"] for x in lines if x["kind"] == "status"]
check("a valid show runs VALIDATING -> PREFLIGHT -> PREPPING -> WAITING -> RUNNING -> COMPLETED",
      statuses == ["VALIDATING", "PREFLIGHT", "PREPPING", "WAITING_FOR_START", "RUNNING", "COMPLETED"],
      statuses)
check("every cue is dispatched, in time order",
      len(r.sent) == 54 and [x[0] for x in r.sent] == sorted(x[0] for x in r.sent), len(r.sent))
check("the twin feed ends not playing, marked COMPLETED",
      r.feed.twin()["status"] == "COMPLETED" and not r.feed.twin()["playing"])

print("\nrunner: refusing before anything moves")
with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, dir=SHOWS) as fh:
    fh.write(open(os.path.join(SHOWS, "hello.yaml")).read().replace("rc_authority: AUTO", "rc_authority: AUOT"))
try:
    r, end, _ = run(fh.name)
    check("an invalid show is refused at VALIDATING, and nothing is sent",
          end[0] == "REFUSED" and end[1] == "INVALID_SHOW" and not r.sent, end)
finally:
    os.unlink(fh.name)
r, end, _ = run("hello.yaml", R.FakeRobotState(deadman=None))
check("an UNKNOWN deadman refuses — unknown never defaults",
      end[0] == "REFUSED" and end[1] == "UNKNOWN_STATE" and "deadman" in end[2], end)
r, end, _ = run("hello.yaml", R.FakeRobotState(ch5="MANUAL"))
check("CH5 not in the show's required mode refuses", end[0] == "REFUSED" and "CH5 must be AUTO" in end[2], end)
r, end, _ = run("hello.yaml", R.FakeRobotState(arms="inactive"))
check("arms not active refuses", end[0] == "REFUSED" and "arms" in end[2], end)

print("\nrunner: ending a show")
r, end, lines = run("hello.yaml", flip_after=10.0, change={"estop": True})
check("an e-stop mid-show ends it as ESTOPPED, at once", end[0] == "ESTOPPED" and end[3] is not None, end)
check("... and the log says audio was cut, not faded", any(x["kind"] == "estop" for x in lines))
r, end, lines = run("hello.yaml", flip_after=10.0, change={"deadman": False})
check("releasing the deadman ABORTS (it never pauses)", end[0] == "ABORTED" and end[1] == "DEADMAN", end)
check("... and every slot's abort state is logged, argued per slot",
      any(x["kind"] == "abort_state" and "LIFT" in x["slots"] and "ARM_L" in x["slots"] for x in lines))
sent_before = len(r.sent)
check("... and no cue after the abort is dispatched", all(t <= end[3] for t, _, _ in r.sent), sent_before)
r, end, _ = run("hello.yaml", flip_after=10.0, change={"ch5": "MANUAL"})
check("CH5 leaving AUTO mid-show aborts", end[0] == "ABORTED" and end[1] == "AUTHORITY", end)
r, end, _ = run("hello.yaml", flip_after=10.0, change={"deadman": None})
check("the deadman becoming UNKNOWN mid-show aborts", end[0] == "ABORTED" and "unknown" in end[2], end)
r, end, _ = run("hello.yaml", stop_after=12.0)
check("an operator stop is an abort", end[0] == "ABORTED" and end[1] == "OPERATOR_STOP", end)

print("\nrunner: the start pose and the log")
tl = R.cue.load_show(os.path.join(SHOWS, "hello.yaml")).export(R.cue.load_limits(LIMITS), LIMITS)
secs, targets = R.prep_moves(tl, {"ARM_L": [10, 0, 0, 40, 0, 0, 0], "ARM_R": [0, -15, 0, 30, 0, 0, 0]})
check("the start-pose move goes to ZERO — where the validator and the twin start every arm",
      set(targets) == {"ARM_L", "ARM_R"} and all(v == 0.0 for t in targets.values() for v in t), targets)
check("... timed by distance at the caps, slowly enough not to warn",
      secs >= max(40 / R.cue.SPEED_CAP_DEG_S[4], 15 / R.cue.SPEED_CAP_DEG_S[2]) * R.cue.PEAK_FACTOR - 0.01, secs)
secs0, _ = R.prep_moves(tl, {"ARM_L": [0.0] * 7, "ARM_R": {}})
check("... and is instant when the arms are already there", secs0 == 0.0, secs0)
one = dict(tl, tracks={k: v for k, v in tl["tracks"].items() if k != "ARM_L"})
secs1, targets1 = R.prep_moves(one, {"ARM_L": [0, -10, 0, 20, 0, 0, 0], "ARM_R": [0.0] * 7})
check("... and homes EVERY arm, even one the show does not move: a one-arm run after a two-arm abort",
      set(targets1) == {"ARM_L", "ARM_R"} and secs1 > 0, (secs1, targets1))
r, end, lines = run("hello.yaml", flip_after=10.0, change={"deadman": False})
last = [x for x in lines if x["kind"] == "status"][-1]
check("an abort's cause can be reconstructed from the log alone (5.5)",
      last["status"] == "ABORTED" and last["code"] == "DEADMAN" and last["show_t"] is not None, last)

print("\nrunner: the routine palette (1.13 / 4.4b)")
import threading  # noqa: E402
gate = threading.Event()
sc = SimClock()
held = {"on": False}


def gated_sleep(dt):
    if held["on"]:
        gate.wait(5)
    sc.sleep(dt)


robot = R.FakeRobotState()
pal = R.Palette(SHOWS, LIMITS, robot, R.TwinFeed(), io.StringIO(), start_lead=0.5,
                clock=sc.clock, sleep=gated_sleep)
ids = [x["id"] for x in pal.routines()]
check("the palette lists the shows marked palette:, in their order", ids[:3] == ["reach", "celebrate", "shrug"], ids)
check("... and only those", "hello" not in ids and "beat-demo" not in ids, ids)
held["on"] = True
pal.run("reach")
try:
    pal.run("celebrate")
    check("a second routine is refused while one runs", False, "accepted")
except RuntimeError as e:
    check("a second routine is refused while one runs", "still running" in str(e), str(e))
held["on"] = False
gate.set()
pal.current[2].join(5)
check("the routine ran to completion", pal.status()["last"]["status"] == "COMPLETED", pal.status())
check("the fake robot's arms are left at rest, so the next routine starts from there",
      len(robot.read()["arm_pose"]["ARM_R"]) == 7 and robot.read()["arm_pose"]["ARM_R"][3] > 0, robot.read()["arm_pose"])
for nxt in ("shrug", "celebrate"):
    pal.run(nxt)
    pal.current[2].join(5)
    check(f"then {nxt} runs after it, in any order", pal.status()["last"] == {**pal.status()["last"], "id": nxt, "status": "COMPLETED"},
          pal.status()["last"])
gate.clear()
held["on"] = True
pal.run("reach")
check("stop aborts the running routine", pal.stop())
held["on"] = False
gate.set()
pal.current[2].join(5)
check("... and it ends ABORTED, by the operator", pal.status()["last"]["code"] == "OPERATOR_STOP", pal.status()["last"])
try:
    pal.run("hello")
    check("a show that is not on the palette cannot be run from it", False)
except KeyError:
    check("a show that is not on the palette cannot be run from it", True)

print("\nrunner: show audio (4.2)")
import wave  # noqa: E402


def _wav(path, secs=1.0):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with wave.open(path, "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(8000)
        w.writeframes(b"\0\0" * int(8000 * secs))


_ad = tempfile.mkdtemp()
_wav(os.path.join(_ad, "audio", "song.wav"), 30)
_wav(os.path.join(_ad, "sounds", "ding.wav"), 0.5)
AUDIO_SHOW = os.path.join(_ad, "a.yaml")
with open(AUDIO_SHOW, "w") as fh:
    fh.write("id: a\nslots: [SOUND]\nrequires: {}\n"
             "music: { file: audio/song.wav, bpm: 120, offset_s: 0.0, beats_per_bar: 4 }\n"
             "cues:\n  - { t: 2.0, slot: SOUND, do: play, sound: ding }\n"
             "  - { t: 3.0, slot: SOUND, do: play, sound: attend }\n"
             "  - { t: 6.0, slot: SOUND, do: play, sound: heard }\n")


def run_audio(audio, **kw):
    robot = R.FakeRobotState()
    sc = SimClock(robot, None if kw.get("flip_after") is None else 1000.0 + kw["flip_after"], kw.get("change"))
    log = io.StringIO()
    r = R.Runner(AUDIO_SHOW, LIMITS, robot, log=log, clock=sc.clock, sleep=sc.sleep, start_lead=0.5, audio=audio)
    end = r.run()
    return r, end, [json.loads(x) for x in log.getvalue().splitlines()]


au = R.NullAudio(holder="tablet")
r, end, lines = run_audio(au)
ops = [c["op"] for c in au.calls]
check("with a device holding show audio: load, then start, then a fade when the show ends",
      end[0] == "COMPLETED" and ops == ["load", "start", "stop"] and au.calls[-1]["fade_s"] == R.AUDIO_FADE_S, au.calls)
items = au.calls[0]["items"]
check("... the song plays from show time 0, and a tablet sound file at its cue time",
      {(i["kind"], i["url"], i["at"]) for i in items} ==
      {("music", "/api/choreo/media/audio/song.wav", 0.0), ("sound", "/api/choreo/media/sounds/ding.wav", 2.0)}, items)
check("... earcons are not sent to it (they play on the head)", not any("attend" in i["url"] for i in items))
check("... and the start names one future Thor time, the show's time 0",
      au.calls[1]["t0_mono"] > 1000.0 and au.calls[1]["id"] == au.calls[0]["id"], au.calls[1])

au = R.NullAudio(holder="tablet")
r, end, _ = run_audio(au, flip_after=4.0, change={"deadman": False})
check("an abort fades the music (DEC-6)", end[0] == "ABORTED" and au.calls[-1] == {"op": "stop", "fade_s": R.AUDIO_FADE_S},
      au.calls[-1:])
au = R.NullAudio(holder="tablet")
r, end, _ = run_audio(au, flip_after=4.0, change={"estop": True})
check("an e-stop cuts it (DEC-6)", end[0] == "ESTOPPED" and au.calls[-1] == {"op": "cut"}, au.calls[-1:])

au = R.NullAudio(holder=None)
r, end, lines = run_audio(au)
check("with no device holding show audio the routine still runs — silently, with a warning (decided)",
      end[0] == "COMPLETED" and not au.calls and "running silently" in (r.audio_note or "")
      and any(x["kind"] == "audio_warning" for x in lines), (end, au.calls, r.audio_note))

au = R.NullAudio(holder="tablet", ready=False)
r, end, lines = run_audio(au)
check("a device that never finishes loading is cut and the routine runs silently, after a bounded wait",
      end[0] == "COMPLETED" and [c["op"] for c in au.calls] == ["load", "cut"]
      and "not ready" in (r.audio_note or ""), (au.calls, r.audio_note))


class Broken:
    def send(self, msg):
        raise OSError("dashboard gone")

    def state(self):
        return {"holder": "tablet", "ready": True}


r, end, lines = run_audio(Broken())
check("audio trouble never stops a show: a failing device is logged, the show completes (DEC-7)",
      end[0] == "COMPLETED" and any(x["kind"] == "audio_error" for x in lines), end)

print("\nrunner: LIVE arm output (5.6)")
import arm_output as AO  # noqa: E402
tlr = R.cue.load_show(os.path.join(SHOWS, "reach.yaml")).export(R.cue.load_limits(LIMITS), LIMITS)
pts = AO.track_points(tlr["tracks"]["ARM_R"])
by_t = dict(pts)
check("an arm's path is dense — a point every 0.1 s — to the routine's last keyframe",
      len(pts) > 100 and abs(pts[1][0] - pts[0][0] - AO.DENSE_S) < 1e-6 and pts[-1][0] == 12.0, (len(pts), pts[:2], pts[-1]))
check("... a straight line in joint space from zero: halfway to the first keyframe at half its time",
      by_t[2.5][0] == 10.0 and by_t[2.5][3] == 35.0 and by_t[5.0][3] == 70.0, (by_t.get(2.5), by_t.get(5.0)))
check("... and it ends at the documented rest", by_t[12.0][3] == round(tlr["tracks"]["ARM_R"][-1]["pose_signed"]["4"], 4))
left = R.cue.show_from_text("id: l\nslots: [ARM_L]\nrequires: {arms: active, rc_authority: AUTO}\n"
                            "cues:\n  - { t: 4.0, slot: ARM_L, joints: { 1: 20, 4: 40 } }\n").export(R.cue.load_limits(LIMITS), LIMITS)
lp = AO.track_points(left["tracks"]["ARM_L"])
check("the LEFT arm goes out in the controller's own sense: its mirrored joint 1 flipped, joint 4 not",
      lp[-1][1][0] == -20.0 and lp[-1][1][3] == 40.0, lp[-1])


def run_live(arms, live=True, **kw):
    robot = R.FakeRobotState()
    sc = SimClock(robot, None if kw.get("flip_after") is None else 1000.0 + kw["flip_after"], kw.get("change"))
    log = io.StringIO()
    r = R.Runner(os.path.join(SHOWS, "hello.yaml"), LIMITS, robot, log=log, clock=sc.clock, sleep=sc.sleep,
                 start_lead=0.5, arms=arms, live=live)
    end = r.run()
    return r, end, [json.loads(x) for x in log.getvalue().splitlines()]


arms = AO.FakeArms({"ARM_L": [0, 0, 0, 30, 0, 0, 0], "ARM_R": [5, 0, 0, 0, 0, 0, 0]})
r, end, lines = run_live(arms)
slots = [s for s, _ in arms.sent]
check("a LIVE run sends each arm a start-pose move from where it MEASURES it, then its whole routine",
      end[0] == "COMPLETED" and slots == ["ARM_L", "ARM_R", "ARM_L", "ARM_R"], (end, slots))
check("... the start-pose move ends at zero, from the measured pose",
      arms.sent[0][1][-1][1] == [0.0] * 7 and arms.sent[0][1][0][1][3] < 30, arms.sent[0][1][:1])
check("... and a completed routine is not cancelled: it ends where the routine ends", arms.cancelled == 0)
arms = AO.FakeArms()
r, end, lines = run_live(arms, flip_after=10.0, change={"deadman": False})
check("releasing the deadman during a LIVE run cancels the arm goals — they hold where they are",
      end[0] == "ABORTED" and arms.cancelled == 1 and any(x["kind"] == "live" and "cancelled" in x.get("note", "") for x in lines),
      (end, arms.cancelled))
arms = AO.FakeArms()
r, end, lines = run_live(arms, flip_after=10.0, change={"estop": True})
check("an e-stop during a LIVE run ends it at once (the contactor has cut the arms' power)", end[0] == "ESTOPPED")
arms = AO.FakeArms(measured=None)
arms._measured = None
r, end, _ = run_live(arms)
check("a LIVE run with the arms' position unknown is refused before anything moves",
      end[0] == "REFUSED" and "measured position is unknown" in end[2] and not arms.sent, end)
arms = AO.FakeArms()
r, end, _ = run_live(arms, live=False)
check("without the LIVE mark, a runner that has arms sends them nothing", end[0] == "COMPLETED" and not arms.sent)
r, end, _ = run_live(None, live=True)
check("LIVE asked of a runner with no arm output runs twin-only", end[0] == "COMPLETED" and r.live is False)
pal2 = R.Palette(SHOWS, LIMITS, R.FakeRobotState(), R.TwinFeed(), io.StringIO())
check("a palette with no arms runs twin-only, and says live output is not available",
      pal2.run("reach")["live"] is False and pal2.status()["live_available"] is False)
pal3 = R.Palette(SHOWS, LIMITS, R.FakeRobotState(), R.TwinFeed(), io.StringIO(), arms=AO.FakeArms())
check("a palette WITH arms makes every run live: CH5 AUTO and the deadman are the go, no second switch",
      pal3.run("reach")["live"] is True and pal3.status()["live_available"] is True)
uses = {r["id"]: {u["part"]: u["live"] for u in r["uses"]} for r in pal3.routines()}
check("each routine says what it will do: Wave and nod plays music and moves the arms for real, "
      "the head and face in the twin only",
      uses.get("wave-and-nod") == {"music": True, "arms": True, "head": False, "face": False}, uses.get("wave-and-nod"))
check("... grippers stay twin-only until calibrated (6.2)", uses["reach"].get("grippers") is False, uses["reach"])
print("\nrunner: the real face (5.6, face half)")
import face_bridge as FB  # noqa: E402


class FakeSock:
    def __init__(self):
        self.lines = []

    def sendto(self, data, addr):
        self.lines.append(data.decode().strip())


def run_face(show, flip_after=None, change=None):
    robot, sock = R.FakeRobotState(), FakeSock()
    face = FB.LiveFace("127.0.0.1", sock=sock)
    sc = SimClock(robot, None if flip_after is None else 1000.0 + flip_after, change)
    r = R.Runner(os.path.join(SHOWS, show), LIMITS, robot, FB.HeadTee(R.TwinFeed(), face), io.StringIO(),
                 clock=sc.clock, sleep=sc.sleep, start_lead=0.5, arms=AO.FakeArms(), live=True)
    return r.run(), sock.lines


end, lines = run_face("celebrate.yaml")
check("a live run sends the face its cues and the head its earcons, then hands the face back",
      end[0] == "COMPLETED" and lines[:2] == ["EMOTE SOFT", "CUE attend"] and lines[-1] == "EMOTE AUTO", lines)
end, lines = run_face("celebrate.yaml", flip_after=2.0, change={"deadman": False})
check("... an aborted run hands the face back too", end[0] == "ABORTED" and lines[-1] == "EMOTE AUTO", lines)
end, lines = run_face("celebrate.yaml", flip_after=0.0, change={"deadman": False})
check("... and a run that never starts leaves the face alone", end[0] in ("REFUSED", "ABORTED") and lines == [], (end, lines))


class FakeHead(FakeSock):
    """Answers like the head: GAZE echoes the CLAMPED angles; an old head knows no GAZE."""

    def __init__(self, tilt_band=(0, 180), knows_gaze=True):
        super().__init__()
        self.band, self.knows, self.out = tilt_band, knows_gaze, []

    def sendto(self, data, addr):
        line = data.decode().strip()
        self.lines.append(line)
        if not line.startswith("GAZE"):
            return
        if not self.knows:
            self.out.append("ERR unknown")
        elif line == "GAZE OFF":
            self.out.append("OK gaze off")
        else:
            p, t = (float(x) for x in line.split()[1:3])
            self.out.append(f"OK gaze {p:.0f} {min(max(t, self.band[0]), self.band[1]):.0f}")

    def recv(self, n):
        if not self.out:
            raise BlockingIOError
        return self.out.pop(0).encode()


def run_gaze(show, head, flip_after=None, change=None):
    robot, notes = R.FakeRobotState(), []
    sc = SimClock(robot, None if flip_after is None else 1000.0 + flip_after, change)
    gaze = FB.LiveGaze("127.0.0.1", sock=head, clock=sc.clock, note=lambda **kw: notes.append(kw))
    r = R.Runner(os.path.join(SHOWS, show), LIMITS, robot, FB.HeadTee(R.TwinFeed(), gaze), io.StringIO(),
                 clock=sc.clock, sleep=sc.sleep, start_lead=0.5, arms=AO.FakeArms(), live=True)
    return r.run(), head.lines, notes


end, lines, notes = run_gaze("reach.yaml", FakeHead())
gz = [ln for ln in lines if ln.startswith("GAZE")]
check("a live run turns the real head: the HEAD track's target, as GAZE with a lease, renewed while it runs",
      end[0] == "COMPLETED" and gz[0] == "GAZE 110 80 600" and "GAZE 90 90 600" in gz
      and len(gz) >= 12 / FB.LiveGaze.RENEW_S - 5, (len(gz), gz[:3]))
check("... and every ending releases it: the head stops where it is and goes back to its own behaviour",
      gz[-1] == "GAZE OFF" and not [n for n in notes if "warning" in n.get("kind", "")], (gz[-2:], notes))
end, lines, notes = run_gaze("reach.yaml", FakeHead(), flip_after=3.0, change={"deadman": False})
check("... an aborted run releases it too", end[0] == "ABORTED" and lines[-1] == "GAZE OFF", lines[-2:])
end, lines, notes = run_gaze("robotic-smile.yaml", FakeHead(tilt_band=(85, 95)))
warn = [n for n in notes if n.get("kind") == "gaze_warning"]
check("a move the head limits to its own range is logged ONCE, with what was asked and what it took",
      len(warn) == 1 and warn[0]["asked"][1] != warn[0]["got"][1], warn)
end, lines, notes = run_gaze("reach.yaml", FakeHead(knows_gaze=False))
gz = [ln for ln in lines if ln.startswith("GAZE")]
check("a head without GAZE (older firmware) is asked once, logged once, and the show plays on",
      end[0] == "COMPLETED" and len(gz) <= 2 and "GAZE OFF" not in gz
      and len([n for n in notes if n.get("kind") == "gaze_warning"]) == 1, (gz, notes))
old = FB.LiveGaze("127.0.0.1", sock=FakeHead(knows_gaze=False))
new = FB.LiveGaze("127.0.0.1", sock=FakeHead())
check("at runner start a probe learns whether the head has GAZE (it releases any stale lease on the way)",
      old.probe(0.2) is False and new.probe(0.2) is True and new.sock.lines == ["GAZE OFF"])
palg = R.Palette(SHOWS, LIMITS, R.FakeRobotState(), R.TwinFeed(), io.StringIO(), arms=AO.FakeArms(), gaze=new)
palo = R.Palette(SHOWS, LIMITS, R.FakeRobotState(), R.TwinFeed(), io.StringIO(), arms=AO.FakeArms(), gaze=old)
head_live = lambda p: {r["id"]: {u["part"]: u["live"] for u in r["uses"]} for r in p.routines()}["reach"]["head"]
check("... and the head's icon is real only when it has", head_live(palg) is True and head_live(palo) is False)

sock, notes = FakeSock(), []
face = FB.LiveFace("127.0.0.1", sock=sock, note=lambda **kw: notes.append(kw), earcons=False)
robot = R.FakeRobotState()
sc = SimClock(robot)
end = R.Runner(os.path.join(SHOWS, "celebrate.yaml"), LIMITS, robot, FB.HeadTee(R.TwinFeed(), face), io.StringIO(),
               clock=sc.clock, sleep=sc.sleep, start_lead=0.5, arms=AO.FakeArms(), live=True).run()
check("with earcons held back the face still plays but no CUE reaches the head, and the log says so once",
      end[0] == "COMPLETED" and "EMOTE SOFT" in sock.lines and not [ln for ln in sock.lines if ln.startswith("CUE")]
      and len([n for n in notes if "held back" in n.get("line", "")]) == 1, (sock.lines, notes))
pale = R.Palette(SHOWS, LIMITS, R.FakeRobotState(), R.TwinFeed(), io.StringIO(), arms=AO.FakeArms(),
                 face=FB.LiveFace("127.0.0.1", sock=FakeSock(), earcons=False))
usese = {r["id"]: {u["part"]: u["live"] for u in r["uses"]} for r in pale.routines()}
check("... and the routine's SOUND icon is faded", usese["celebrate"]["sound"] is False and usese["celebrate"]["face"] is True,
      usese["celebrate"])
pal4 = R.Palette(SHOWS, LIMITS, R.FakeRobotState(), R.TwinFeed(), io.StringIO(), arms=AO.FakeArms(),
                 face=FB.LiveFace("127.0.0.1", sock=FakeSock()))
uses4 = {r["id"]: {u["part"]: u["live"] for u in r["uses"]} for r in pal4.routines()}
check("with the face, its icons and the head's earcons are real; pan/tilt stays twin-only",
      uses4["celebrate"] == {"arms": True, "head": False, "face": True, "sound": True}, uses4["celebrate"])
uses2 = {r["id"]: {u["part"]: u["live"] for u in r["uses"]} for r in pal2.routines()}
check("... and with no arms on this runner, the arms are twin-only too", uses2["celebrate"]["arms"] is False, uses2["celebrate"])

print("\nrunner: the grippers, live (plan item 3)")
import grippers as G  # noqa: E402
CAL = G.load(os.path.join(HERE, "gripper_calibration_fixture.yaml"))


def run_grips(show_text_cues, grips=CAL, results=None, flip_after=None, change=None):
    robot = R.FakeRobotState()
    sc = SimClock(robot, None if flip_after is None else 1000.0 + flip_after, change)
    log = io.StringIO()
    tmp = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
    tmp.write("id: g\nslots: [ARM_R, GRIP_R]\nrequires: {arms: active, rc_authority: AUTO}\ncues:\n"
              + "".join(f"  - {c}\n" for c in show_text_cues))
    tmp.close()
    arms = AO.FakeArms()
    if results is not None:
        orig = arms.send_grip

        def send_grip(slot, m):
            orig(slot, m)
            arms.results[slot] = results.pop(0) if results else 4
        arms.send_grip = send_grip
    r = R.Runner(tmp.name, LIMITS, robot, log=log, clock=sc.clock, sleep=sc.sleep, start_lead=0.5,
                 arms=arms, live=True, grips=grips)
    end = r.run()
    return end, arms, [json.loads(x) for x in log.getvalue().splitlines()]


cues = ["{ t: 1.0, slot: GRIP_R, do: open }", "{ t: 2.0, slot: GRIP_R, open: 0.5 }",
        "{ t: 3.0, slot: GRIP_R, do: close }", "{ t: 4.0, slot: ARM_R, pose: rest }"]
end, arms, lines = run_grips(cues)
check("a live run sends each gripper cue through this robot's calibration, at its time",
      end[0] == "COMPLETED" and arms.grips == [("GRIP_R", 0.04), ("GRIP_R", 0.02), ("GRIP_R", 0.0)], arms.grips)
check("... and `close` is EXACTLY the calibrated closed position, never past it",
      arms.grips[-1][1] == CAL["right"][0], arms.grips[-1])
end, arms, lines = run_grips(cues, grips=None)
check("with no calibration on the robot, no gripper cue is sent, and the log says why",
      end[0] == "COMPLETED" and not arms.grips and any("no gripper calibration" in x.get("note", "") for x in lines))
cues2 = ["{ t: 1.0, slot: GRIP_R, do: close }", "{ t: 2.0, slot: GRIP_R, do: open }",
         "{ t: 3.0, slot: GRIP_R, do: close }", "{ t: 4.0, slot: ARM_R, pose: rest }"]
end, arms, lines = run_grips(cues2, results=[6, 4, 4])          # the first close STALLS
check("a close that STALLED (something in the gripper) is never re-sent that run; an open still is",
      arms.grips == [("GRIP_R", 0.0), ("GRIP_R", 0.04)]
      and any("STALLED" in x.get("note", "") for x in lines), (arms.grips, [x.get("note") for x in lines if x.get("note")]))
end, arms, lines = run_grips(["{ t: 1.0, slot: GRIP_R, do: open }", "{ t: 6.0, slot: GRIP_R, do: close }"],
                             flip_after=3.0, change={"deadman": False})
check("releasing the deadman cancels the gripper goals too, even in a run that moved no arm — they hold",
      end[0] == "ABORTED" and arms.cancelled == 1 and arms.grips == [("GRIP_R", 0.04)], (end, arms.cancelled, arms.grips))
os.environ["OPENDUBS_GRIPPER_CAL"] = os.path.join(HERE, "gripper_calibration_fixture.yaml")
palg = R.Palette(SHOWS, LIMITS, R.FakeRobotState(), R.TwinFeed(), io.StringIO(), arms=AO.FakeArms())
check("with a calibration, Reach's GRIPPERS icon is real",
      {u["part"]: u["live"] for u in next(r for r in palg.routines() if r["id"] == "reach")["uses"]}["grippers"] is True)
os.environ["OPENDUBS_GRIPPER_CAL"] = os.path.join(HERE, "no-such-calibration.yaml")
check("... and without one it stays twin-only",
      {u["part"]: u["live"] for u in next(r for r in palg.routines() if r["id"] == "reach")["uses"]}["grippers"] is False)
del os.environ["OPENDUBS_GRIPPER_CAL"]

print("\nrunner: on the robot (4.4c)")
missing = R.Palette(SHOWS, "/nonexistent/joint_limits.yaml", R.FakeRobotState(), R.TwinFeed(), io.StringIO())
rs = missing.routines()
check("with no limits file every routine is still listed, refused, and says why",
      rs and all(not x["valid"] and "joint limits cannot be read" in x["problems"][0] for x in rs), rs[:1])
try:
    missing.run(rs[0]["id"])
    check("... and none can be run", False, "accepted")
except ValueError as e:
    check("... and none can be run", "joint limits" in str(e), str(e))

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer  # noqa: E402
import time  # noqa: E402
asked = []
answer = {"ch5": "AUTO", "deadman": True, "estop": False, "fault": None, "arms": "active",
          "tab5": True, "arm_pose": {"ARM_L": {}, "ARM_R": {}}}


class Dash(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        asked.append(self.path)
        b = json.dumps(answer).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


dash = ThreadingHTTPServer(("127.0.0.1", 0), Dash)
threading.Thread(target=dash.serve_forever, daemon=True).start()
real = R.DashboardRobotState(f"http://127.0.0.1:{dash.server_address[1]}/api/choreo/robot")
time.sleep(0.4)
check("the dashboard reader reports what the dashboard says", real.read()["ch5"] == "AUTO", real.read())
real.refresh()
check("... and asks the arms and head afresh only when told to (preflight)",
      "/api/choreo/robot?probe=1" in asked and asked.count("/api/choreo/robot?probe=1") == 1, asked[-3:])
dash.shutdown()
time.sleep(0.7)
check("a dashboard that stops answering makes everything UNKNOWN within half a second",
      all(real.read()[k] is None for k in ("ch5", "deadman", "estop")), real.read())
check("... so a show would end, not carry on", R.monitor({}, real.read())[0] == R.ESTOPPED)

srv = R.serve(R.TwinFeed(), real, {}, "127.0.0.1", 0)
port = srv.server_address[1]
import urllib.error  # noqa: E402
import urllib.request  # noqa: E402
try:
    urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/api/sim/estop", data=b"", method="POST"))
    check("the real robot's state has no switches: /api/sim/* is not served", False, "served")
except urllib.error.HTTPError as e:
    check("the real robot's state has no switches: /api/sim/* is not served", e.code == 404, e.code)
srv.shutdown()

print()
if fails:
    for f in fails:
        print("FAILED:", f)
    sys.exit(1)
print("all good")
