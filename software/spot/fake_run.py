"""Play a stored plan on a FAKE Spot player from the command line, as a headless operator page: claim,
beat every 200 ms, release, stand, load, arm, start, and print every late window, stop and the end.
It refuses a player that is not --fake.

    uv run --no-project --python 3.12 --with websockets python software/spot/fake_run.py CODE PLAN.json [SPEED] [PORT]
    # CODE is the pairing code the fake player printed; PLAN.json a plan stored on it; SPEED 1 by default;
    # PORT a port on this machine (8097 by default) or HOST:PORT, e.g. 192.168.50.5:8099 for the Orin.
"""
import asyncio, json, sys, urllib.request
import websockets

code, plan = sys.argv[1], sys.argv[2]
SPEED = float(sys.argv[3]) if len(sys.argv) > 3 else 1.0
_where = sys.argv[4] if len(sys.argv) > 4 else "8097"     # a port on this machine, or HOST:PORT (the Orin)
BASE = _where if ":" in _where else f"127.0.0.1:{_where}"


def post(path, body):
    req = urllib.request.Request(f"http://{BASE}{path}", json.dumps(body).encode(), {"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req))


async def main():
    if not json.load(urllib.request.urlopen(f"http://{BASE}/api/describe"))["fake"]:
        sys.exit("refused: not a fake player")
    token = post("/api/claim", {"code": code})["token"]
    async with websockets.connect(f"ws://{BASE}/ws?token={token}") as ws:
        async def beat():
            while True:
                await ws.send(json.dumps({"op": "beat"}))
                await asyncio.sleep(0.2)
        beating = asyncio.create_task(beat())
        last = None
        async def latest():
            nonlocal last
            last = json.loads(await ws.recv())
            return last
        await latest()
        if not last["connected"]:
            await ws.send(json.dumps({"op": "connect"}))
        for op in [{"op": "release"}, {"op": "cmd", "name": "stand"}, {"op": "load", "name": plan}]:
            await asyncio.sleep(1.5); await ws.send(json.dumps(op))
        await asyncio.sleep(2.0); await ws.send(json.dumps({"op": "arm", "speed": SPEED}))
        await asyncio.sleep(1.0); await ws.send(json.dumps({"op": "start"}))
        played = False
        while True:
            s = await latest()
            played = played or s["playback"]["state"] == "PLAYING"
            for _, line in s["log"]:
                if "LATE" in line or "STOP" in line or "DONE" in line or "refused" in line or "failed" in line:
                    print(line, flush=True)
            if played and s["playback"]["state"] in ("DONE", "STOPPED"):
                pb = s["playback"]
                print(f"END {pb['state']} sent {pb['sent']}/{pb['windows']} {pb['why']}", flush=True)
                beating.cancel()
                return

asyncio.run(main())
