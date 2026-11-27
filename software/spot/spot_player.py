"""OpenDubs Spot player: holds Spot's session, keeps ENCORE plans, plays them, and serves the operator page.
It runs beside Spot (the Orin or a Pi on the payload network, or the Mac on its cable), as the Thor's
runner does for opendubs2; the operator page runs on any browser, such as a tablet.

    cd software/spot
    uv run --no-project --python 3.12 --with 'bosdyn-client==5.1.4' --with fastapi \
        --with 'uvicorn[standard]' python spot_player.py [--fake] [--spot HOST] [--listen ADDR] [--port N]

  --fake      no robot: fake_spot.py stands in, for developing shows and the page with Spot off.
  --walking   the walking envelope: shows with BASE play, under its caps. Without it the feet stay
              planted. Only with the owner's word at Spot.
  --live      serve /live on port 8100, where a stream of velocity requests drives Spot once the operator
              switches Live on (live.py). Only with the owner's word at Spot.
  --listen    127.0.0.1 by default (this machine only); 0.0.0.0 on the Orin, so a tablet can reach it.

The E-Stop is the person's, on the operator page:
- The page claims the operator role with the pairing code this program prints at start. One operator
  at a time; a new claim takes over, and Spot is stopped first.
- The operator page beats every 200 ms. No beat for a second, a closed or hidden page, or a dropped
  Wi-Fi link stops Spot (sit, then cut power). If this program dies, Spot stops within the E-Stop timeout.
- STOP and the space bar on any open page stop Spot. Only the operator page can release the E-Stop.

The player interface (agreed with ENCORE), JSON over HTTP:
  GET  /api/describe        what this player is and what it accepts
  GET  /api/plans           stored plans
  POST /api/load            {"name": ..., "plan": {...}} stores and loads; {"name": ...} loads a stored one
  GET  /api/ready           {"ok": bool, "problems": [...]}
  POST /api/start           {"at": wall-clock seconds}; accepted only while the operator has ARMED the show
  POST /api/stop            {"why": ...}: the E-Stop (sit, then cut)
  GET  /api/status          everything the page shows, and "now": this player's clock (time.time())
The interface never releases the E-Stop, never arms a show and never sends Spot a command.

For ENCORE's Connect (notes/design/spot-encore-only.md): with --login-stdin the player reads Spot's login
as two lines on stdin and prints ENCORE-LOGIN-OK or ENCORE-LOGIN-REFUSED <error>. Once it takes
connections it prints ENCORE-PAIRING <code>, and it exits when stdin closes, so Spot sits. /ws accepts
the Origins http://127.0.0.1 and http://localhost on any port, this player's own page and any
--allow-origin, and refuses the rest. /api/describe carries the player's commit.
"""
import argparse
import asyncio
import collections
import json
import os
import re
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

import uvicorn
from fastapi import Body, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

import live as live_mode
import spot_credentials
from player import Playback
from spot_playback import envelope_name, use_envelope
from spot_session import BY_NAME, COMMANDS, DEFAULT_HOST, explain, plan_length_s

HERE = os.path.dirname(os.path.abspath(__file__))
INTERFACE_VERSION = 1
ORIGIN_LOCAL = re.compile(r"http://(127\.0\.0\.1|localhost)(:\d+)?")


def _commit():
    """The commit this player came from: COMMIT, which orin/sync.sh writes, or git in a checkout."""
    try:
        return open(os.path.join(HERE, "COMMIT")).read().strip()
    except OSError:
        try:
            return subprocess.run(["git", "-C", HERE, "rev-parse", "--short", "HEAD"], capture_output=True,
                                  text=True, timeout=3).stdout.strip() or None
        except (OSError, subprocess.SubprocessError):
            return None


COMMIT = _commit()


