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
from bosdyn.api import arm_command_pb2
from bosdyn.client.estop import EstopClient, EstopEndpoint, EstopKeepAlive
from bosdyn.client.lease import LeaseClient, LeaseKeepAlive
from bosdyn.client.robot_command import (RobotCommandBuilder, RobotCommandClient, blocking_sit,
                                         blocking_stand)
from bosdyn.client.robot_state import RobotStateClient
from bosdyn.client.token_manager import TokenManager

from spot_credentials import load
from spot_playback import build_window, command_problems, envelope_problems

DEFAULT_HOST = "192.168.50.3"

# What each Spot SDK error means here, and what the person at Spot does about it. Keyed by class name, so
# one table covers the lease, power and command clients.
ADVICE = {
    "KeepaliveMotorsOffError": "an E-Stop or lease stopped checking in, so Spot holds its motors off. Press "
                               "Release again, then power on. If it stays, run keepalive_status.py",
    "EstoppedError": "an E-Stop is stopping Spot. Release it on the operator page, and check the tablet and "
                     "Spot's own E-Stop buttons",
    "ResourceAlreadyClaimedError": "another client holds Spot's lease, usually the tablet. Release control "
                                   "on the tablet, then try again",
    "BehaviorFaultError": "Spot has a behavior fault, for example after a bump. Its status lists the fault. "
                          "Sit + power off, then stand again",
    "FaultedError": "Spot has a system fault. Its status lists the fault",
    "ShorePowerConnectedError": "Spot is on its charger. Unplug it first",
    "NotPoweredOnError": "Spot's motors are off. Power on + stand first",
    "DockedError": "Spot is docked. Undock it first",
    "NoTimeSyncError": "time sync with Spot was lost. Disconnect, then Connect again",
    "ExpiredError": "the command reached Spot after its time. Disconnect, then Connect again to resync clocks",
    "TooDistantError": "the command's end time is too far ahead. Disconnect, then Connect again to resync clocks",
    "InvalidRequestError": "Spot refused the command as built. Tell the Spot lane; check_plan.py should have "
                           "caught it",
    "TimedOutError": "Spot did not answer in time. Check the cable and network. An E-Stop at the same moment "
                     "also does this",
    "CommandTimedOutError": "Spot did not finish in time. An E-Stop at the same moment also does this",
    "LicenseError": "Spot's licence does not allow this",
}


def explain(e):
    """An SDK error with what it means here and what to do; any other error as it is."""
    for cls in type(e).__mro__:
        if cls.__name__ in ADVICE:
            return f"{e} -> {ADVICE[cls.__name__]}"
    return str(e)
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
        self.stood = False              # set by stand(); a show needs Spot standing, not just powered
        self._carry = {}
        self.last_beat = time.monotonic()
        self.released = False
        estop_client = self.robot.ensure_client(EstopClient.default_service_name)
        self._refuse_live_estop(estop_client)
        endpoint = EstopEndpoint(estop_client, name=name, estop_timeout=ESTOP_TIMEOUT_S)
        endpoint.force_simple_setup()   # this session becomes Spot's only E-Stop
        self._endpoint = endpoint
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
        self.stood = False              # settle_then_cut sits Spot, then cuts power
        threading.Thread(target=self.estop.settle_then_cut, daemon=True).start()
        self.log(f"E-STOP: {why}")

    def release(self):
        # A stale heartbeat ends the SDK's check-in thread for good (keep_running_cb), so Spot cuts even if
        # the stop RPC is lost. Releasing again needs a new one, or the endpoint goes stale and Spot's
        # E-Stop autopolicy holds the motors off (KeepaliveMotorsOffError on every power-on).
        if not self.estop._thread.is_alive():
            self.estop = EstopKeepAlive(self._endpoint, keep_running_cb=self._person_alive)
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
        if not self.robot.is_powered_on():
            self.stood = False
        self.robot.power_on(timeout_sec=20)
        return "motors on, lease held (Spot stays where it is)"

    def stand(self):
        self.power_on()
        blocking_stand(self.cmd, timeout_sec=10)
        self.stood = True
        return "standing, motors on, lease held"

    def gripper(self, fraction):
        self._need_released()
        self._need_powered()
        self._ensure_lease()
        self.cmd.robot_command(RobotCommandBuilder.claw_gripper_open_fraction_command(fraction))
        return f"gripper to {fraction:.0%} open"

    def sit(self):
        self.stood = False
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
        """From what this session did, with no call to Spot: the operator page asks it four times a second,
        on the same loop that takes the heartbeat. send_window asks Spot itself."""
        return self.lease is not None and self.stood

    # Playback (player.py, spot_playback.py). ENCORE's limits for Spot are placeholders, so a real Spot plays
    # only plans inside the player's own first-run envelope: feet planted, small body offsets, slow joints.
    @staticmethod
    def plan_refusal(plan):
        problems = envelope_problems(plan)
        if problems:
            return "outside the first-run envelope: " + "; ".join(problems[:4]) + \
                (f"; and {len(problems) - 4} more" if len(problems) > 4 else "")
        return None

    def send_window(self, commands, t0_wall):
        """One window's commands (BODY, ARM, GRIP) as one synchronized command, sent ahead of its start."""
        self._need_released()
        self._need_powered()
        if self.lease is None:
            raise RuntimeError("refused: no lease held; Power on + stand first")
        if commands[0]["window"] == 0:
            self._carry = {}
        now = time.time()
        cmd = build_window(commands, t0_wall, now, self._carry)
        problems = command_problems(cmd, t0_wall, now)
        if problems:
            raise RuntimeError("refused before sending: " + "; ".join(problems))
        cmd_id = self.cmd.robot_command(cmd, timesync_endpoint=self.robot.time_sync.endpoint)  # to Spot's clock
        threading.Thread(target=self._arm_planner, args=(cmd_id, commands[0]["window"]), daemon=True).start()

    def _arm_planner(self, cmd_id, window):
        """Say when Spot's arm planner could not follow a window: it then keeps within its own speed and
        acceleration limits (MODIFIED) and the arm lags the beat, or it goes to the first point (FAILED)."""
        time.sleep(0.3)
        try:
            fb = self.cmd.robot_command_feedback(cmd_id).feedback
            ps = fb.synchronized_feedback.arm_command_feedback.arm_joint_move_feedback.planner_status
        except Exception as e:
            self.log(f"window {window}: arm feedback unavailable: {explain(e)}")
            return
        name = arm_command_pb2.ArmJointMoveCommand.Feedback.PlannerStatus.Name(ps)
        if name == "PLANNER_STATUS_MODIFIED":
            self.log(f"window {window}: the arm cannot keep up, so it lags the beat (planner MODIFIED). "
                     "Play it at a lower speed")
        elif name == "PLANNER_STATUS_FAILED":
            self.log(f"window {window}: the arm planner FAILED and goes to the window's first point")

    def after_show(self):
        """The show's over: stow the arm (the show ends there already) so Sit and walking work as usual."""
        self.cmd.robot_command(RobotCommandBuilder.arm_stow_command())
        return "arm stowed"

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
