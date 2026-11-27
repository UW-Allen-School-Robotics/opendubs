"""A stand-in for SpotSession with no robot: the same E-Stop, heartbeat, command and playback interface,
so the player and its page can be developed with Spot switched off. Every command it is sent is logged
with the time it arrived, and each show window is checked against its deadline: on time means it
arrived before its reference time (show start + window start), as Spot needs it.

Show windows go through the real path up to the network: the envelope, build_window() and
command_problems() with the walking guard, so a plan or a builder change that a real Spot would refuse
fails here first. Its Spot starts a walking show at an awkward vision-frame pose (START), facing nearly
backwards, so a mistake in the frame change or in the angle's wrap shows up here.
"""
import threading
import time

import math

from live import command_problems as velocity_problems, velocity_command
from spot_playback import build_window, command_problems, end_time_s
from spot_session import FREEZE_STOP_S, SpotSession


START = (3.2, -1.7, 3.05)       # the fake Spot's vision-frame pose (x, y, angle) at a walking show's start


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
        self._start = None
        self.sent = []                      # one entry per show window received
        self.pose = [0.0, 0.0, 0.0]         # live mode: x, y (m), yaw (rad) in the fake's odometry frame
        self._vel = (0.0, 0.0, 0.0, 0.0)    # vx, vy, wz (body frame) and the wall time they end
        self._pose_t = time.time()
        self.stop("connected")
        threading.Thread(target=self._watchdog, daemon=True).start()

    def stop(self, why):
        self.released = False
        self.stood = False
        if hasattr(self, "_vel"):
            self._advance(time.time())
            self._vel = (0.0, 0.0, 0.0, 0.0)    # sits: the last velocity command no longer moves it
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
            self._start = START if any(c["part"] == "BASE" for c in commands) else None
        cmd = build_window(commands, t0_wall, now, self._carry, self._start)
        problems = command_problems(cmd, t0_wall, now, self._start)
        end = end_time_s(cmd)
        if end is not None and end <= now:
            problems.append("BASE: its end time is already past")
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

    def _advance(self, now):
        """Moves the fake pose by the velocity it was last sent, until that command's end time."""
        vx, vy, wz, end = self._vel
        t0, t1 = self._pose_t, min(now, end)
        self._pose_t = now
        steps = max(1, int((t1 - t0) / 0.01)) if t1 > t0 else 0
        dt = (t1 - t0) / steps if steps else 0.0
        x, y, a = self.pose
        for _ in range(steps):
            x += (vx * math.cos(a) - vy * math.sin(a)) * dt
            y += (vx * math.sin(a) + vy * math.cos(a)) * dt
            a += wz * dt
        self.pose = [x, y, math.atan2(math.sin(a), math.cos(a))]

    def pose_report(self):
        self._advance(time.time())
        x, y, a = self.pose
        return {"x": round(x, 4), "y": round(y, 4), "yaw_deg": round(math.degrees(a), 2)}

    def send_velocity(self, vx, vy, wz, end_wall, padding=None):
        """Live mode: the same checks as a real Spot, then it integrates the velocity into its pose."""
        self._need_released()
        if not self.is_standing():
            raise RuntimeError("refused: Spot isn't standing; Power on + stand first")
        problems = velocity_problems(velocity_command(vx, vy, wz, padding))
        now = time.time()
        if end_wall <= now:
            problems.append("its end time is already past")
        if problems:
            raise RuntimeError("a real Spot would refuse this command: " + "; ".join(problems))
        self._advance(now)
        self._vel = (vx, vy, wz, end_wall)
        return {"pose": self.pose_report()}

    def after_show(self):
        return "arm stowed (fake)"

    def status(self):
        return {"motors": "ON" if self.motors else "OFF", "battery_pct": 100,
                "estop": "RELEASED" if self.released else "STOPPED", "estop_robot": ["fake"],
                "lease": "held" if self.lease else "not held", "faults": [], "fake_pose": self.pose_report()}

    def close(self):
        self.stop("closing")
        self.sit()
