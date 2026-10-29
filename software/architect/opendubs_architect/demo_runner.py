"""The ARCHITECT runner for the Halloween demo: a spoken instruction becomes an approved, frozen
program, and every "trick or treat" runs it again.

    python3 -m opendubs_architect.demo_runner --agent program --program handout.py --dry-run   # a desk
    python3 -m opendubs_architect.demo_runner --agent ssa                                       # the robot

STATES (the design doc's interaction flow):

    IDLE          an instruction                         -> GENERATING
    GENERATING    a program passes the desk check        -> AWAITING_YES, its summary read out
    AWAITING_YES  "yes"                                  -> ARMED, the program frozen by its hash
                  "no", or silence for APPROVAL_TIMEOUT_S -> IDLE
    ARMED         "trick or treat" with the deadman held  -> RUNNING
    RUNNING       done, refused, or the deadman released  -> ARMED
                  a check fails                          -> CHECK_FAILED, the robot says why
                                                            (ARMED again with a fixed program, --agent
                                                            program: a correction cannot change it)
    CHECK_FAILED  a spoken correction                    -> GENERATING
    any state     "stop"                                 -> IDLE

WHAT REACHES THE MODEL. Only an instruction while IDLE and a correction while CHECK_FAILED. While
ARMED or RUNNING nothing anyone says reaches it: "trick or treat" starts a run, "stop" disarms, and
everything else is ignored. So a child's words never become a prompt, and to give a new
instruction the supervisor says "stop" first.

WHAT RUNS. The approved text, checked against its hash before every run, in a namespace holding
the robot's primitives and nothing else. Library helpers the program calls are copied into it
before approval (Agent.bundle), so the supervisor approves every line that will run. Before a
program is offered for approval it must pass check_program: it compiles, names only primitives,
builtins and what it defines itself, imports nothing beyond ALLOWED_IMPORTS, and catches no
RuntimeError (a refusal must stop it). It must also run once, start to end, on the dry-run robot.

"Stop" ends a run at its next primitive call; the deadman, which the robot's own services check,
is what stops a motion already under way.
"""

from __future__ import annotations

import argparse
import ast
import builtins
import hashlib
import json
import re
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field

from opendubs_architect import api
from opendubs_architect.client import MotionRefused

IDLE, GENERATING, AWAITING_YES, ARMED, RUNNING, CHECK_FAILED = (
    "IDLE", "GENERATING", "AWAITING_YES", "ARMED", "RUNNING", "CHECK_FAILED")
APPROVAL_TIMEOUT_S = 60.0
MAX_REPAIRS = 2                 # desk-check failures sent back to the model before giving up
ALLOWED_IMPORTS = frozenset({"math", "time"})
FORBIDDEN_CATCH = frozenset({"Exception", "BaseException", "RuntimeError"})
SAID_MAX = 120                  # what the robot says about a failure, in characters

_YES = re.compile(r"\b(yes|yeah|yep)\b", re.I)
_NO = re.compile(r"\b(no|nope)\b", re.I)
_STOP = re.compile(r"\bstop\b", re.I)
_SUMMARY = re.compile(r"^\s*#\s*Summary:\s*(.+?)\s*$", re.I | re.M)


class Stopped(Exception):
    """The supervisor said "stop" during a run."""


@dataclass
class Program:
    code: str
    summary: str
    sha: str = field(init=False)

    def __post_init__(self):
        self.sha = sha(self.code)


