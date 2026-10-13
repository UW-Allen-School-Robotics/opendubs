"""The lift and the state light, for a show's LIVE runs: through the dashboard, never round it.

Each is armed separately (`runner.py palette --lift dashboard --light dashboard`) and acts only on a
live run — so only under the runner's own gate: CH5 AUTO, the deadman held, the arms active. Both
end the moment the run ends, however it ends.

THE LIFT goes through the dashboard's POST /api/mast/{up|down|stop}?by=show, which the StamPLC takes
as `MAST CMD`: a held intent it expires after 250 ms, behind the contactor, the e-stop, the RC link
and the pinch guard. So the show RENEWS its intent every RENEW_S while a cue is in force, and a
runner that dies stops the lift within 250 ms. The show never sends `MAST ACK`: it cannot authorize a
descent through the pinch zone, so a long `down` stops at the guard (the twin does not model that).
THE OPERATOR WINS (decided 2026-10-12, as the RC stick does in the firmware): if anyone else sends a
lift command THROUGH THE DASHBOARD during the run — the Z-AXIS HOLD buttons on its StamPLC page, or
the ROS `/zaxis/*` triggers (stamplc_bridge.py), counted as `mast_operator_seq` — the show's lift
output stands down for the rest of that run, and says so. NOT SEEN (corrected 2026-10-13): the head's
RAISE/LOWER, which the Tab5 sends straight to the StamPLC as `MAST CMD`, and the RC stick. The stick
needs no counting: it moves the lift in MANUAL only, and leaving AUTO aborts the show. The head's
buttons do: during a run in AUTO with the deadman held, the StamPLC takes either sender's intent,
latest first — the operator's way to win there is to let go of the deadman (which ends the show and
stops its lift) or STOP. Seeing them would need the StamPLC to report who sent the intent.

THE LIGHT goes through POST /api/choreo/light (the dashboard's show_light.py): a lease the dashboard
layers over the state colour, which a reserved state (DEGRADED, E_STOP) always overrides. The strip
shares the StamPLC's I2C with the drive ESCs, so colour changes closer than MIN_GAP_S are merged here
(the latest wins), and the dashboard writes only on a change, from its own 0.3 s loop.

Nothing here raises into the show: a failure stands that output down for the run, logged once.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request


class Dashboard:
    """POSTs to the dashboard on this machine (the runner and the dashboard share the Thor)."""

    def __init__(self, base: str = "http://127.0.0.1:8080", timeout: float = 0.5):
        self.base, self.timeout = base.rstrip("/"), timeout

    def post(self, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body or {}).encode()
        req = urllib.request.Request(self.base + path, data=data, method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read() or b"{}")

    def mast(self, cmd: str) -> dict:
        return self.post(f"/api/mast/{urllib.parse.quote(cmd)}?by=show")

    def light(self, body: dict) -> dict:
        return self.post("/api/choreo/light", body)


def base_of(url: str) -> str:
    """http://127.0.0.1:8080/api/choreo/robot -> http://127.0.0.1:8080"""
    p = urllib.parse.urlsplit(url)
    return f"{p.scheme}://{p.netloc}"


class LiftOutput:
    RENEW_S = 0.1                     # well inside the StamPLC's 250 ms expiry

    def __init__(self, dash, note):
        self.dash, self.note = dash, note
        self.intent, self.until, self.last = 0, 0.0, None
        self.sent_any, self.off, self.seq0 = False, None, None

    def start(self, operator_seq):
        """At RUNNING: the operator count to watch (None: the dashboard does not report it)."""
        self.seq0 = operator_seq
        if operator_seq is None:
            self.note("live", note="the dashboard does not report operator lift commands: the show's lift "
                                   "cannot see an operator take over (it still stops on every end)")
        else:
            self.note("live", note="the show's lift stands down if the dashboard's lift buttons or /zaxis are "
                                   "used; the head's RAISE/LOWER go straight to the StamPLC and are NOT seen — "
                                   "let go of the deadman or STOP to take the lift back")

    def _post(self, cmd) -> bool:
        try:
            self.dash.mast(cmd)
            self.sent_any = True
            return True
        except Exception as e:  # noqa: BLE001  the lift is never a reason to stop the show
            self._stand_down(f"the lift command failed ({type(e).__name__}: {e}); the lift stops by its own "
                             "250 ms expiry", send_stop=False)
            return False

    def _stand_down(self, why, send_stop=True):
        if self.off:
            return
        self.off, self.intent = why, 0
        if send_stop and self.sent_any:
            try:
                self.dash.mast("stop")
            except Exception:  # noqa: BLE001
                pass
        self.note("live", note=f"the show's lift output stands down for the rest of this run: {why}")

    def cue(self, c: dict, now: float, t: float):
        if self.off:
            return
        do = c.get("do")
        if do in ("up", "down"):
            self.intent = 1 if do == "up" else -1
            self.until = now + float(c.get("secs", 0.0))
            self.last = now
            if self._post(do):
                self.note("lift", do=do, secs=c.get("secs"), show_t=round(t, 3))
        elif do == "stop":
            self.intent = 0
            if self._post("stop"):
                self.note("lift", do="stop", show_t=round(t, 3))

    def tick(self, now: float, operator_seq):
        if self.off:
            return
        if self.seq0 is not None and operator_seq is not None and operator_seq != self.seq0:
            self._stand_down("an operator moved the lift during the run — the operator wins")
            return
        if self.intent and now >= self.until:
            self.intent = 0
            self._post("stop")
        elif self.intent and now - self.last >= self.RENEW_S:
            self.last = now
            self._post("up" if self.intent > 0 else "down")

    def end(self):
        """Every end — completed, aborted, e-stopped, lease lost: stop now, not in 250 ms."""
        if self.sent_any and not self.off:
            self.intent = 0
            self._post("stop")


class LightOutput:
    RENEW_S, LEASE_MS = 0.2, 600      # the lease outlives two missed renewals
    MIN_GAP_S = 0.33                  # colour changes closer than this merge: the latest wins

    def __init__(self, dash, note):
        self.dash, self.note = dash, note
        self.current, self.pending, self.changed, self.renewed = None, None, None, None
        self.sent_any, self.off = False, None

    def _post(self, body) -> bool:
        try:
            self.dash.light(body)
            self.sent_any = True
            return True
        except Exception as e:  # noqa: BLE001
            if not self.off:
                self.off = f"the light command failed ({type(e).__name__}: {e}); the lease lapses by itself"
                self.note("live", note=f"the show's light output stands down for the rest of this run: {self.off}")
            return False

    def cue(self, c: dict, now: float, t: float):
        if self.off:
            return
        if c.get("do") == "release":
            self.pending = "release"
        elif c.get("do") == "color":
            self.pending = (list(c["rgb"]), int(c.get("br", 100)))
        self.tick(now)

    def tick(self, now: float, operator_seq=None):
        if self.off:
            return
        if self.pending is not None and (self.changed is None or now - self.changed >= self.MIN_GAP_S):
            p, self.pending, self.changed = self.pending, None, now
            if p == "release":
                self.current = None
                self._post({"release": True})
            else:
                self.current, self.renewed = p, now
                self._post({"rgb": p[0], "br": p[1], "lease_ms": self.LEASE_MS})
        elif self.current is not None and now - self.renewed >= self.RENEW_S:
            self.renewed = now
            self._post({"rgb": self.current[0], "br": self.current[1], "lease_ms": self.LEASE_MS})

    def end(self):
        if self.sent_any and not self.off:
            self.current = self.pending = None
            self._post({"release": True})
