"""Plays an ENCORE Spot plan through a session (SpotSession or FakeSession), one window at a time.

States: EMPTY -> LOADED -> ARMED -> PLAYING -> DONE, and STOPPED from ARMED or PLAYING.
- Only the person on the operator surface arms a show. A program (ENCORE) may start it only while it is
  armed: the parity with opendubs2, where a show waits ARMED for the operator's deadman.
- start(at) takes t0 as wall-clock seconds (time.time()), so several players can share one start.
  Each window is sent `lead_s` before it starts, as the plan's send_by says.
- Any E-Stop ends playback. A window already sent keeps running on Spot until it ends, so stopping a
  show is the E-Stop (sit, then cut), never a quiet halt.
"""
import threading
import time

from spot_session import plan_length_s, summarize_plan

ARM_EXPIRES_S = 120.0       # an armed show nobody starts disarms itself


class Playback:
    def __init__(self, log):
        self.log = log
        self.state = "EMPTY"
        self.plan = None
        self.name = None
        self.t0 = None
        self.armed_at = None
        self.why = ""
        self.sent = 0
        self._thread = None

    def describe(self):
        return {"state": self.state, "plan": self.name, "why": self.why, "t0": self.t0,
                "show_s": round(time.time() - self.t0, 2) if self.t0 and self.state == "PLAYING" else None,
                "length_s": round(plan_length_s(self.plan), 2) if self.plan else None,
                "sent": self.sent, "windows": len(self.plan["commands"]) if self.plan else 0,
                "summary": summarize_plan(self.plan) if self.plan else ""}

    def load(self, name, plan):
        if self.state in ("ARMED", "PLAYING"):
            raise RuntimeError(f"refused: a show is {self.state}; stop it first")
        if plan.get("robot") != "spot" or not isinstance(plan.get("commands"), list):
            raise RuntimeError("refused: not a Spot plan (robot must be 'spot', with a commands list)")
        self.plan, self.name, self.state, self.why, self.sent, self.t0 = plan, name, "LOADED", "", 0, None
        self.log(f"plan loaded: {name}")
        return self.describe()

    def problems(self, session):
        """Why this show can't be armed or started now; empty when it can."""
        out = []
        if self.plan is None:
            return ["no plan loaded"]
        if session is None:
            return ["not connected"]
        out += [f"plan problem: {p}" for p in self.plan.get("problems", [])]
        refusal = session.plan_refusal(self.plan)
        if refusal:
            out.append(refusal)
        if not session.released:
            out.append("the E-Stop is STOPPED")
        elif not session.is_standing():
            out.append("Spot is not standing: Power on + stand first")
        return out

    def arm(self, session):
        if self.state not in ("LOADED", "STOPPED", "DONE"):
            raise RuntimeError(f"refused: cannot arm while {self.state}")
        problems = self.problems(session)
        if problems:
            raise RuntimeError("refused: " + "; ".join(problems))
        self.state, self.armed_at, self.why, self.sent = "ARMED", time.monotonic(), "", 0
        self.log("show ARMED: Start, or ENCORE's start(at), plays it")
        return self.describe()

    def start(self, session, at=None):
        if self.state != "ARMED":
            raise RuntimeError(f"refused: the show is {self.state}, not ARMED (only the operator arms it)")
        if time.monotonic() - self.armed_at > ARM_EXPIRES_S:
            self.state, self.why = "LOADED", f"armed over {ARM_EXPIRES_S:.0f} s ago; arm it again"
            raise RuntimeError(f"refused: {self.why}")
        problems = self.problems(session)
        if problems:
            raise RuntimeError("refused: " + "; ".join(problems))
        lead = float(self.plan.get("lead_s", 1.0))
        now = time.time()
        at = now + lead + 0.2 if at is None else float(at)
        if at - lead < now - 0.05:
            raise RuntimeError(f"refused: start time is {now - at:+.2f} s from now; the first window must be "
                               f"sent {lead} s before it")
        self.t0, self.state = at, "PLAYING"
        self._thread = threading.Thread(target=self._play, args=(session, lead), daemon=True)
        self._thread.start()
        self.log(f"show PLAYING: t0 in {at - now:.2f} s")
        return self.describe()

    def stopped(self, why):
        """Called on every E-Stop."""
        if self.state in ("ARMED", "PLAYING"):
            self.state, self.why = "STOPPED", why
            self.log(f"show STOPPED: {why}")

    def _going(self, session):
        if self.state == "PLAYING" and not session.released:
            self.stopped("the E-Stop stopped Spot")     # the watchdog or another surface stopped it
        return self.state == "PLAYING"

    def _play(self, session, lead):
        for c in sorted(self.plan["commands"], key=lambda c: (c["show_start_s"], c["part"])):
            send_at = self.t0 + c["show_start_s"] - lead
            while self._going(session) and time.time() < send_at:
                time.sleep(min(0.02, max(0.0, send_at - time.time())))
            if not self._going(session):
                return
            try:
                session.send_window(c, self.t0)
                self.sent += 1
            except Exception as e:
                session.stop(f"sending window {c['window']} {c['part']} failed: {e}")
                self.stopped(f"send failed: {e}")
                return
        end = self.t0 + plan_length_s(self.plan)
        while self._going(session) and time.time() < end:
            time.sleep(0.05)
        if self._going(session):
            self.state, self.why = "DONE", "played to the end; Spot is still standing"
            self.log("show DONE")
