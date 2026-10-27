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
        self.received, self.until, self.last, self.supervised = [], 0.0, "none", False
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
            return (f"{self.side} arm, mujoco: {'park running' if running else 'held'}; last: {self.last}\njoints (deg) +0.0 +0.0 +0.0 +0.0 +0.0 +0.0 +0.0"
                    f"\nsupervised: {'yes' if self.supervised else 'no'}")
        if self.refusal:
            return f"refused: {self.refusal}"
        if command in ("park", "activate"):
            self.until = time.time() + self.busy
            return f"{command} started"
        if command in ("supervise", "unsupervise"):
            self.supervised = command == "supervise"
            return command + "d"
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
    assert openarm.start_op("park", "right")[0]
    op = wait()
    assert op["rc"] == 1 and "does not answer" in op["error"], op
    ok, detail = openarm.start_op("hold", "right")      # an instant one answers in the request itself
    assert not ok and "does not answer" in detail, (ok, detail)


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


def test_supervise_says_so_to_the_service(arms):
    right = arms("right")
    assert openarm.start_op("supervise", "right")[0]
    op = wait()
    assert op["rc"] == 0 and right.supervised and right.received == ["supervise"], (op, right.received)
    assert "supervised: yes" in openarm._service_status("right")


def test_hold_and_end_supervision_are_not_kept_waiting_by_a_running_move(arms):
    """A HOLD refused as 'busy' while a park runs would be the one button that must always work; ending supervision only takes capability away."""
    right = arms("right", busy=3.0)
    right.supervised = True
    assert openarm.start_op("park", "right")[0]
    assert openarm.op_status()["running"]
    end = time.time() + 5                       # start_op returns once its thread is started, not once the park has reached the service: a hold sent first reads as the first command
    while "park" not in right.received and time.time() < end:
        time.sleep(0.01)
    ok, detail = openarm.start_op("hold", "right")
    assert ok and "holding" in detail, (ok, detail)
    ok, detail = openarm.start_op("unsupervise", "right")
    assert ok and not right.supervised, (ok, detail)
    assert right.received[:3] == ["park", "hold", "unsupervise"], right.received
    assert not openarm.start_op("supervise", "right")[0], "attesting is an operation like any other and waits for the move"
    assert not openarm.start_op("activate", "right")[0], "a second move must still be refused while one runs"
    wait()


def test_a_hold_with_no_service_says_so_at_once(arms):
    ok, detail = openarm.start_op("hold", "right")
    assert not ok and "no arm service" in detail


def test_effector_status_reports_each_arm_from_its_service(arms):
    arms("right").supervised = True
    eff = openarm.effector_status()
    assert eff["arm_right"]["state"] == "ok" and eff["arm_right"]["pose_deg"] == [0.0] * 7, eff["arm_right"]
    assert eff["arm_left"]["state"] == "down" and "no arm service for the left arm" in eff["arm_left"]["why"], eff["arm_left"]
    for side in ("left", "right"):
        assert eff[f"grip_{side}"]["state"] == "down" and eff[f"grip_{side}"]["excluded"], "a gripper nobody enabled must not read ok"


def test_an_arm_that_is_up_but_not_supervised_is_not_ok_and_says_how_to_fix_it(arms):
    """A show is a trajectory goal per arm and the service rejects it without supervision, which ends the show: so the runner must be told before it starts."""
    right = arms("right")
    eff = openarm.effector_status()["arm_right"]
    assert eff["state"] == "degraded" and eff["pose_deg"] == [0.0] * 7 and "PREPARE R" in eff["why"], eff       # the pose stays: a skipped arm is checked to be at rest
    right.supervised = True
    assert openarm.effector_status()["arm_right"]["state"] == "ok"


def test_an_arm_whose_service_stopped_answering_is_down(arms):
    arms("right").stop()
    assert openarm.effector_status()["arm_right"]["state"] == "down"