class Player:
    def __init__(self, args):
        self.args = args
        self.session = None
        self.connecting = False
        self.status = {}
        self.lines = collections.deque(maxlen=300)
        self.seq = 0
        self.lock = threading.Lock()
        self.work = ThreadPoolExecutor(max_workers=1)        # one robot command at a time
        self.playback = Playback(self.log)
        if args.fake:
            from fake_spot import FakeSession as Session
        else:
            from spot_session import SpotSession as Session
        self.Session = Session
        self._shows = {}                                     # name -> (mtime, summary of the plan)
        self.code = f"{secrets.randbelow(10**6):06d}"
        self.origins = {f"http://{args.listen}:{args.port}", *getattr(args, "allow_origin", [])}   # also allowed on /ws
        self.operator_token = None
        self.operator_ws = None
        os.makedirs(args.plans, exist_ok=True)
        self.live = live_mode.Live(self, args.live_log, args.live_padding) if args.live else None
        threading.Thread(target=self._poll_status, daemon=True).start()

    def log(self, msg):
        with self.lock:
            self.seq += 1
            self.lines.append((self.seq, f"{time.strftime('%H:%M:%S')} {msg}"))
        print(msg, flush=True)

    # The E-Stop and the session
    def stop(self, why):
        if self.live:
            self.live.switch(False, f"STOP: {why}")
        if self.session:
            self.session.stop(why)
        else:
            self.log(f"STOP ({why}): not connected")
        self.playback.stopped(why)

    def beat(self):
        if self.session:
            self.session.last_beat = time.monotonic()

    def connect(self, host):
        if self.session or self.connecting:
            raise RuntimeError("already connected")
        self.connecting = True

        def go():
            try:
                if not self.args.fake:
                    self.log(f"connecting to {host} (allow the Keychain dialog on this machine)")
                s = self.Session(host, self.log, name="opendubs-player-estop", heartbeat="the operator page")
                s.last_beat = time.monotonic()
                self.session = s
                self.log("connected; the operator page is Spot's E-Stop, STOPPED")
            except Exception as e:
                self.log(f"connect failed: {explain(e)}")
            finally:
                self.connecting = False
        self.work.submit(go)

    def disconnect(self):
        s, self.session = self.session, None
        if s:
            self.playback.stopped("disconnected")
            self.work.submit(s.close)
            self.log("disconnecting: Spot sits, the lease is returned")

    def run(self, name):
        def go():
            try:
                if not self.session:
                    raise RuntimeError("not connected")
                self.log(f"{name}: {BY_NAME[name].run(self.session)}")
            except Exception as e:
                self.log(f"{name}: {explain(e)}")
        self.log(f"{name} ...")
        self.work.submit(go)

    def _poll_status(self):
        while True:
            time.sleep(1.0)
            s = self.session
            try:
                self.status = s.status() if s else {}
            except Exception as e:
                self.status = {"error": f"status unavailable: {e}"}

    # Plans
    def plan_names(self):
        return sorted(f for f in os.listdir(self.args.plans) if f.endswith(".json"))

    def shows(self):
        """The stored plans as the page lists them: name, length, the parts they move, and whether they
        can run on this player (the reason when not). Robot state (E-Stop, standing) is not counted here."""
        out = []
        for name in self.plan_names():
            path = os.path.join(self.args.plans, name)
            mtime = os.path.getmtime(path)
            cached = self._shows.get(name)
            if not cached or cached[0] != mtime:
                try:
                    with open(path) as f:
                        plan = json.load(f)
                    why = []
                    if plan.get("robot") != "spot":
                        why.append(f"for robot {plan.get('robot')!r}, not Spot")
                    why += [f"plan problem: {p}" for p in plan.get("problems", [])]
                    refusal = self.Session.plan_refusal(plan)
                    if refusal:
                        why.append(refusal)
                    info = {"length_s": round(plan_length_s(plan), 1),
                            "parts": sorted({c.get("part") for c in plan.get("commands", [])}), "why": why}
                except Exception as e:
                    info = {"length_s": None, "parts": [], "why": [f"can't read it: {e}"]}
                label = re.sub(r"(\.spot-plan)?\.json$", "", name)
                cached = (mtime, {"name": name, "label": label, **info, "can_run": not info["why"]})
                self._shows[name] = cached
            out.append(cached[1])
        return out

    def load(self, name, plan=None):
        name = os.path.basename(name or "")
        if not re.fullmatch(r"[\w.-]+\.json", name):
            raise RuntimeError("refused: a plan name is letters, digits, . _ - and ends in .json")
        path = os.path.join(self.args.plans, name)
        given = plan is not None
        if not given:
            with open(path) as f:
                plan = json.load(f)
        out = self.playback.load(name, plan)                 # validates before anything is stored
        if given:
            with open(path, "w") as f:
                json.dump(plan, f)
        return out

    def state(self, operator=False, since=0):
        s = self.session
        with self.lock:
            lines = [l for l in self.lines if l[0] > since]
        return {"connected": bool(s), "connecting": self.connecting, "fake": self.args.fake,
                "host": getattr(s, "host", self.args.spot) if s else self.args.spot,
                "estop": ("RELEASED" if s.released else "STOPPED") if s else "NOT CONNECTED",
                "status": self.status, "playback": self.playback.describe(),
                "problems": self.playback.problems(s), "shows": self.shows(),
                "commands": [{"name": c.name, "label": c.label, "group": c.group, "widget": c.widget}
                             for c in COMMANDS],
                "live": self.live.describe() if self.live else None,
                "operator": operator, "operator_present": self.operator_ws is not None,
                "log": lines, "seq": self.seq}


