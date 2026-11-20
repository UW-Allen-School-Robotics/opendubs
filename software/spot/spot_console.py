"""OpenDubs Spot console: one window that holds Spot's login for the session, is the person's E-Stop,
sends simple commands, and loads ENCORE plans (playing them comes next).

    uv run --no-project --python 3.12 --with 'bosdyn-client==5.1.4' python software/spot/spot_console.py

- Connect reads the Keychain login once (macOS asks you to allow it) and keeps the session in memory;
  the SDK refreshes the token hourly. Nothing is written to disk.
- The big red STOP, or the SPACE BAR while this window is in front, stops Spot: it sits, then power is
  cut. The E-Stop starts STOPPED; only a click on Release E-Stop, in this window, releases it.
- If this window freezes for over a second, it stops Spot. If the program dies, its check-ins end and
  Spot stops within the E-Stop timeout.
- Power on + stand holds the lease until Sit + power off (Spot 5.1.5 sits by itself once the lease is
  returned). The lease is acquired only when nobody holds it, never taken from the tablet.
- Spot's functions are listed once, in COMMANDS: each is a widget in the window and a name on the socket.
- Other local programs (an agent session, a show player) can send `status`, `stop` or any COMMANDS name
  on a private socket, ~/.opendubs-spot/console.sock (mode 0600): one command per line, one JSON reply.
  Releasing the E-Stop is never accepted there.
"""
import json
import os
import queue
import socket
import socketserver
import threading
import time
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable
from tkinter import filedialog, ttk

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
FREEZE_STOP_S = 1.0                     # the window not ticking this long stops Spot
SOCK_DIR = os.path.expanduser("~/.opendubs-spot")
SOCK_PATH = os.path.join(SOCK_DIR, "console.sock")


class SpotSession:
    """Spot's login, the person's E-Stop and the lease, for one console session."""

    def __init__(self, host, log):
        self.log = log
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
        self.last_tick = time.monotonic()
        self.released = False
        endpoint = EstopEndpoint(self.robot.ensure_client(EstopClient.default_service_name),
                                 name="opendubs-console-estop", estop_timeout=ESTOP_TIMEOUT_S)
        endpoint.force_simple_setup()   # the console becomes Spot's only E-Stop
        self.estop = EstopKeepAlive(endpoint, keep_running_cb=self._window_alive)
        self.stop("connected")
        self._watch = threading.Thread(target=self._watchdog, daemon=True)
        self._watch.start()

    # The E-Stop. stop() returns at once; the check-in goes on its own thread.
    def stop(self, why):
        self.released = False
        threading.Thread(target=self.estop.settle_then_cut, daemon=True).start()
        self.log(f"E-STOP: {why}")

    def release(self):
        self.estop.allow()
        self.released = True
        self.log("E-Stop released by the person at the console")

    def _window_alive(self):
        return time.monotonic() - self.last_tick < 3 * FREEZE_STOP_S

    def _watchdog(self):
        while True:
            time.sleep(0.2)
            if self.released and time.monotonic() - self.last_tick > FREEZE_STOP_S:
                self.stop("the console window stopped responding")

    # Commands: blocking, so they run on the console's command thread. Each one is listed in COMMANDS.
    def _need_released(self):
        if not self.released:
            raise RuntimeError("refused: the E-Stop is STOPPED; release it in the console first")

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

    def status(self):
        s = self.state_client.get_robot_state()
        ps = s.power_state
        return {
            "motors": ps.MotorPowerState.Name(ps.motor_power_state).removeprefix("STATE_"),
            "battery_pct": round(s.battery_states[0].charge_percentage.value) if s.battery_states else None,
            "estop": "RELEASED" if self.released else "STOPPED",
            "estop_robot": [f"{e.name}:{e.State.Name(e.state).removeprefix('STATE_')}" for e in s.estop_states],
            "lease": "held by the console" if self.lease else "not held",
            "faults": [f.name for f in s.system_fault_state.faults]
                      + [f.Cause.Name(f.cause) for f in s.behavior_fault_state.faults],
        }

    def close(self):
        self.stop("console closing")
        try:
            self.sit()
        except Exception as e:
            self.log(f"sit on close failed: {e}")
        time.sleep(0.5)
        self.estop.shutdown()
        self.tokens.stop()


@dataclass(frozen=True)
class Command:
    """One of Spot's functions: a widget in the console and a name on the socket. Add new ones (standing
    height, body pose, ...) to COMMANDS. `widget` says how the console shows it: "button" today; a
    "slider" kind (one value between bounds, e.g. a standing height) is the planned next one."""
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


def summarize_plan(plan):
    """A few lines about an ENCORE spot_driver.plan() output."""
    cmds = plan.get("commands", [])
    parts = {}
    for c in cmds:
        parts[c["part"]] = parts.get(c["part"], 0) + 1
    end = max((c["show_start_s"] + c["points"][-1]["time_since_reference"]
               for c in cmds if c.get("points")), default=0.0)
    lines = [f"robot {plan.get('robot')}, driver {plan.get('driver')}",
             f"{plan.get('windows')} windows of {plan.get('window_s')} s, lead {plan.get('lead_s')} s, "
             f"about {end:.1f} s long",
             "commands: " + (", ".join(f"{p} {n}" for p, n in sorted(parts.items())) or "none")]
    for p in plan.get("problems", [])[:8]:
        lines.append(f"problem: {p}")
    return "\n".join(lines)