def test_follow_goes_to_the_service_and_nowhere_else(arms):
    right = arms("right")
    assert openarm.start_op("follow", "right")[0]
    op = wait()
    assert op["error"] is None and right.received == ["follow"], (op, right.received)


def _free_port():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_viewer(side, state, seconds=15):
    end = time.time() + seconds
    while openarm.viewer_state()[side]["state"] != state and time.time() < end:
        time.sleep(0.1)
    return openarm.viewer_state()[side]


def test_a_port_that_is_not_the_dashboards_is_left_alone(monkeypatch):
    import socket
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(16)
    port = listener.getsockname()[1]
    monkeypatch.setitem(openarm.VIEWER_PORTS, "right", port)
    try:
        ok, detail = openarm.start_op("viewer", "right")
        assert not ok and f"port {port}" in detail and "left alone" in detail, detail
        ok, detail = openarm.start_op("viewer-stop", "right")
        assert ok and "not this dashboard's" in detail, detail
        assert openarm._port_open(port), "the other process's port was closed"
    finally:
        listener.close()


def test_a_viewer_starts_runs_and_stops_without_leaving_a_process(tmp_path, monkeypatch):
    port = _free_port()
    stub = tmp_path / "arm"
    stub.write_text('#!/bin/bash\nexec python3 -m http.server "$3" --bind 127.0.0.1\n')
    stub.chmod(0o755)
    monkeypatch.setattr(openarm, "ARM_CLI", str(stub))
    monkeypatch.setitem(openarm.VIEWER_PORTS, "left", port)
    try:
        assert openarm.start_op("viewer", "left")[0]
        assert _wait_viewer("left", "running")["state"] == "running" and openarm._port_open(port)
        assert openarm.start_op("viewer", "left")[0], "a second start is not a second process"
        assert openarm.start_op("viewer-stop", "left")[0]
        assert _wait_viewer("left", "stopped")["state"] == "stopped"
        end = time.time() + 5
        while openarm._port_open(port) and time.time() < end:
            time.sleep(0.1)
        assert not openarm._port_open(port), "the viewer's process outlived its stop"
    finally:
        openarm.viewer_stop("left")


def test_a_viewer_that_ends_at_once_says_why(tmp_path, monkeypatch):
    stub = tmp_path / "arm"
    stub.write_text("#!/bin/bash\necho 'gafro ABI mismatch' >&2\nexit 3\n")
    stub.chmod(0o755)
    monkeypatch.setattr(openarm, "ARM_CLI", str(stub))
    monkeypatch.setitem(openarm.VIEWER_PORTS, "right", _free_port())
    assert openarm.start_op("viewer", "right")[0]
    state = _wait_viewer("right", "failed")
    assert state["state"] == "failed" and "ABI mismatch" in state["error"], state


def test_prepare_supervises_then_activates_each_arm(arms, monkeypatch):
    monkeypatch.setattr(openarm, "_show_running", lambda: None)
    left, right = arms("left", busy=0.3), arms("right", busy=0.3)
    assert openarm.start_op("prepare", "both")[0]
    op = wait()
    assert op["error"] is None and op["rc"] == 0, op
    for fake in (left, right):
        assert [c for c in fake.received if c != "status"] == ["supervise", "activate"], fake.received
        assert fake.supervised


def test_prepare_stops_at_the_first_refusal_and_does_not_move_the_arm(arms, monkeypatch):
    monkeypatch.setattr(openarm, "_show_running", lambda: None)
    right = arms("right", refusal="no person at the e-stop")
    assert openarm.start_op("prepare", "right")[0]
    op = wait()
    assert op["error"] and "supervise did not go through" in op["error"] and "no person at the e-stop" in op["error"], op
    assert "activate" not in right.received, right.received


def test_prepare_waits_for_a_show_to_end(arms, monkeypatch):
    right = arms("right")
    monkeypatch.setattr(openarm, "_show_running", lambda: "shrug")
    ok, detail = openarm.start_op("prepare", "right")
    assert not ok and "shrug" in detail and right.received == [], (ok, detail, right.received)