def make_app(p):
    @asynccontextmanager
    async def lifespan(app):
        # Printed once the server takes connections, so it also tells ENCORE the player is ready:
        # the pairing code on one line for ENCORE to read.
        print(f"ENCORE-PAIRING {p.code}", flush=True)
        yield
        if p.session:
            p.log("player closing: Spot sits, the lease is returned")
            p.session.close()

    app = FastAPI(lifespan=lifespan)

    def fail(e):
        raise HTTPException(status_code=409, detail=str(e))

    @app.get("/api/describe")
    def describe():
        return {"robot": "spot", "player": "opendubs-spot-player", "interface": INTERFACE_VERSION, "commit": COMMIT,
                "fake": p.args.fake, "parts": ["BASE", "BODY", "ARM", "GRIP"], "envelope": envelope_name(),
                "live": {"port": p.args.live_port, "path": "/live", "end_s": live_mode.END_S,
                         "limits": p.live.describe()["limits"]} if p.live else None,
                "states": ["EMPTY", "LOADED", "ARMED", "PLAYING", "DONE", "STOPPED"],
                "start": "accepted only while the operator has ARMED the show; at is wall-clock seconds",
                "never": ["release the E-Stop", "arm a show", "send Spot a command"]}

    @app.get("/api/plans")
    def plans():
        return {"plans": p.shows()}

    @app.post("/api/load")
    def load(body: dict = Body(...)):
        try:
            return p.load(body.get("name"), body.get("plan"))
        except Exception as e:
            fail(e)

    @app.get("/api/ready")
    def ready():
        problems = p.playback.problems(p.session)
        return {"ok": not problems, "problems": problems, "state": p.playback.state}

    @app.post("/api/start")
    def start(body: dict = Body(default={})):
        try:
            return p.playback.start(p.session, body.get("at"))
        except Exception as e:
            fail(e)

    @app.post("/api/stop")
    def stop(body: dict = Body(default={})):
        p.stop(f"interface: {body.get('why') or 'stop'}")
        return {"ok": True, "estop": "STOPPED"}

    @app.get("/api/status")
    def status():
        st = p.state()
        st.pop("log")
        st["now"] = time.time()         # this player's clock, so a client (ENCORE's song) can measure its offset
        return st

    @app.post("/api/claim")
    def claim(body: dict = Body(...)):
        if not secrets.compare_digest(str(body.get("code", "")), p.code):
            raise HTTPException(status_code=403, detail="wrong pairing code (it is printed where the player runs)")
        if p.session and p.session.released:
            p.stop("a new operator page took over")
        p.operator_token = secrets.token_urlsafe(24)
        p.log("a page claimed the operator role")
        return {"token": p.operator_token}

    @app.websocket("/ws")
    async def ws(sock: WebSocket):
        # A browser always sends its page's Origin. Accept this player's own page, ENCORE (127.0.0.1 or
        # localhost, any port, which is also the SSH tunnel) and any --allow-origin; refuse other sites,
        # so a web page elsewhere in the browser can't open the socket. Scripts send no Origin.
        origin = sock.headers.get("origin")
        if origin and not (ORIGIN_LOCAL.fullmatch(origin) or origin in p.origins):
            await sock.close(code=1008)
            return
        await sock.accept()
        token = sock.query_params.get("token")
        is_op = bool(token) and token == p.operator_token
        if is_op:
            if p.operator_ws is not None:
                p.stop("the operator page reconnected")
            p.operator_ws = sock
            p.beat()
        since = 0

        async def push():
            nonlocal since
            while True:
                op = is_op and p.operator_ws is sock and token == p.operator_token
                st = p.state(operator=op, since=since)
                since = st["seq"]
                await sock.send_text(json.dumps(st))
                await asyncio.sleep(0.25)

        pusher = asyncio.create_task(push())
        try:
            while True:
                msg = json.loads(await sock.receive_text())
                op = msg.get("op")
                if op == "stop":                               # any page may stop
                    p.stop(msg.get("why") or "STOP on a page")
                    continue
                if not (is_op and p.operator_ws is sock and token == p.operator_token):
                    continue                                   # everything else is the operator's
                try:
                    if op == "beat":
                        p.beat()
                    elif op == "release":
                        if p.session:
                            p.beat()
                            p.session.release()
                    elif op == "connect":
                        p.connect(msg.get("host") or p.args.spot)
                    elif op == "disconnect":
                        p.disconnect()
                    elif op == "cmd" and msg.get("name") in BY_NAME:
                        p.run(msg["name"])
                    elif op == "load":
                        p.load(msg.get("name"))
                    elif op == "arm":
                        if p.live and p.live.on:
                            raise RuntimeError("refused: switch Live off before readying a show")
                        p.playback.arm(p.session, msg.get("speed", 1.0))
                    elif op == "live" and p.live:
                        p.live.switch(bool(msg.get("on")))
                    elif op == "start":
                        p.playback.start(p.session)
                except Exception as e:
                    p.log(f"{op}: {e}")
        except (WebSocketDisconnect, RuntimeError, ValueError):
            pass
        finally:
            pusher.cancel()
            if p.operator_ws is sock:
                p.operator_ws = None
                if p.session and p.session.released:
                    p.stop("the operator page closed or lost its connection")

    app.mount("/", StaticFiles(directory=os.path.join(HERE, "web"), html=True), name="web")
    return app


