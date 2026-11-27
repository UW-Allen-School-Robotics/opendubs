"""Check live mode on a FAKE Spot player, as the operator page and a student client at once. It refuses a
player that is not --fake. It checks what keeps Spot safe when a program drives it:
- nothing is sent before the operator switches Live on, and Live needs Spot standing;
- requests are held to the walking limits, and an out-of-order seq is refused;
- a second client, and a browser page from another site, are refused;
- Spot stands once requests stop, and a stale operator heartbeat stops Spot and switches Live off.

    uv run --no-project --python 3.12 --with 'bosdyn-client==5.1.4' --with fastapi --with 'uvicorn[standard]' \
        python software/spot/spot_player.py --fake --live --port 8197 --live-port 8198
    uv run --no-project --python 3.12 --with websockets python software/spot/fake_live.py CODE [PORT] [LIVE_PORT]
"""
import asyncio, json, sys, time, urllib.request
import websockets

code = sys.argv[1]
PORT = sys.argv[2] if len(sys.argv) > 2 else "8097"
LIVE = sys.argv[3] if len(sys.argv) > 3 else "8100"
BASE, LIVE_URL = f"127.0.0.1:{PORT}", f"ws://127.0.0.1:{LIVE}/live"
failed = []


def check(ok, what):
    print(("PASS " if ok else "FAIL ") + what, flush=True)
    if not ok:
        failed.append(what)


def post(path, body):
    req = urllib.request.Request(f"http://{BASE}{path}", json.dumps(body).encode(), {"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req))


def status():
    return json.load(urllib.request.urlopen(f"http://{BASE}/api/status"))


async def main():
    if not json.load(urllib.request.urlopen(f"http://{BASE}/api/describe"))["fake"]:
        sys.exit("refused: not a fake player")
    token = post("/api/claim", {"code": code})["token"]
    async with websockets.connect(f"ws://{BASE}/ws?token={token}") as op:
        beating = True

        async def beat():
            while True:
                if beating:
                    await op.send(json.dumps({"op": "beat"}))
                await asyncio.sleep(0.2)

        async def drain():
            while True:
                await op.recv()

        tasks = [asyncio.create_task(beat()), asyncio.create_task(drain())]
        if not status()["connected"]:
            await op.send(json.dumps({"op": "connect"}))
            await asyncio.sleep(1.0)

        async with websockets.connect(LIVE_URL) as cl:
            seq = 0

            async def ask(vx=0.0, vy=0.0, wz=0.0, s=None):
                nonlocal seq
                seq = seq + 1 if s is None else s
                await cl.send(json.dumps({"seq": seq, "vx": vx, "vy": vy, "wz": wz}))
                return json.loads(await cl.recv())

            r = await ask(0.2)
            check(r["result"] == "refused", f"refused before Live is on ({r.get('reason')})")
            await op.send(json.dumps({"op": "live", "on": True}))
            await asyncio.sleep(0.6)
            check(not status()["live"]["on"], "Live stays off while Spot sits")

            for m in [{"op": "release"}, {"op": "cmd", "name": "stand"}]:
                await op.send(json.dumps(m))
                await asyncio.sleep(1.6)
            await op.send(json.dumps({"op": "live", "on": True}))
            await asyncio.sleep(0.6)
            check(status()["live"]["on"], "Live switches on with Spot standing")

            x0 = status()["status"]["fake_pose"]["x"]
            results = []
            for _ in range(40):                                  # 2 s at 20 Hz, 0.2 m/s forward
                results.append((await ask(0.2))["result"])
                await asyncio.sleep(0.05)
            check(results.count("sent") == 40, f"40 requests at 20 Hz all sent ({results.count('sent')})")

            r = await ask(1.0, 0.0, 2.0)
            check(r["result"] == "limited" and abs(r["sent"]["vx"] - 0.35) < 1e-3 and abs(r["sent"]["wz"] - 0.5236) < 1e-3,
                  f"1 m/s and 2 rad/s held to 0.35 m/s and 30 deg/s ({r.get('sent')})")
            r = await ask(0.1, s=seq)
            check(r["result"] == "refused" and "out of order" in r["reason"], "a repeated seq is refused")
            r = await ask(0.1, "fast")
            check(r["result"] == "refused", f"a non-number is refused ({r.get('reason')})")

            await asyncio.sleep(1.0)                             # no requests: Spot stands
            p1 = status()["status"]["fake_pose"]
            await asyncio.sleep(0.8)
            p2 = status()["status"]["fake_pose"]
            moved = p1["x"] - x0
            check(0.35 < moved < 1.0, f"walked {moved:.2f} m for 2 s at 0.2 m/s and the tail")
            check(p1 == p2, f"stands once requests stop ({p1} then {p2})")

            try:
                async with websockets.connect(LIVE_URL) as second:
                    await second.recv()
                check(False, "a second client is refused")
            except Exception:
                check(True, "a second client is refused")
            try:
                async with websockets.connect(LIVE_URL, origin="http://example.com") as other:
                    await other.recv()
                check(False, "a page from another site is refused")
            except Exception:
                check(True, "a page from another site is refused")

            beating = False                                      # the operator page goes quiet
            await asyncio.sleep(1.8)
            st = status()
            check(st["estop"] == "STOPPED" and not st["live"]["on"], "a stale heartbeat stops Spot and Live")
            r = await ask(0.2)
            check(r["result"] == "refused", f"refused after the stop ({r.get('reason')})")
            print("log:", st["live"]["log"])
        for t in tasks:
            t.cancel()
    sys.exit(f"{len(failed)} failed" if failed else 0)

asyncio.run(main())