def sha(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()[:12]


def summary_of(code: str) -> str:
    """The program's `# Summary:` line, read aloud for approval."""
    m = _SUMMARY.search(code)
    return m.group(1)[:SAID_MAX] if m else "I have a program ready."


def check_program(code: str, primitives=api.PRIMITIVE_NAMES) -> None:
    """Raise ValueError naming what is wrong with a program, before anyone is asked to approve it.
    `primitives`: what the session's profile offers (api.PROFILES)."""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        raise ValueError(f"does not compile: {e}") from None
    defined, used = set(), {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            for mod in mods:
                if mod.split(".")[0] not in ALLOWED_IMPORTS:
                    raise ValueError(f"imports {mod!r}; only {', '.join(sorted(ALLOWED_IMPORTS))} may be imported")
            defined.update((a.asname or a.name).split(".")[0] for a in node.names)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
        elif isinstance(node, ast.arg):
            defined.add(node.arg)
        elif isinstance(node, ast.ExceptHandler):
            if node.type is None:
                raise ValueError(f"line {node.lineno}: a bare except would swallow MotionRefused")
            caught = node.type.elts if isinstance(node.type, ast.Tuple) else [node.type]
            for c in caught:
                if isinstance(c, ast.Name) and c.id in FORBIDDEN_CATCH | {"MotionRefused"}:
                    raise ValueError(f"line {node.lineno}: catching {c.id} would swallow MotionRefused")
            if node.name:
                defined.add(node.name)
        elif isinstance(node, ast.Name):
            if isinstance(node.ctx, (ast.Store, ast.Del)):
                defined.add(node.id)
            else:
                used.setdefault(node.id, node.lineno)
    known = defined | set(primitives) | set(dir(builtins))
    unknown = sorted((n, line) for n, line in used.items() if n not in known)
    if unknown:
        raise ValueError("names nothing defines: " + ", ".join(f"{n} (line {line})" for n, line in unknown))


class _Quiet:
    """A console that keeps what the dry-run robot prints."""

    def __init__(self):
        self.lines = []

    def print(self, *args, **kwargs):
        self.lines.append(" ".join(str(a) for a in args))


def desk_check(code: str, primitives=api.PRIMITIVE_NAMES) -> None:
    """check_program, then one run on the dry-run robot. Raise ValueError with the reason."""
    from opendubs_architect.dryrun import DryRunRobot, namespace_for
    check_program(code, primitives)
    ns = {k: v for k, v in namespace_for(DryRunRobot(_Quiet())).items() if k in primitives}
    try:
        exec(compile(code, "<desk check>", "exec"), ns)  # noqa: S102  the dry-run robot moves nothing
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"failed on the dry-run robot: {type(e).__name__}: {e}") from None


def generation_namespace(namespace: dict) -> dict:
    """The robot's namespace with every motion primitive refusing: what the agent's own tools
    (scene description, robot state) may call while a program is being written."""
    def refuse(name):
        def f(*args, **kwargs):
            raise MotionRefused(f"{name}: no motion while a program is being written")
        f.__name__ = name
        return f
    return {k: (refuse(k) if k in api.MOTION_PRIMITIVES else v) for k, v in namespace.items()}


class ProgramAgent:
    """An agent that always offers the same program: for a desk, rehearsal, or the fallback when
    the model is unreachable. The supervisor still approves it by voice."""

    def __init__(self, code: str):
        self.code = code

    def generate(self, instruction: str) -> str | None:
        return self.code

    def correct(self, correction: str) -> str | None:
        return self.code

    def bundle(self, program: str) -> str:
        return program


class DemoRunner:
    """The state machine. Transport-free: `heard`, `trigger`, `stop` and `tick` come from the voice
    service and the dashboard page; `speak` and `deadman_held` reach the robot."""

    def __init__(self, agent, make_namespace, speak, deadman_held, clock=time.monotonic,
                 log=None, background=True, primitives=api.PRIMITIVE_NAMES, publish=None,
                 rearm_on_failure=False, after_run=None):
        """`publish`, when given, is called with each approved Program instead of arming it for
        "trick or treat": the show path, where the Shows page runs it (show_backend.publish). It
        returns what to report (a dict), or raises ValueError, and the program is not approved."""
        self.agent, self.make_namespace = agent, make_namespace
        self.primitives, self.publish = tuple(primitives), publish
        self.published = None                     # what publish returned for the approved program
        self.failed: Program | None = None        # a program whose show could not be made
        self.speak, self.deadman_held, self.clock = speak, deadman_held, clock
        self.background = background
        #: A failed run goes back to ARMED, not CHECK_FAILED: for a fixed program, which a spoken
        #: correction cannot change, so the next "trick or treat" simply tries again.
        self.rearm_on_failure = rearm_on_failure
        #: Called after every run, however it ended (the gafro backend releases the head's hold).
        self.after_run = after_run
        self.lock = threading.RLock()
        self.state = IDLE
        self.offered: Program | None = None       # awaiting approval
        self.armed: Program | None = None         # approved and frozen
        self.since = clock()
        self.runs, self.last = 0, None            # runs completed; the last run's outcome
        self.events: list = []
        self._gen = 0                             # bumped by stop: a late generation is dropped
        self._stop = threading.Event()
        self._log_fn = log

    # ---- bookkeeping -----------------------------------------------------------------------------
    def _log(self, what: str, **kw) -> None:
        ev = {"t": round(time.time(), 3), "state": self.state, "event": what, **kw}
        self.events = (self.events + [ev])[-200:]
        if self._log_fn:
            self._log_fn(ev)

    def _to(self, state: str, event: str, **kw) -> None:
        self._log(event, to=state, **kw)
        self.state, self.since = state, self.clock()

    def _say(self, text: str) -> None:
        try:
            self.speak(text[:api.SAY_MAX_CHARS])
        except Exception as e:  # noqa: BLE001  speech trouble never changes the state
            self._log("speak_failed", error=str(e))

    def _spawn(self, fn, *args) -> None:
        if self.background:
            threading.Thread(target=fn, args=args, daemon=True).start()
        else:
            fn(*args)

    # ---- inputs ----------------------------------------------------------------------------------
    def heard(self, kind: str, text: str = "") -> str:
        """Something the voice service heard. `kind`: "instruction" (spoken to the robot by its
        wake phrase), "speech" (anything else), "trigger" ("trick or treat"). Returns the state."""
        with self.lock:
            if _STOP.search(text or ""):
                self.stop("said stop")
            elif kind == "trigger":
                self.trigger("trick or treat")
            elif self.state == AWAITING_YES:
                if _NO.search(text):
                    self.offered = None
                    self._to(IDLE, "declined", text=text)
                    self._say("Okay, I won't do that.")
                elif _YES.search(text):
                    self._approve()
            elif kind == "instruction" and self.state == IDLE:
                self._generate(text, correction=False)
            elif self.state == CHECK_FAILED and text.strip():
                self._generate(text, correction=True)
            else:
                self._log("ignored", kind=kind, text=text)   # ARMED, RUNNING, GENERATING: not a prompt
            return self.state

    def trigger(self, source: str = "trick or treat") -> bool:
        """Start a run of the armed program, if one is armed and the deadman is held."""
        with self.lock:
            if self.state != ARMED:
                self._log("trigger_ignored", source=source)
                return False
            if self.publish is not None:
                self._log("trigger_ignored", source=source, why="the Shows page runs a published show")
                return False
            try:
                held = bool(self.deadman_held())
            except Exception as e:  # noqa: BLE001  unknown is not held
                held, why = False, str(e)
            else:
                why = "the deadman is not held"
            if not held:
                self._log("trigger_refused", source=source, why=why)
                return False
            program = self.armed
            if program is None or sha(program.code) != program.sha:
                self._to(IDLE, "hash_mismatch")
                self.armed = None
                return False
            self._stop.clear()
            self._to(RUNNING, "run_started", source=source, sha=program.sha)
            self._spawn(self._run, program)
            return True

    def cancel(self, why: str = "cancel") -> bool:
        """End the run under way at its next primitive call (the gafro backend also cancels a goal in
        flight), and keep the program ARMED: the demo day's pause. The state stays RUNNING until the run
        has ended, so no "trick or treat" can start a second one beside it. False if nothing was running."""
        with self.lock:
            if self.state != RUNNING:
                return False
            self._stop.set()
            self._log("cancel_requested", why=why)
            return True

    def stop(self, why: str = "stop") -> None:
        with self.lock:
            self._gen += 1
            self._stop.set()
            self.offered = self.armed = self.published = self.failed = None
            if self.state != IDLE:
                self._to(IDLE, "stopped", why=why)

    def tick(self) -> None:
        with self.lock:
            if self.state == AWAITING_YES and self.clock() - self.since > APPROVAL_TIMEOUT_S:
                self.offered = None
                self._to(IDLE, "approval_timed_out")
                self._say("I didn't hear a yes, so I won't do it.")

    # ---- generation ------------------------------------------------------------------------------
    def _generate(self, text: str, correction: bool) -> None:
        self._gen += 1
        self.armed = self.published = self.failed = None   # a new program needs its own yes
        self._to(GENERATING, "correction" if correction else "instruction", text=text)
        self._say("Let me think about that.")
        self._spawn(self._write, self._gen, text, correction)

    def _write(self, gen: int, text: str, correction: bool) -> None:
        context = ""
        if correction and self.last:
            context = f" (The last run failed: {self.last.get('error')})"
        try:
            code = (self.agent.correct(text + context) if correction else self.agent.generate(text))
            for attempt in range(MAX_REPAIRS + 1):
                if code is None:
                    break
                code = self.agent.bundle(code)
                try:
                    desk_check(code, self.primitives)
                    break
                except ValueError as e:
                    with self.lock:
                        if gen != self._gen:
                            return
                        self._log("desk_check_failed", error=str(e), attempt=attempt)
                    code = (self.agent.correct(f"The program failed a desk check and was not run: {e}. "
                                               "Fix it.") if attempt < MAX_REPAIRS else None)
        except Exception as e:  # noqa: BLE001
            code = None
            self._log("agent_error", error=f"{type(e).__name__}: {e}")
        with self.lock:
            if gen != self._gen:
                self._log("generation_dropped")
                return
            if code is None:
                self._to(IDLE, "generation_failed")
                self._say("I couldn't write a program for that.")
                return
            self.offered = Program(code, summary_of(code))
            self._to(AWAITING_YES, "program_offered", sha=self.offered.sha)
            self._say(f"{self.offered.summary} Shall I? Say yes to approve.")

    def _approve(self) -> None:
        if self.publish is not None:
            program, self.offered = self.offered, None
            self._to(GENERATING, "approved_publishing", sha=program.sha)
            self._spawn(self._publish, self._gen, program)
            return
        self.armed, self.offered = self.offered, None
        self._to(ARMED, "approved", sha=self.armed.sha)
        self._say("Approved. Say trick or treat.")

    def _publish(self, gen: int, program: Program) -> None:
        try:
            result, error = self.publish(program), None
        except Exception as e:  # noqa: BLE001  a refusal while recording is a failed check
            result, error = None, f"{type(e).__name__}: {e}"
        with self.lock:
            if gen != self._gen:
                self._log("publish_dropped", sha=program.sha)
                return
            if error:
                self.last = {"sha": program.sha, "result": "failed", "error": error}
                self.failed = program                # shown on the page, beside the failure
                self._to(CHECK_FAILED, "publish_failed", error=error)
                self._say("I couldn't make that into a show: " + error.split(": ", 1)[-1][:SAID_MAX])
                return
            self.armed, self.published = program, result
            self._to(ARMED, "published", sha=program.sha, **result)
            self._say("Approved. The show is ready for the Shows page.")

    # ---- a run -----------------------------------------------------------------------------------
    def _namespace(self) -> dict:
        def guard(f):
            def call(*args, **kwargs):
                if self._stop.is_set():
                    raise Stopped()
                return f(*args, **kwargs)
            call.__name__ = getattr(f, "__name__", "primitive")
            return call
        ns = {k: guard(v) for k, v in self.make_namespace().items() if k in self.primitives}
        ns["__builtins__"] = builtins
        return ns

    def _run(self, program: Program) -> None:
        outcome = {"sha": program.sha}
        try:
            exec(compile(program.code, f"<program {program.sha}>", "exec"), self._namespace())  # noqa: S102
            outcome["result"] = "done"
        except Stopped:
            outcome["result"] = "stopped"
        except MotionRefused as e:
            outcome.update(result="refused", error=str(e))
        except Exception as e:  # noqa: BLE001  a failed check: the robot says why
            outcome.update(result="failed", error=f"{type(e).__name__}: {e}",
                           trace=traceback.format_exc(limit=4))
        finally:
            if self.after_run is not None:
                try:
                    self.after_run()
                except Exception as e:  # noqa: BLE001  cleanup trouble never changes the outcome
                    self._log("after_run_failed", error=str(e))
        with self.lock:
            self.last = outcome
            if self.state != RUNNING:            # stopped meanwhile
                self._log("run_ended", **outcome)
                return
            if outcome["result"] == "stopped" or (outcome["result"] == "refused" and self._stop.is_set()):
                self._to(ARMED, "run_cancelled", **{k: v for k, v in outcome.items() if k != "trace"})
            elif outcome["result"] in ("done", "refused"):
                self.runs += outcome["result"] == "done"
                self._to(ARMED, "run_ended", **{k: v for k, v in outcome.items() if k != "trace"})
            elif self.rearm_on_failure:              # the error is in the log and on the page, not said aloud
                self._to(ARMED, "run_failed", error=outcome["error"], rearmed=True)
                self._say("Oops, that didn't work. Say trick or treat to try again.")
            else:
                self._to(CHECK_FAILED, "run_failed", error=outcome["error"])
                self._say("Something went wrong: " + str(outcome["error"]).split(": ", 1)[-1][:SAID_MAX])

    # ---- what the dashboard page shows -----------------------------------------------------------
    def status(self) -> dict:
        with self.lock:
            shown = self.armed or self.offered or self.failed
            return {"state": self.state, "since_s": round(self.clock() - self.since, 1),
                    "summary": shown.summary if shown else None, "sha": shown.sha if shown else None,
                    "code": shown.code if shown else None, "approved": self.armed is not None,
                    "published": self.published if self.armed else None,
                    "runs": self.runs, "last": self.last, "events": self.events[-30:]}


# ---- the HTTP side: the voice service posts what it heard; a browser shows the code -----------------
PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>ARCHITECT program</title>
<style>
  :root { --bg: #fafaf8; --ink: #1d1d1b; --quiet: #6b6b66; --line: #d9d8d3; --accent: #5b3fd1; }
  @media (prefers-color-scheme: dark) { :root { --bg: #171716; --ink: #ecebe6; --quiet: #a3a29b; --line: #393936; --accent: #a996ff; } }
  body { margin: 0; padding: 16px; background: var(--bg); color: var(--ink); font: 15px/1.45 system-ui, sans-serif; }
  main { max-width: 960px; margin: 0 auto; }
  h1 { font-size: 20px; margin: 0 0 12px; }
  .state { font-size: 28px; font-weight: 650; color: var(--accent); }
  .quiet { color: var(--quiet); }
  pre { background: transparent; border: 1px solid var(--line); border-radius: 8px; padding: 12px; overflow-x: auto; font-size: 13px; }
  button { font: inherit; padding: 8px 16px; border-radius: 8px; border: 1px solid var(--line); background: transparent; color: var(--ink); cursor: pointer; }
  button.stop { border-color: #c0392b; color: #c0392b; }
  .row { display: flex; gap: 12px; flex-wrap: wrap; align-items: baseline; margin: 8px 0 16px; }
</style></head>
<body><main>
<h1>ARCHITECT program</h1>
<div class="row"><span class="state" id="state">...</span><span class="quiet" id="since"></span></div>
<form class="row" id="ask"><input id="instruction" placeholder="Instruction for the robot" style="flex:1;min-width:240px;font:inherit;padding:8px;border-radius:8px;border:1px solid var(--line);background:transparent;color:var(--ink)"><button>Send instruction</button></form>
<div class="row"><button id="yes">Yes</button><button id="no">No</button><button id="run">Run once (deadman must be held)</button><button class="stop" id="stop">Stop</button></div>
<p class="quiet">Typed here until the voice service posts what it hears; a typed yes approves like a spoken one.</p>
<p id="published"></p>
<p id="summary"></p><p class="quiet" id="sha"></p>
<pre id="code">No program yet.</pre>
<p class="quiet" id="last"></p>
<details><summary>Events</summary><pre id="events"></pre></details>
</main>
<script>
const $ = (id) => document.getElementById(id);
async function refresh() {
  try {
    const s = await (await fetch("/api/state")).json();
    $("state").textContent = s.state.replace("_", " ");
    $("since").textContent = s.since_s + " s";
    $("summary").textContent = s.summary || "";
    $("sha").textContent = s.sha ? (s.approved ? "approved, frozen as " : "awaiting a spoken yes: ") + s.sha + (s.runs ? " - " + s.runs + " runs" : "") : "";
    $("code").textContent = s.code || "No program yet.";
    $("published").textContent = s.published ? "Show ready: " + s.published.show_id + " (" + s.published.cues + " cues, " + s.published.seconds + " s, closest " + s.published.clearance_mm + " mm; limits: " + s.published.limits + ")" + (s.published.warnings.length ? ". Validator warnings: " + s.published.warnings.join("; ") : "") + ". Push it with show_sync.py, then run it from the Shows page." : "";
    $("last").textContent = s.last ? "Last run: " + s.last.result + (s.last.error ? " - " + s.last.error : "") : "";
    $("events").textContent = s.events.map((e) => JSON.stringify(e)).reverse().join("\\n");
  } catch (e) { $("state").textContent = "runner unreachable"; }
}
const heard = (kind, text) => fetch("/api/heard", {method: "POST", body: JSON.stringify({kind, text})}).then(refresh);
$("ask").onsubmit = (e) => { e.preventDefault(); heard("instruction", $("instruction").value); $("instruction").value = ""; };
$("yes").onclick = () => heard("speech", "yes");
$("no").onclick = () => heard("speech", "no");
$("run").onclick = () => fetch("/api/trigger", {method: "POST"}).then(refresh);
$("stop").onclick = () => fetch("/api/stop", {method: "POST"}).then(refresh);
refresh(); setInterval(refresh, 1000);
</script></body></html>
"""


def serve(runner: DemoRunner, host: str, port: int):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class H(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, code, body, ctype="application/json"):
            data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/":
                return self._send(200, PAGE, "text/html; charset=utf-8")
            if self.path == "/api/state":
                return self._send(200, runner.status())
            return self._send(404, {"error": "not found"})

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}") if n else {}
            except ValueError:
                return self._send(400, {"error": "the body must be JSON"})
            if self.path == "/api/heard":
                kind = body.get("kind")
                if kind not in ("instruction", "speech", "trigger"):
                    return self._send(422, {"error": "kind must be instruction, speech or trigger"})
                return self._send(200, {"state": runner.heard(kind, str(body.get("text") or ""))})
            if self.path == "/api/trigger":
                return self._send(200, {"started": runner.trigger("dashboard"), "state": runner.state})
            if self.path == "/api/stop":
                runner.stop("dashboard")
                return self._send(200, {"state": runner.state})
            if self.path == "/api/cancel":
                return self._send(200, {"cancelled": runner.cancel("dashboard"), "state": runner.state})
            return self._send(404, {"error": "not found"})

    return ThreadingHTTPServer((host, port), H)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--agent", choices=("ssa", "program"), default="ssa",
                    help="ssa: ARCHITECT writes the program; program: always offer --program")
    ap.add_argument("--program", help="the program file --agent program offers")
    ap.add_argument("--dry-run", action="store_true", help="the dry-run robot, with the deadman held")
    ap.add_argument("--sim-deadman", action="store_true",
                    help="the gafro backend on a SIMULATED arm service: the deadman counts as held")
    ap.add_argument("--sim-gripper", action="store_true",
                    help="the gafro backend on a simulation, whose gripper does not run: gripper calls are logged")
    ap.add_argument("--sim-head", action="store_true",
                    help="the gafro backend on a simulation: set_head_pose is logged and moves nothing")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8098)
    ap.add_argument("--log", help="append every event to this file, one JSON line each")
    ap.add_argument("--publish", metavar="DIR",
                    help="the show path: each approved program is recorded as a stamped show in DIR, for "
                         "show_sync.py and the Shows page; nothing here reaches the robot")
    ap.add_argument("--say-url", metavar="URL",
                    help="speak through the listener (listener.py), e.g. http://127.0.0.1:8099/api/say")
    a = ap.parse_args(argv)

    from opendubs_architect import config as config_mod
    cfg = config_mod.OpenDubsConfig()
    primitives = config_mod.primitives()
    publish = None
    if a.publish:
        from opendubs_architect.show_backend import Publisher
        from opendubs_architect.show_backend import PerceptionDryRun
        sees = "detect_objects" in primitives            # the arms_see profile: perception at approval
        publish = Publisher(a.publish, primitives, base=PerceptionDryRun if sees else None)
    after_run, holder = None, {}
    if a.dry_run or a.publish:
        namespace = cfg.make_dry_run_namespace(_Quiet())
        deadman_held = lambda: True  # noqa: E731
    elif config_mod.backend() == "gafro":
        from opendubs_architect import gafro_robot, live_arms
        from opendubs_architect.dryrun import namespace_for
        robot_state = live_arms.runner.DashboardRobotState()
        if a.sim_deadman:                         # a simulated arm service, which has no deadman to read
            deadman_held = lambda: True  # noqa: E731
        else:
            deadman_held = lambda: robot_state.read().get("deadman") is True  # noqa: E731
        robot = gafro_robot.make(live_arms.ArmConfig.load(), deadman_held,
                                 stopped=lambda: holder["runner"]._stop.is_set(),
                                 head="log" if a.sim_head else "dashboard", sim_gripper=a.sim_gripper,
                                 log=lambda ev: print(json.dumps(ev), flush=True))
        namespace = {k: v for k, v in namespace_for(robot).items() if k in primitives}
        after_run = robot.release_head
    else:
        from opendubs_architect import live_arms
        namespace, _client = cfg.make_live_namespace()
        robot_state = live_arms.runner.DashboardRobotState()
        deadman_held = lambda: robot_state.read().get("deadman") is True  # noqa: E731

    if a.agent == "program":
        if not a.program:
            ap.error("--agent program needs --program")
        with open(a.program, encoding="utf-8") as fh:
            agent = ProgramAgent(fh.read())
    else:
        from opendubs_architect.ssa_agent import SsaAgent, ssa_root_on_path
        ssa_root_on_path()
        agent = SsaAgent(namespace, log=lambda line: print(line, flush=True))

    log_fh = open(a.log, "a", encoding="utf-8") if a.log else None

    def log(ev):
        line = json.dumps(ev, default=str)
        print(line, flush=True)
        if log_fh:
            log_fh.write(line + "\n")
            log_fh.flush()

    def speak(text):                              # the arms profile has no say(): the page and log show it
        if a.say_url:                             # queued there, so the runner's lock is not held while it plays
            import urllib.request
            req = urllib.request.Request(a.say_url, data=json.dumps({"text": text}).encode(), method="POST",
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=2.0).close()
            log({"said": text})
            return
        (namespace["say"] if "say" in namespace else lambda t: log({"said": t}))(text)

    runner = DemoRunner(agent, lambda: namespace, speak, deadman_held, log=log,
                        primitives=primitives, publish=publish, rearm_on_failure=a.agent == "program",
                        after_run=after_run)
    holder["runner"] = runner

    def ticks():
        while True:
            runner.tick()
            time.sleep(0.5)
    threading.Thread(target=ticks, daemon=True).start()
    srv = serve(runner, a.host, a.port)
    print(f"ARCHITECT runner on http://{a.host}:{a.port}/", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        runner.stop("shutdown")
    return 0


if __name__ == "__main__":
    sys.exit(main())