def make_live_app(p):
    """The live port's app: /live and nothing else, so a client there can't load, start or stop a show
    or touch the E-Stop. live.py describes the messages."""
    app = FastAPI()

    @app.websocket("/live")
    async def live(sock: WebSocket):
        # Scripts send no Origin. A browser page may drive Spot only from an --allow-origin, so a web page
        # open anywhere on Spot's network can't.
        origin = sock.headers.get("origin")
        if origin and origin not in p.args.allow_origin:
            await sock.close(code=1008)
            return
        who = f"{sock.client.host}:{sock.client.port}" if sock.client else "?"
        if not p.live.connect(who):
            await sock.close(code=1013, reason="another live client is connected")
            return
        await sock.accept()
        none = object()
        pending, ready = none, asyncio.Event()

        async def receive():
            # Keeps only the newest request while Spot is taking the one before, so a slow link never
            # plays out a backlog of old requests.
            nonlocal pending
            try:
                while True:
                    text = await sock.receive_text()
                    try:
                        msg = json.loads(text)
                    except ValueError:
                        await sock.send_text(json.dumps(p.live.refuse(text[:200], "not JSON")))
                        continue
                    if pending is not none:
                        await sock.send_text(json.dumps(p.live.refuse(pending, "superseded by a newer request")))
                    pending = msg
                    ready.set()
            finally:
                ready.set()             # wakes the sender to see the client has gone

        reader = asyncio.create_task(receive())
        try:
            while True:
                await ready.wait()
                if reader.done():
                    break
                ready.clear()
                msg, pending = pending, none
                if msg is none:
                    continue
                await sock.send_text(json.dumps(await asyncio.to_thread(p.live.request, msg)))
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            reader.cancel()
            p.live.disconnect(who)

    return app


