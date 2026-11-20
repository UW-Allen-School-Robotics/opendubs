"""Spot's session: the login, the person's E-Stop, the lease and the commands. Shared by the Tk console
(spot_console.py) and the player (spot_player.py); fake_spot.py has the same interface with no robot.

The E-Stop is kept alive by a person's heartbeat, `last_beat`: the console window's tick, or the
operator page's beats over its WebSocket. A stale heartbeat stops Spot (sit, then cut power); a dead
program stops checking in and Spot stops within ESTOP_TIMEOUT_S.
"""
import threading
import time
from dataclasses import dataclass
from typing import Callable

import bosdyn.client
from bosdyn.client.estop import EstopClient, EstopEndpoint, EstopKeepAlive
from bosdyn.client.lease import LeaseClient, LeaseKeepAlive
from bosdyn.client.robot_command import (RobotCommandBuilder, RobotCommandClient, blocking_sit,
                                         blocking_stand)
from bosdyn.client.robot_state import RobotStateClient
from bosdyn.client.token_manager import TokenManager

from spot_credentials import load

DEFAULT_HOST = "192.168.50.3"
ESTOP_TIMEOUT_S = 9.0
FREEZE_STOP_S = 1.0                     # no heartbeat for this long stops Spot


class SpotSession:
    """Spot's login, the person's E-Stop and the lease, for one session."""

    fake = False

    def __init__(self, host, log, name="opendubs-console-estop", heartbeat="the console window"):
        self.log = log
        self.heartbeat = heartbeat
        self.robot = bosdyn.client.create_standard_sdk("opendubs-spot-console").create_robot(host)
        user, password = load()
        self.robot.authenticate(user, password)
        del password
        self.tokens = TokenManager(self.robot)
        self.robot.time_sync.wait_for_sync()
        self.state_client = self.robot.ensure_client(RobotStateClient.default_service_name)
        self.cmd = self.robot.ensure_client(RobotCommandClient.default_service_name)
        self.lease_client = self.robot.ensure_client(LeaseClient.default_service_name)
        self.lease = None
        self.last_beat = time.monotonic()
        self.released = False
        estop_client = self.robot.ensure_client(EstopClient.default_service_name)
        self._refuse_live_estop(estop_client)
        endpoint = EstopEndpoint(estop_client, name=name, estop_timeout=ESTOP_TIMEOUT_S)
        endpoint.force_simple_setup()   # this session becomes Spot's only E-Stop
        self.estop = EstopKeepAlive(endpoint, keep_running_cb=self._person_alive)
        self.stop("connected")
        self._watch = threading.Thread(target=self._watchdog, daemon=True)
        self._watch.start()

    @staticmethod
    def _refuse_live_estop(client):
        """force_simple_setup would replace a live E-Stop, leaving its STOP connected to nothing: a second
        console, the tablet or estop.py. Refuse while any endpoint is still checking in."""
        for e in client.get_status().endpoints:
            if e.time_since_valid_response.ToTimedelta().total_seconds() < ESTOP_TIMEOUT_S:
                raise RuntimeError(f"refused: another E-Stop is live on Spot ({e.endpoint.name or 'unnamed'}); "
                                   "close it (or release control on the tablet) and connect again")

    # The E-Stop. stop() returns at once; the check-in goes on its own thread.
    def stop(self, why):
        self.released = False
        threading.Thread(target=self.estop.settle_then_cut, daemon=True).start()
        self.log(f"E-STOP: {why}")

    def release(self):
        self.estop.allow()
        self.released = True
        self.log(f"E-Stop released by the person at {self.heartbeat}")

    def _person_alive(self):
        return time.monotonic() - self.last_beat < 3 * FREEZE_STOP_S

    def _watchdog(self):
        while True:
            time.sleep(0.2)
            if self.released and time.monotonic() - self.last_beat > FREEZE_STOP_S:
                self.stop(f"{self.heartbeat} stopped responding")

    # Commands: blocking, so they run on one command thread. Each one is listed in COMMANDS.
    def _need_released(self):
        if not self.released:
            raise RuntimeError("refused: the E-Stop is STOPPED; release it first")

    def _need_powered(self):
        if not self.robot.is_powered_on():
            raise RuntimeError("refused: motors are off; Power on first")

    def _ensure_lease(self):
        if self.lease is None:
            self.lease = LeaseKeepAlive(self.lease_client, must_acquire=True, return_at_exit=True)

    def power_on(self):
        self._need_released()
        self._ensure_lease()
        self.robot.power_on(timeout_sec=20)
        return "motors on, lease held (Spot stays where it is)"

    def stand(self):
        self.power_on()
        blocking_stand(self.cmd, timeout_sec=10)
        return "standing, motors on, lease held"

    def gripper(self, fraction):
        self._need_released()
        self._need_powered()
        self._ensure_lease()
        self.cmd.robot_command(RobotCommandBuilder.claw_gripper_open_fraction_command(fraction))
        return f"gripper to {fraction:.0%} open"

    def sit(self):
        if self.lease is None:
            return "no lease held: nothing to sit"
        try:
            if self.robot.is_powered_on():
                blocking_sit(self.cmd, timeout_sec=10)
                self.robot.power_off(cut_immediately=False, timeout_sec=20)
        finally:
            self.lease.shutdown()           # returns the lease (return_at_exit)
            self.lease = None
        return "sitting, motors off, lease returned"

    def is_standing(self):
        return self.lease is not None and self.robot.is_powered_on()

    # Playback (player.py). Not built for the real robot: the plan's ranges and caps are placeholders.
    @staticmethod
    def plan_refusal(plan):
        return "real playback not built yet: placeholder limits (built: false); plays on the fake Spot only"

    def send_window(self, command, t0_wall):
        raise RuntimeError(self.plan_refusal(None))

    def status(self):
        s = self.state_client.get_robot_state()
        ps = s.power_state
        return {
            "motors": ps.MotorPowerState.Name(ps.motor_power_state).removeprefix("STATE_"),
            "battery_pct": round(s.battery_states[0].charge_percentage.value) if s.battery_states else None,
            "estop": "RELEASED" if self.released else "STOPPED",
            "estop_robot": [f"{e.name}:{e.State.Name(e.state).removeprefix('STATE_')}" for e in s.estop_states],
            "lease": "held" if self.lease else "not held",
            "faults": [f.name for f in s.system_fault_state.faults]
                      + [f.Cause.Name(f.cause) for f in s.behavior_fault_state.faults],
        }

    def close(self):
        self.stop("closing")
        try:
            self.sit()
        except Exception as e:
            self.log(f"sit on close failed: {e}")
        time.sleep(0.5)
        self.estop.shutdown()
        self.tokens.stop()


