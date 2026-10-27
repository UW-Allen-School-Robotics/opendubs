"""The dashboard's arm operations go to the arm service over its socket, and nowhere else: no `openarm-up` for park, activate, hold or down, and no fallback when there is no
service. A fake service speaks the real protocol (arm_client.py). No robot, no ROS, no root."""
import json
import os
import socketserver
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import openarm  # noqa: E402  -- puts software/openarm/gafro on the path, as it does on the robot
import arm_client  # noqa: E402


class Fake:
    """What one arm service says: held, or running `busy` for a while; refuses everything with `refusal` if set."""

    def __init__(self, side, directory, refusal=None, busy=0.6, outcome="park done"):
        self.side, self.refusal, self.busy, self.outcome = side, refusal, busy, outcome
        self.received, self.until, self.last = [], 0.0, "none"
        self.path = os.path.join(directory, f"opendubs-arm-service-{side}.sock")
        fake = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                request = json.loads(self.rfile.readline())
                self.wfile.write((fake.answer(request) + "\n").encode())

        self.server = socketserver.ThreadingUnixStreamServer(self.path, Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def answer(self, request):
        command = request["command"]
        self.received.append(command)
        running = time.time() < self.until
        if command == "status":
            if not running and self.until:
                self.last = self.outcome
            return f"{self.side} arm, mujoco: {'park running' if running else 'held'}; last: {self.last}\njoints (deg) +0.0 +0.0 +0.0 +0.0 +0.0 +0.0 +0.0"
        if self.refusal:
            return f"refused: {self.refusal}"
        if command in ("park", "activate"):
            self.until = time.time() + self.busy
            return f"{command} started"
        return "holding" if command == "hold" else "down"

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def arms(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENDUBS_ARM_SOCKET_DIR", str(tmp_path))
    monkeypatch.setattr(openarm, "_run", lambda *a, **k: (_ for _ in ()).throw(AssertionError(f"the old stack was called: {a}")))   # openarm-up must not run
    made = []

    def make(side, **kwargs):
        made.append(Fake(side, str(tmp_path), **kwargs))
        return made[-1]

    yield make
    for fake in made:
        fake.stop()


def wait():
    end = time.time() + 20
    while openarm.op_status()["running"] and time.time() < end:
        time.sleep(0.1)
    return openarm.op_status()


def test_park_goes_to_the_service_and_waits_for_it(arms):
    right = arms("right", busy=1.0)
    assert openarm.start_op("park", "right")[0]
    op = openarm.op_status()
    assert op["running"], "a 1 s park was reported done at once"
    op = wait()
    assert op["error"] is None and op["rc"] == 0 and "park done" in op["out"], op
    assert right.received[0] == "park"


def test_both_means_every_arm_that_has_a_service(arms):
    right = arms("right")
    assert openarm.start_op("hold", "both")[0]
    op = wait()
    assert op["error"] is None and right.received == ["hold"], (op, right.received)


def test_no_service_is_a_refusal_and_not_a_fallback(arms):
    arms("right")
    ok, detail = openarm.start_op("park", "left")
    assert not ok and "no arm service for the left arm" in detail and "right" in detail
    assert not openarm.start_op("up", "right")[0], "`up` started the old stack; it does not exist any more"


def test_nothing_served_at_all(arms):
    ok, detail = openarm.start_op("down", "both")
    assert not ok and "Running: none" in detail


def test_the_services_refusal_reaches_the_operator(arms):
    right = arms("right", refusal="a move needs --supervised: a person at the e-stop")
    assert openarm.start_op("activate", "right")[0]
    op = wait()
    assert op["rc"] == 1 and "--supervised" in op["error"] and op["error"].startswith("right:"), op
    assert right.received == ["activate"]


def test_a_move_that_stops_is_an_error(arms):
    arms("right", busy=0.5, outcome="park stopped: TRACKING: a joint is 0.20 rad from the path")
    assert openarm.start_op("park", "right")[0]
    op = wait()
    assert op["rc"] == 1 and "TRACKING" in op["error"], op


def test_a_dead_service_with_a_stale_socket(arms):
    arms("right").stop()                      # the socket file stays, nothing answers
    assert openarm.start_op("hold", "right")[0]
    op = wait()
    assert op["rc"] == 1 and "does not answer" in op["error"], op


def test_the_snapshot_shows_each_service(arms, monkeypatch):
    arms("right")
    monkeypatch.setattr(openarm, "_motor_summary", lambda: {"text": "", "error": None, "at": 0})
    monkeypatch.setattr(openarm, "link_status", lambda: None)
    monkeypatch.setattr(openarm, "_teleop_running", lambda: None)
    monkeypatch.setattr(openarm, "_journal_tail", lambda: None)
    monkeypatch.setattr(openarm, "_port_open", lambda port: False)
    service = openarm.snapshot()["service"]
    assert list(service) == ["right"] and "held" in service["right"]


class Unit:
    """`systemctl` as the dashboard calls it: a start brings the fake service up (or does not), `show` reports the unit's state."""

    def __init__(self, arms, side, comes_up=True, rc=0, stderr="", state=None):
        self.arms, self.side, self.comes_up, self.rc, self.stderr, self.state, self.calls = arms, side, comes_up, rc, stderr, state or {}, []

    def __call__(self, argv, timeout):
        import subprocess

        self.calls.append(list(argv))
        if "start" in argv:
            if self.rc == 0 and self.comes_up:
                self.arms(self.side)
            return subprocess.CompletedProcess(argv, self.rc, "", self.stderr)
        if "show" in argv:
            return subprocess.CompletedProcess(argv, 0, "".join(f"{k}={v}\n" for k, v in self.state.items()), "")
        raise AssertionError(f"unexpected command {argv}")


def test_start_runs_exactly_what_the_sudoers_rule_allows_and_waits_for_the_service(arms, monkeypatch):
    unit = Unit(arms, "right")
    monkeypatch.setattr(openarm, "_run", unit)
    assert openarm.start_op("start", "right")[0]
    op = wait()
    assert op["rc"] == 0 and op["error"] is None and "started" in op["out"], op
    assert unit.calls[0] == ["sudo", "-n", "/usr/bin/systemctl", "start", "opendubs-arm@right.service"], unit.calls[0]


def test_start_does_nothing_for_a_service_already_up(arms, monkeypatch):
    arms("right")
    unit = Unit(arms, "right")
    monkeypatch.setattr(openarm, "_run", unit)
    assert openarm.start_op("start", "right")[0]
    op = wait()
    assert op["rc"] == 0 and "already running" in op["out"] and unit.calls == [], (op, unit.calls)


def test_start_is_per_arm(arms, monkeypatch):
    monkeypatch.setattr(openarm, "_run", Unit(arms, "right"))
    ok, detail = openarm.start_op("start", "both")
    assert not ok and "per arm" in detail


def test_a_refused_sudo_says_which_rule(arms, monkeypatch):
    monkeypatch.setattr(openarm, "_run", Unit(arms, "right", rc=1, stderr="sudo: a password is required"))
    assert openarm.start_op("start", "right")[0]
    op = wait()
    assert op["rc"] == 1 and "password is required" in op["error"] and "opendubs-arm.sudoers" in op["error"], op


def test_a_unit_that_ends_before_it_answers_is_reported_at_once(arms, monkeypatch):
    unit = Unit(arms, "right", comes_up=False, state={"ActiveState": "failed", "Result": "exit-code", "ExecMainStatus": "2"})
    monkeypatch.setattr(openarm, "_run", unit)
    began = time.time()
    assert openarm.start_op("start", "right")[0]
    op = wait()
    assert op["rc"] == 1 and "exit status 2" in op["error"] and "old arm stack" in op["error"], op
    assert time.time() - began < 10, "it waited out the whole timeout for a unit that had already failed"


def test_there_is_no_stop(arms):
    assert not openarm.start_op("stop", "right")[0], "`systemctl stop` would end the service without the not-at-stow refusal that `down` has"