def serve_live(p, addr, port):
    """The live port on one address, on its own thread (uvicorn installs no signal handlers there)."""
    server = uvicorn.Server(uvicorn.Config(make_live_app(p), host=addr, port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True, name=f"live {addr}:{port}").start()


def _bind_or_exit(addr, port):
    probe = socket.socket()
    # As uvicorn binds: connections from a player that just stopped linger in TIME_WAIT for up to a
    # minute, and without this the next start (ENCORE's reconnect after a drop) would read "busy". A
    # live listener still refuses the bind.
    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        probe.bind((addr, port))
    except OSError as e:
        raise SystemExit(f"refused: cannot listen on {addr}:{port} ({e.strerror}). If it is busy, "
                         f"see who holds it: lsof -nP -iTCP:{port}")
    finally:
        probe.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--fake", action="store_true", help="no robot: a fake Spot stands in")
    ap.add_argument("--spot", default=DEFAULT_HOST, help="Spot's address")
    ap.add_argument("--listen", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8097)
    ap.add_argument("--plans", default=os.path.expanduser("~/.opendubs-spot/plans"))
    ap.add_argument("--login-stdin", action="store_true",
                    help="for ENCORE's Connect: read Spot's login as two lines on stdin, then exit when stdin closes")
    ap.add_argument("--walking", action="store_true",
                    help="hold Spot to the walking envelope (BASE allowed, under its caps) instead of feet planted. "
                         "Only with the owner's word at Spot")
    ap.add_argument("--allow-origin", action="append", default=[],
                    help="another browser origin allowed on /ws (and on /live), e.g. http://192.168.80.3:21099")
    ap.add_argument("--live", action="store_true",
                    help="serve /live, where a stream of velocity requests drives Spot once the operator "
                         "switches Live on (live.py). Only with the owner's word at Spot")
    ap.add_argument("--live-listen", action="append", default=None,
                    help="an address for the live port; repeat for more (default 127.0.0.1)")
    ap.add_argument("--live-port", type=int, default=live_mode.PORT)
    ap.add_argument("--live-padding", type=float, default=None,
                    help="how far (m) Spot's obstacle avoidance keeps from obstacles; Spot's default if unset")
    ap.add_argument("--live-log", default=os.path.expanduser("~/.opendubs-spot/live"),
                    help="where each run's JSON-lines log of requests and commands goes")
    args = ap.parse_args()
    args.live_listen = args.live_listen or ["127.0.0.1"]
    use_envelope("walking" if args.walking else "first-run")
    _bind_or_exit(args.listen, args.port)
    if args.live:
        for addr in args.live_listen:
            _bind_or_exit(addr, args.live_port)
    def check(user, password):
        import bosdyn.client                # a login only: nothing is sent to Spot
        bosdyn.client.create_standard_sdk("opendubs-spot-login").create_robot(args.spot).authenticate(user, password)

    if args.login_stdin:
        # ENCORE's Connect sends the login down the SSH session's stdin and keeps the session open. When
        # it closes (ENCORE quits, the link drops), stdin ends and the player exits, so Spot sits and the
        # lease and the port are freed. A player left behind would block the next Connect.
        try:
            if args.fake:
                sys.stdin.readline(), sys.stdin.readline()          # the fake takes any login
            else:
                spot_credentials.from_stdin(check)
        except Exception as e:
            print(f"ENCORE-LOGIN-REFUSED {type(e).__name__}", flush=True)
            raise SystemExit(f"refused: Spot's login failed ({type(e).__name__})")
        print("ENCORE-LOGIN-OK", flush=True)

        def until_eof():
            for _ in sys.stdin:
                pass
            print("stdin closed: the player stops, and Spot sits", flush=True)
            os.kill(os.getpid(), signal.SIGINT)
        threading.Thread(target=until_eof, daemon=True).start()
    elif not args.fake and not spot_credentials.keychain():
        # No Keychain (the Orin): ask for the login now, on the SSH terminal, and check it with Spot.
        # It stays in memory for every Connect this run.
        print(f"Spot at {args.spot}: its login is asked for here and kept in memory only.", flush=True)
        spot_credentials.ask(check)
    p = Player(args)
    print(f"Spot player{' (FAKE Spot)' if args.fake else ''}, {envelope_name()} envelope: open http://{args.listen}:{args.port}/  "
          f"pairing code {p.code}", flush=True)
    if args.live:
        for addr in args.live_listen:
            serve_live(p, addr, args.live_port)
        print(f"live mode: ws://{{{','.join(args.live_listen)}}}:{args.live_port}/live, off until the operator "
              f"switches it on; log {p.live.path}", flush=True)
    uvicorn.run(make_app(p), host=args.listen, port=args.port, log_level="warning")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:           # stdin closed (--login-stdin) before the server was up
        pass
