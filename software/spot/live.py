"""Live mode: a stream of velocity requests drives Spot, for the students' traffic-control system.

The students' controller keeps the robots apart with MOCAP. This end only holds Spot to the walking
limits and lets it stop when requests stop. It runs inside the player, so the operator page keeps the
E-Stop, the lease and the heartbeat exactly as for a show.

The interface: a WebSocket at /live on its own port (8100 by default), which serves nothing else. A
client sends one JSON message per request, about 20 a second:
    {"seq": 12, "vx": 0.2, "vy": 0.0, "wz": 0.1}     # m/s forward, m/s left, rad/s counter-clockwise, body frame
and gets one reply per request:
    {"seq": 12, "result": "sent" | "limited" | "refused", "reason": "...", "sent": {"vx", "vy", "wz"}}
- "limited" means it was sent clamped to the limits; "sent" shows what Spot was told.
- A seq at or below the last one is refused (out of order). One client at a time.
- Each request becomes a velocity command that ends END_S after it is sent. When requests stop, Spot
  stands where it is once the last one ends. The requests never count as the operator's heartbeat.
- Nothing is sent until the operator switches Live on, with Spot standing and no show ready or playing.
With the fake Spot, replies also carry "fake_pose": the pose the fake integrates from what it was sent.
"""
import json
import math
import os
import threading
import time

from bosdyn.api import geometry_pb2
from bosdyn.api.spot import robot_command_pb2 as spot_command_pb2
from bosdyn.client.robot_command import RobotCommandBuilder

from spot_playback import WALKING

END_S = 0.4                     # each command ends this long after it is sent
LIMITS = {"speed": WALKING["base_speed"], "turn": math.radians(WALKING["base_turn_deg"])}   # m/s, rad/s
PORT = 8100


def clamp(req):
    """(vx, vy, wz, limited) from a request, held to LIMITS: speed along the ground, then the turn rate."""
    vals = []
    for k in ("vx", "vy", "wz"):
        v = req.get(k, 0.0)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            raise ValueError(f"{k} must be a finite number")
        vals.append(float(v))
    vx, vy, wz = vals
    limited = False
    speed = math.hypot(vx, vy)
    if speed > LIMITS["speed"]:
        vx, vy, limited = vx * LIMITS["speed"] / speed, vy * LIMITS["speed"] / speed, True
    if abs(wz) > LIMITS["turn"]:
        wz, limited = math.copysign(LIMITS["turn"], wz), True
    return vx, vy, wz, limited


def velocity_command(vx, vy, wz, padding=None):
    """Spot's velocity command in the body frame, TROT, its speed limit set to LIMITS. Obstacle
    avoidance stays on; `padding` (m) sets how far it keeps from obstacles, Spot's default when None."""
    cap, turn = LIMITS["speed"], LIMITS["turn"]
    params = spot_command_pb2.MobilityParams(
        locomotion_hint=spot_command_pb2.HINT_TROT,
        vel_limit=geometry_pb2.SE2VelocityLimit(
            max_vel=geometry_pb2.SE2Velocity(linear=geometry_pb2.Vec2(x=cap, y=cap), angular=turn),
            min_vel=geometry_pb2.SE2Velocity(linear=geometry_pb2.Vec2(x=-cap, y=-cap), angular=-turn)))
    if padding is not None:
        params.obstacle_params.obstacle_avoidance_padding = padding
    return RobotCommandBuilder.synchro_velocity_command(vx, vy, wz, params=params)


def command_problems(cmd):
    """Why a real Spot must not be sent this velocity command; [] when it may. Checked on the built
    message, just before it goes."""
    out = []
    mc = cmd.synchronized_command.mobility_command
    if not mc.HasField("se2_velocity_request") or cmd.synchronized_command.HasField("arm_command"):
        return ["not a plain velocity command"]
    v = mc.se2_velocity_request.velocity
    if math.hypot(v.linear.x, v.linear.y) > LIMITS["speed"] + 1e-6:
        out.append(f"speed {math.hypot(v.linear.x, v.linear.y):.2f} m/s is over {LIMITS['speed']} m/s")
    if abs(v.angular) > LIMITS["turn"] + 1e-6:
        out.append(f"turn {math.degrees(v.angular):.0f} deg/s is over {math.degrees(LIMITS['turn']):.0f} deg/s")
    mp = spot_command_pb2.MobilityParams()
    mc.params.Unpack(mp)
    if mp.locomotion_hint != spot_command_pb2.HINT_TROT:
        out.append("gait is not TROT")
    o = mp.obstacle_params
    if o.disable_vision_body_obstacle_avoidance or o.disable_vision_foot_obstacle_avoidance:
        out.append("obstacle avoidance is off")
    return out


