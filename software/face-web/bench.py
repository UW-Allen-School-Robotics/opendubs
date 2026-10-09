#!/usr/bin/env python3
"""Measure the face page on a tablet over USB: frame times, video decode, and tracking.

    uv run --with websockets python3 software/face-web/bench.py --serial <adb serial> [--webview]

It drives the page that is already open (Chrome, or with --webview the kiosk app's WebView)
through the browser's remote-debugging socket, forwarded over adb: it loads each configuration,
lets it run, and reads the page's own counters (window.faceStats, window.trackStats). The face is
served from this machine by door_dev.py, reached by the tablet as localhost through `adb reverse`.

A run needs the tablet awake, the page in front, and nobody touching it.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import time
import urllib.request

import websockets

ADB = os.path.expanduser("~/Library/Android/sdk/platform-tools/adb")
BASE = "http://localhost:8765/?hud=1&state=SAFE_IDLE"

RUNS = [   # (label, query, seconds, before-reading action)
    ("eyes", "&skin=eyes", 15, None),
    ("ring", "&skin=ring", 15, None),
    ("eyes + 1200p60 video decoding behind", "&skin=eyes&video=vendor/test-1200p60.mp4", 15, None),
    ("1200p60 video showing (cut to media)", "&skin=eyes&video=vendor/test-1200p60.mp4", 15, "cut('media')"),
    ("eyes + 1080p60 video showing", "&skin=eyes&video=vendor/test-1080p60.mp4", 15, "cut('media')"),
    ("eyes + tracking, worker, GPU (sweep stimulus)", "&skin=eyes&track=1&tracksrc=sweep", 25, None),
    ("eyes + tracking, worker, CPU (sweep stimulus)", "&skin=eyes&track=1&tracksrc=sweep&delegate=CPU", 25, None),
    ("eyes + tracking, MAIN THREAD, GPU (sweep stimulus)", "&skin=eyes&track=1&tracksrc=sweep&trackmain=1", 25, None),
]


def socket_name(serial: str, webview: bool) -> str:
    """EXACTLY the socket wanted. Other apps expose devtools sockets too (Samsung's own WebView
    components among them), and "the first one that matches" picked one of those."""
    sh = lambda *c: subprocess.run([ADB, "-s", serial, "shell", *c], capture_output=True, text=True).stdout
    names = {w.lstrip("@") for line in sh("cat", "/proc/net/unix").splitlines() for w in line.split()
             if "devtools_remote" in w}
    if webview:
        pid = sh("pidof", "org.opendubs.kiosk").strip()
        if not pid:
            raise SystemExit("the kiosk app is not running")
        want = f"webview_devtools_remote_{pid}"
    else:
        want = "chrome_devtools_remote"
    if want not in names:
        raise SystemExit(f"no socket {want}; found {sorted(names)}")
    return want


async def evaluate(ws, expr, i=[0]):
    i[0] += 1
    await ws.send(json.dumps({"id": i[0], "method": "Runtime.evaluate",
                              "params": {"expression": expr, "returnByValue": True, "awaitPromise": True}}))
    while True:
        m = json.loads(await ws.recv())
        if m.get("id") == i[0]:
            return m.get("result", {}).get("result", {}).get("value")


async def main(a):
    sock = socket_name(a.serial, a.webview)
    subprocess.run([ADB, "-s", a.serial, "forward", f"tcp:{a.port}", f"localabstract:{sock}"], check=True)
    pages = json.load(urllib.request.urlopen(f"http://127.0.0.1:{a.port}/json"))
    page = next(p for p in pages if p.get("type") == "page" and "8765" in p.get("url", ""))
    results = {"device": None, "runs": []}
    async with websockets.connect(page["webSocketDebuggerUrl"], max_size=None) as ws:
        # Measure what is on disk now, not what the browser kept from an earlier load.
        await ws.send(json.dumps({"id": 800, "method": "Network.clearBrowserCache"}))
        results["device"] = await evaluate(ws, """(() => {
            const c = document.createElement('canvas'), g = c.getContext('webgl2');
            const d = g && g.getExtension('WEBGL_debug_renderer_info');
            return { ua: navigator.userAgent, dpr: devicePixelRatio, css: [innerWidth, innerHeight],
                     gl: d ? g.getParameter(d.UNMASKED_RENDERER_WEBGL) : (g ? 'webgl2 (renderer hidden)' : 'no webgl2'),
                     webgpu: !!navigator.gpu };
        })()""")
        print(json.dumps(results["device"], indent=1))
        for label, query, secs, action in RUNS:
            if a.only and a.only not in label:
                continue
            await ws.send(json.dumps({"id": 900, "method": "Page.navigate", "params": {"url": BASE + query}}))
            time.sleep(3)
            if action:
                await evaluate(ws, action)
            time.sleep(secs)
            v = await evaluate(ws, "JSON.stringify({face: window.faceStats, track: window.trackStats ? "
                                   "{warmupMs: trackStats.warmupMs, loadMs: trackStats.loadMs, frames: trackStats.frames, "
                                   "hits: trackStats.hits, detectMs: trackStats.detectMs} : null, "
                                   "hud: document.getElementById('hud').textContent})")
            r = json.loads(v)
            r["label"] = label
            results["runs"].append(r)
            print(f"\n== {label}\n{r['hud']}")
    out = a.out or f"bench-{'webview' if a.webview else 'chrome'}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    with open(out, "w") as f:
        json.dump(results, f, indent=1)
    print(f"\nwritten {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--serial", required=True, help="the bench tablet's adb serial — never the robot's by accident")
    ap.add_argument("--webview", action="store_true", help="the kiosk app's WebView instead of Chrome")
    ap.add_argument("--port", type=int, default=9333)
    ap.add_argument("--out")
    ap.add_argument("--only", help="run only the configurations whose label contains this")
    asyncio.run(main(ap.parse_args()))