@dataclass(frozen=True)
class Command:
    """One of Spot's functions: a widget on each surface and a name on each interface. Add new ones
    (standing height, body pose, ...) to COMMANDS. `widget` says how a surface shows it: "button" today;
    a "slider" kind (one value between bounds, e.g. a standing height) is the planned next one."""
    name: str
    label: str
    group: str
    run: Callable
    widget: str = "button"


COMMANDS = [
    Command("power_on", "Power on (stays sitting)", "Power", lambda s: s.power_on()),
    Command("stand", "Power on + stand", "Power", lambda s: s.stand()),
    Command("sit", "Sit + power off", "Power", lambda s: s.sit()),
    Command("gripper_open", "Open gripper", "Gripper", lambda s: s.gripper(1.0)),
    Command("gripper_close", "Close gripper", "Gripper", lambda s: s.gripper(0.0)),
]
BY_NAME = {c.name: c for c in COMMANDS}


def plan_length_s(plan):
    return max((c["show_start_s"] + c["points"][-1]["time_since_reference"]
                for c in plan.get("commands", []) if c.get("points")), default=0.0)


def summarize_plan(plan):
    """A few lines about an ENCORE spot_driver.plan() output."""
    parts = {}
    for c in plan.get("commands", []):
        parts[c["part"]] = parts.get(c["part"], 0) + 1
    lines = [f"robot {plan.get('robot')}, driver {plan.get('driver')}",
             f"{plan.get('windows')} windows of {plan.get('window_s')} s, lead {plan.get('lead_s')} s, "
             f"about {plan_length_s(plan):.1f} s long",
             "commands: " + (", ".join(f"{p} {n}" for p, n in sorted(parts.items())) or "none")]
    for p in plan.get("problems", [])[:8]:
        lines.append(f"problem: {p}")
    return "\n".join(lines)