class Console:
    def __init__(self, root):
        self.root = root
        self.session = None
        self.work = ThreadPoolExecutor(max_workers=1)       # one robot command at a time
        self.events = queue.Queue()
        self.plan = None
        self.owns_socket = False
        root.title("OpenDubs Spot console")
        root.geometry("640x860")
        root.protocol("WM_DELETE_WINDOW", self.quit)

        top = ttk.Frame(root, padding=8)
        top.pack(fill="x")
        ttk.Label(top, text="Spot").pack(side="left")
        self.host = tk.StringVar(value=DEFAULT_HOST)
        ttk.Entry(top, textvariable=self.host, width=16, takefocus=0).pack(side="left", padx=4)
        self.connect_btn = self._button(top, "Connect", self.connect)
        self.connect_btn.pack(side="left")
        self.conn_label = ttk.Label(top, text="not connected")
        self.conn_label.pack(side="left", padx=8)

        self.stop_btn = tk.Label(root, text="STOP\n(space bar)", bg="#c40000", fg="white",
                                 font=("Helvetica", 40, "bold"), height=3, cursor="hand2")
        self.stop_btn.pack(fill="x", padx=8, pady=4)
        self.stop_btn.bind("<Button-1>", lambda e: self.stop("STOP button"))
        self.estop_label = tk.Label(root, text="E-Stop: not connected", font=("Helvetica", 16, "bold"))
        self.estop_label.pack(fill="x")

        row = ttk.Frame(root, padding=(8, 4))
        row.pack(fill="x")
        self.release_btn = self._button(row, "Release E-Stop", self.release)
        self.release_btn.pack(side="left")
        self.cmd_buttons, groups = [], {}
        for c in COMMANDS:                       # one frame per group, one widget per command
            if c.group not in groups:
                groups[c.group] = ttk.LabelFrame(root, text=c.group, padding=6)
                groups[c.group].pack(fill="x", padx=8, pady=2)
            b = self._button(groups[c.group], c.label, lambda c=c: self.run(c.name))
            b.pack(side="left", padx=4)
            self.cmd_buttons.append(b)

        show = ttk.LabelFrame(root, text="ENCORE show", padding=8)
        show.pack(fill="x", padx=8, pady=4)
        srow = ttk.Frame(show)
        srow.pack(fill="x")
        self._button(srow, "Load plan (JSON)...", self.load_plan).pack(side="left")
        self.play_btn = self._button(srow, "Play (not built yet)", lambda: None)
        self.play_btn.state(["disabled"])
        self.play_btn.pack(side="left", padx=8)
        self.plan_text = tk.Text(show, height=6, width=70, takefocus=0)
        self.plan_text.pack(fill="x", pady=4)

        stat = ttk.LabelFrame(root, text="Status", padding=8)
        stat.pack(fill="x", padx=8, pady=4)
        self.status_label = ttk.Label(stat, text="-", justify="left")
        self.status_label.pack(anchor="w")

        logf = ttk.LabelFrame(root, text="Log", padding=8)
        logf.pack(fill="both", expand=True, padx=8, pady=4)
        self.log_text = tk.Text(logf, height=10, takefocus=0)
        self.log_text.pack(fill="both", expand=True)

        # Space stops, wherever the focus is, and never presses a focused button.
        for cls in ("TButton", "Button", "Text", "TEntry", "Entry"):
            root.unbind_class(cls, "<space>")
        root.bind_all("<space>", lambda e: (self.stop("space bar"), "break")[1])
        self._set_connected(False)
        self.root.after(100, self.tick)
        self.root.after(1000, self.poll_status)
        threading.Thread(target=self.serve_socket, daemon=True).start()

    def _button(self, parent, text, cmd):
        return ttk.Button(parent, text=text, command=cmd, takefocus=0)

    def log(self, msg):
        self.events.put(("log", f"{time.strftime('%H:%M:%S')} {msg}"))

    def tick(self):
        if self.session:
            self.session.last_tick = time.monotonic()
        while not self.events.empty():
            kind, payload = self.events.get()
            if kind == "log":
                self.log_text.insert("end", payload + "\n")
                self.log_text.see("end")
            elif kind == "status":
                self.status_label.config(text=payload)
            elif kind == "connected":
                self._set_connected(payload)
        self._show_estop()
        self.root.after(100, self.tick)

    def _show_estop(self):
        if not self.session:
            self.estop_label.config(text="E-Stop: not connected", bg=self.root.cget("bg"), fg="black")
        elif self.session.released:
            self.estop_label.config(text="E-Stop RELEASED: Spot may move", bg="#2e7d32", fg="white")
        else:
            self.estop_label.config(text="E-Stop STOPPED", bg="#c40000", fg="white")

    def _set_connected(self, on):
        for b in (self.release_btn, *self.cmd_buttons):
            b.state(["!disabled"] if on else ["disabled"])
        self.connect_btn.state(["disabled"] if on else ["!disabled"])
        self.conn_label.config(text=f"connected to {self.host.get()}" if on else "not connected")

    def connect(self):
        self.connect_btn.state(["disabled"])
        self.log(f"connecting to {self.host.get()} (allow the Keychain dialog)")

        def go():
            try:
                self.session = SpotSession(self.host.get(), self.log)
                self.events.put(("connected", True))
                self.log("connected; the console is Spot's E-Stop, STOPPED")
            except Exception as e:
                self.log(f"connect failed: {e}")
                self.events.put(("connected", False))
        self.work.submit(go)

    def stop(self, why):
        if self.session:
            self.session.stop(why)
        else:
            self.log(f"STOP ({why}): not connected")

    def release(self):
        if self.session:
            self.session.release()

    def run(self, name, reply=None):
        """Queue a command on the command thread; reply(dict) gets the result."""
        def go():
            try:
                if not self.session:
                    raise RuntimeError("not connected")
                out = {"ok": True, "result": BY_NAME[name].run(self.session)}
            except Exception as e:
                out = {"ok": False, "error": str(e)}
            self.log(f"{name}: {out.get('result') or out.get('error')}")
            if reply:
                reply(out)
        self.log(f"{name} ...")
        return self.work.submit(go)

    def poll_status(self):
        def go():
            try:
                s = self.session.status()
                text = (f"motors {s['motors']}   battery {s['battery_pct']} %   lease {s['lease']}\n"
                        f"robot e-stops: {', '.join(s['estop_robot'])}\n"
                        f"faults: {', '.join(s['faults']) or 'none'}")
            except Exception as e:
                text = f"status unavailable: {e}"
            self.events.put(("status", text))
        if self.session:
            threading.Thread(target=go, daemon=True).start()
        self.root.after(1000, self.poll_status)

    def load_plan(self):
        if self.session and self.session.released:
            # The file dialog pauses this window, so the space bar can't reach it: stop first.
            self.stop("opening the file dialog")
        exports = os.path.expanduser("~/.encore/exports")     # where ENCORE's push dry run writes them
        path = filedialog.askopenfilename(title="ENCORE Spot plan",
                                          initialdir=exports if os.path.isdir(exports) else None,
                                          filetypes=[("Spot plan", "*.spot-plan.json"), ("JSON", "*.json")])
        if not path:
            return
        try:
            with open(path) as f:
                self.plan = json.load(f)
            text = summarize_plan(self.plan)
        except Exception as e:
            self.plan, text = None, f"could not load {path}: {e}"
        self.plan_text.delete("1.0", "end")
        self.plan_text.insert("end", text)
        self.log(f"plan loaded: {os.path.basename(path)}" if self.plan else text)

    def serve_socket(self):
        """status / stand / sit / stop for local programs. Never release."""
        console = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                for raw in self.rfile:
                    name = raw.decode(errors="replace").strip().lower()
                    if name == "stop":
                        console.stop("socket")
                        out = {"ok": True, "result": "stopped"}
                    elif name == "status" and not console.session:
                        out = {"ok": False, "error": "not connected"}
                    elif name == "status":
                        try:
                            out = {"ok": True, "result": console.session.status()}
                        except Exception as e:
                            out = {"ok": False, "error": str(e)}
                    elif name in BY_NAME:
                        done = threading.Event()
                        box = {}
                        console.run(name, reply=lambda o: (box.update(o), done.set()))
                        done.wait(60)
                        out = box or {"ok": False, "error": "timed out"}
                    else:
                        out = {"ok": False, "error": f"unknown or refused command {name!r}; allowed: "
                               + ", ".join(["status", "stop", *BY_NAME])}
                    self.wfile.write((json.dumps(out) + "\n").encode())

        os.makedirs(SOCK_DIR, mode=0o700, exist_ok=True)
        os.chmod(SOCK_DIR, 0o700)
        if os.path.exists(SOCK_PATH):
            probe = socket.socket(socket.AF_UNIX)
            try:
                probe.connect(SOCK_PATH)
                self.log(f"another console already serves {SOCK_PATH}; this one has no socket")
                return
            except OSError:
                os.unlink(SOCK_PATH)        # a stale socket from a console that died
            finally:
                probe.close()
        old = os.umask(0o177)
        try:
            server = socketserver.ThreadingUnixStreamServer(SOCK_PATH, Handler)
        finally:
            os.umask(old)
        self.owns_socket = True
        self.log(f"commands accepted on {SOCK_PATH}")
        server.serve_forever()

    def quit(self):
        if self.session:
            self.log("closing: stopping Spot and returning the lease")
            self.session.close()
        if self.owns_socket and os.path.exists(SOCK_PATH):
            os.unlink(SOCK_PATH)
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    Console(root)
    root.mainloop()
