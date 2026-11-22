"""A stand-in for SpotSession with no robot: the same E-Stop, heartbeat, command and playback interface,
so the player and its page can be developed with Spot switched off. Every command it is sent is logged
with the time it arrived, and each show window is checked against its deadline: on time means it
arrived before its reference time (show start + window start), as Spot needs it.

Show windows go through the real path up to the network: the first-run envelope, build_window() and
command_problems(), so a plan or a builder change that a real Spot would refuse fails here first.
"""
import threading
import time

from spot_playback import build_window, command_problems
from spot_session import FREEZE_STOP_S, SpotSession


class FakeSession:
    fake = True

    def __init__(self, host, log, name="fake", heartbeat="the operator page"):
        self.log = log
        self.heartbeat = heartbeat
        self.host = f"fake Spot (no robot; asked for {host})"
        self.last_beat = time.monotonic()
        self.released = False
        self.motors = False
        self.lease = None
        self.gripper_open = 0.0
        self.stood = False
        self._carry = {}
        self.sent = []                      # one entry per show window received
        self.stop("connected")
        threading.Thread(target=self._watchdog, daemon=True).start()

    def stop(self, why):
        self.released = False
        self.stood = False
        if self.motors:
            self.motors = False             # settle_then_cut: sits, then power is cut
            self.log("fake Spot sits and its motors are cut")
        self.log(f"E-STOP: {why}")

    def release(self):
        self.released = True
        self.log(f"E-Stop released by the person at {self.heartbeat}")

    def _watchdog(self):
        while True:
            time.sleep(0.2)
            if self.released and time.monotonic() - self.last_beat > FREEZE_STOP_S:
                self.stop(f"{self.heartbeat} stopped responding")

    def _need_released(self):
        if not self.released:
            raise RuntimeError("refused: the E-Stop is STOPPED; release it first")

    def power_on(self):
        self._need_released()
        self.lease = "fake lease"
        self.motors = True
        return "motors on, lease held (fake)"

    def stand(self):
        self.power_on()
        time.sleep(1.0)
        self.stood = True
        return "standing, motors on, lease held (fake)"

    def gripper(self, fraction):
        self._need_released()
        if not self.motors:
            raise RuntimeError("refused: motors are off; Power on first")
        self.gripper_open = fraction
        return f"gripper to {fraction:.0%} open (fake)"

    def sit(self):
        if self.lease is None:
            return "no lease held: nothing to sit"
        self.motors, self.lease, self.stood = False, None, False
        return "sitting, motors off, lease returned (fake)"

    def is_standing(self):
        return self.motors and self.lease is not None and self.stood

    plan_refusal = staticmethod(SpotSession.plan_refusal)

    def send_window(self, commands, t0_wall):
        self._need_released()
        if not self.motors:
            raise RuntimeError("refused: motors are off")
        now = time.time()
        if commands[0]["window"] == 0:
            self._carry = {}
        problems = command_problems(build_window(commands, t0_wall, now, self._carry), t0_wall, now)
        if problems:
            raise RuntimeError("a real Spot would refuse this window: " + "; ".join(problems))
        due = t0_wall + commands[0]["show_start_s"]      # the trajectory's reference time
        margin = due - now
        entry = {"window": commands[0]["window"], "part": "+".join(sorted(c["part"] for c in commands)),
                 "points": sum(len(c["points"]) for c in commands),
                 "show_s": round(now - t0_wall, 3), "margin_s": round(margin, 3), "on_time": margin > 0}
        self.sent.append(entry)
        self.log(f"fake Spot got window {entry['window']} {entry['part']} ({entry['points']} points) at show "
                 f"t={entry['show_s']:+.2f} s, {margin:.2f} s before it starts"
                 + ("" if entry["on_time"] else "  LATE"))
        return entry

    def after_show(self):
        return "arm stowed (fake)"

    def status(self):
        return {"motors": "ON" if self.motors else "OFF", "battery_pct": 100,
                "estop": "RELEASED" if self.released else "STOPPED", "estop_robot": ["fake"],
                "lease": "held" if self.lease else "not held", "faults": []}

    def close(self):
        self.stop("closing")
        self.sit()
