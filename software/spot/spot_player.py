"""OpenDubs Spot player: holds Spot's session, keeps ENCORE plans, plays them, and serves the operator page.
It runs beside Spot (the Orin or a Pi on the payload network, or the Mac on its cable), as the Thor's
runner does for opendubs2; the operator page runs on any browser, such as a tablet.

    cd software/spot
    uv run --no-project --python 3.12 --with 'bosdyn-client==5.1.4' --with fastapi \
        --with 'uvicorn[standard]' python spot_player.py [--fake] [--spot HOST] [--listen ADDR] [--port N]

  --fake      no robot: fake_spot.py stands in, for developing shows and the page with Spot off.
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
  GET  /api/status          everything the page shows
The interface never releases the E-Stop, never arms a show and never sends Spot a command.
"""
import argparse
import asyncio
import collections
import json
import os
import re
import secrets
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

import uvicorn
from fastapi import Body, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

import spot_credentials
from player import Playback
from spot_session import BY_NAME, COMMANDS, DEFAULT_HOST, explain, plan_length_s

HERE = os.path.dirname(os.path.abspath(__file__))
INTERFACE_VERSION = 1


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
        self.operator_token = None
        self.operator_ws = None
        os.makedirs(args.plans, exist_ok=True)
        threading.Thread(target=self._poll_status, daemon=True).start()

    def log(self, msg):
        with self.lock:
            self.seq += 1
            self.lines.append((self.seq, f"{time.strftime('%H:%M:%S')} {msg}"))
        print(msg, flush=True)

    # The E-Stop and the session
    def stop(self, why):
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
                "operator": operator, "operator_present": self.operator_ws is not None,
                "log": lines, "seq": self.seq}


def make_app(p):
    @asynccontextmanager
    async def lifespan(app):
        yield
        if p.session:
            p.log("player closing: Spot sits, the lease is returned")
            p.session.close()

    app = FastAPI(lifespan=lifespan)

    def fail(e):
        raise HTTPException(status_code=409, detail=str(e))

    @app.get("/api/describe")
    def describe():
        return {"robot": "spot", "player": "opendubs-spot-player", "interface": INTERFACE_VERSION,
                "fake": p.args.fake, "parts": ["BASE", "BODY", "ARM", "GRIP"],
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
                        p.playback.arm(p.session, msg.get("speed", 1.0))
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


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--fake", action="store_true", help="no robot: a fake Spot stands in")
    ap.add_argument("--spot", default=DEFAULT_HOST, help="Spot's address")
    ap.add_argument("--listen", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8097)
    ap.add_argument("--plans", default=os.path.expanduser("~/.opendubs-spot/plans"))
    args = ap.parse_args()
    probe = socket.socket()
    try:
        probe.bind((args.listen, args.port))
    except OSError as e:
        raise SystemExit(f"refused: cannot listen on {args.listen}:{args.port} ({e.strerror}). If it is busy, "
                         f"see who holds it: lsof -nP -iTCP:{args.port}")
    finally:
        probe.close()
    if not args.fake and not spot_credentials.keychain():
        # No Keychain (the Orin): ask for the login now, on the SSH terminal, and check it with Spot
        # (a login only, nothing is sent to Spot). It stays in memory for every Connect this run.
        import bosdyn.client

        def check(user, password):
            bosdyn.client.create_standard_sdk("opendubs-spot-login").create_robot(args.spot).authenticate(user, password)
        print(f"Spot at {args.spot}: its login is asked for here and kept in memory only.", flush=True)
        spot_credentials.ask(check)
    p = Player(args)
    print(f"Spot player{' (FAKE Spot)' if args.fake else ''}: open http://{args.listen}:{args.port}/  "
          f"pairing code {p.code}", flush=True)
    uvicorn.run(make_app(p), host=args.listen, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