class Live:
    """The player's live mode: the operator's switch, the one client, and the log of every request."""

    def __init__(self, player, log_dir, padding=None):
        self.p = player
        self.padding = padding
        self.on = False
        self.client = None              # the one connected client's address
        self.last_seq = None
        self.last = None                # the last reply
        self.last_at = None             # monotonic time of the last request
        self.counts = {"sent": 0, "limited": 0, "refused": 0}
        self.lock = threading.Lock()
        os.makedirs(log_dir, exist_ok=True)
        self.path = os.path.join(log_dir, time.strftime("live-%Y%m%d-%H%M%S.jsonl"))
        self._f = open(self.path, "a")
        threading.Thread(target=self._watch, daemon=True).start()

    def _watch(self):
        """Live goes off the moment it can't stay on: the heartbeat stop sat Spot, a show was readied."""
        while True:
            time.sleep(0.1)
            if self.on:
                reason = self.why_not()
                if reason:
                    self.switch(False, reason)

    def _write(self, row):
        self._f.write(json.dumps(row) + "\n")
        self._f.flush()

    def why_not(self):
        """Why the operator can't switch Live on now; None when they can."""
        s = self.p.session
        if not s:
            return "not connected"
        if not s.released:
            return "the E-Stop is STOPPED"
        if not s.is_standing():
            return "Spot isn't standing (Power on + stand first)"
        if self.p.playback.state in ("ARMED", "PLAYING"):
            return f"a show is {self.p.playback.state}"
        return None

    def switch(self, on, why="the operator"):
        if on:
            reason = self.why_not()
            if reason:
                raise RuntimeError(f"refused: Live stays off: {reason}")
        if on != self.on:
            self.on = on
            self.last_seq = None        # a new run of requests may start its seq again
            self.p.log(f"Live {'ON: velocity requests drive Spot' if on else 'OFF'} ({why})")
            self._write({"t": time.time(), "live": on, "why": why})

    def connect(self, who):
        with self.lock:
            if self.client is not None:
                return False
            self.client, self.last_seq = who, None
        self.p.log(f"live client connected from {who}")
        self._write({"t": time.time(), "client": who, "event": "connected"})
        return True

    def disconnect(self, who):
        with self.lock:
            if self.client != who:
                return
            self.client = None
        self.p.log(f"live client {who} disconnected; Spot stands once its last command ends")
        self._write({"t": time.time(), "client": who, "event": "disconnected"})

    def refuse(self, msg, reason):
        """A request refused before it is read: not JSON, or superseded by a newer one that arrived while
        Spot was still taking the one before."""
        reply = {"seq": msg.get("seq") if isinstance(msg, dict) else None, "result": "refused", "reason": reason}
        self.counts["refused"] += 1
        self._write({"t": time.time(), "req": msg, "result": "refused", "reason": reason})
        return reply

    def request(self, msg):
        """One request, start to finish: checked, clamped, sent, logged. Returns the reply. Blocking (it
        waits for Spot to take the command), so the server calls it off its event loop."""
        t = time.time()
        self.last_at = time.monotonic()
        reply = {"seq": msg.get("seq") if isinstance(msg, dict) else None}
        row = {"t": t, "req": msg}
        try:
            if not isinstance(msg, dict):
                raise ValueError("a request is a JSON object")
            seq = msg.get("seq")
            if isinstance(seq, bool) or not isinstance(seq, int):
                raise ValueError("seq must be an integer")
            if self.last_seq is not None and seq <= self.last_seq:
                raise ValueError(f"out of order: seq {seq} after {self.last_seq}")
            self.last_seq = seq
            vx, vy, wz, limited = clamp(msg)
            if not self.on:
                raise RuntimeError("Live is off on the operator page")
            reason = self.why_not()
            if reason:
                self.switch(False, reason)
                raise RuntimeError(reason)
            end = time.time() + END_S
            out = self.p.session.send_velocity(vx, vy, wz, end, self.padding)
            reply.update(result="limited" if limited else "sent",
                         sent={"vx": round(vx, 4), "vy": round(vy, 4), "wz": round(wz, 4)})
            if limited:
                reply["reason"] = (f"held to {LIMITS['speed']} m/s and {math.degrees(LIMITS['turn']):.0f} deg/s")
            if isinstance(out, dict) and "pose" in out:
                reply["fake_pose"] = out["pose"]
            row.update(sent=reply["sent"], end=round(end, 3), took_s=round(time.time() - t, 4))
        except Exception as e:
            from spot_session import explain
            reply.update(result="refused", reason=explain(e))
        self.counts[reply["result"]] += 1
        self.last = reply
        row["result"] = reply["result"]
        if "reason" in reply:
            row["reason"] = reply["reason"]
        self._write(row)
        return reply

    def describe(self):
        age = None if self.last_at is None else round(time.monotonic() - self.last_at, 2)
        return {"on": self.on, "client": self.client, "why_not": self.why_not(), "counts": dict(self.counts),
                "last": self.last, "last_age_s": age, "stale": age is None or age > END_S,
                "limits": {"speed_m_s": LIMITS["speed"], "turn_deg_s": math.degrees(LIMITS["turn"])},
                "end_s": END_S, "log": self.path}
