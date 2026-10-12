#!/usr/bin/env python3
"""Is the kiosk still a kiosk? Run before and after anything that changes the tablet — a WebView
update, a system update, an app update — and compare.

    uv run --with websockets python3 software/tablet-kiosk/kiosk_check.py --serial <adb serial>

Checks, each PASS or FAIL:
  - the tablet's model, Android release, and current WebView (reported, not judged)
  - the kiosk app is device owner, in front, and in lock task
  - in the app's WebView: the page, a secure context, the bridge (and that it says device owner),
    the microphone granted with no prompt (TB-1), and a screen wake lock

It changes nothing it does not put back: the microphone is opened and its track stopped at once,
and the wake lock is released. It needs the app to be a debuggable build (its WebView then exposes
a devtools socket), which the team's builds are.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import urllib.request

import websockets

ADB = os.path.expanduser("~/Library/Android/sdk/platform-tools/adb")
APP = "org.opendubs.kiosk"


def sh(serial, *cmd):
    return subprocess.run([ADB, "-s", serial, "shell", *cmd], capture_output=True, text=True).stdout


results = []


def check(name, ok, detail=""):
    results.append(("PASS" if ok else "FAIL", name, detail))


async def evaluate(ws, expr, i=[0]):
    i[0] += 1
    await ws.send(json.dumps({"id": i[0], "method": "Runtime.evaluate",
                              "params": {"expression": expr, "returnByValue": True, "awaitPromise": True}}))
    while True:
        m = json.loads(await ws.recv())
        if m.get("id") == i[0]:
            r = m.get("result", {})
            if "exceptionDetails" in r:
                return {"error": r["exceptionDetails"].get("text", "exception")}
            return r.get("result", {}).get("value")


async def page_checks(serial, port):
    pid = sh(serial, "pidof", APP).strip()
    if not pid:
        return check("the app is running", False)
    sock = f"webview_devtools_remote_{pid}"
    if sock not in sh(serial, "cat", "/proc/net/unix"):
        return check("the app's WebView can be inspected", False, f"no {sock}: not a debuggable build?")
    subprocess.run([ADB, "-s", serial, "forward", f"tcp:{port}", f"localabstract:{sock}"], check=True)
    try:
        pages = [p for p in json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json")) if p.get("type") == "page"]
        if not pages:
            return check("the app shows a page", False)
        page = pages[0]
        async with websockets.connect(page["webSocketDebuggerUrl"], max_size=None) as ws:
            url = await evaluate(ws, "location.href")
            check("the page", True, url)
            check("a secure context", await evaluate(ws, "window.isSecureContext") is True)
            bridge = await evaluate(ws, "window.OpenDubsKiosk ? JSON.stringify({owner: OpenDubsKiosk.isDeviceOwner(), version: OpenDubsKiosk.version()}) : null")
            check("the bridge (window.OpenDubsKiosk)", bool(bridge), bridge or "absent")
            if bridge:
                check("the bridge says device owner", json.loads(bridge)["owner"] is True)
            mic = await evaluate(ws, """(async () => {
                if (!navigator.mediaDevices) return 'no mediaDevices';
                const t = Promise.race([navigator.mediaDevices.getUserMedia({audio: true}),
                                        new Promise((_, rej) => setTimeout(() => rej(new Error('no answer in 5 s: a prompt?')), 5000))]);
                try { const s = await t; const st = s.getAudioTracks()[0].readyState; s.getTracks().forEach(k => k.stop()); return st; }
                catch (e) { return 'refused: ' + e.message; } })()""")
            check("the microphone, with no prompt", mic == "live", str(mic))
            wl = await evaluate(ws, """(async () => {
                if (!navigator.wakeLock) return 'no wakeLock';
                try { const l = await navigator.wakeLock.request('screen'); await l.release(); return 'granted'; }
                catch (e) { return 'refused: ' + e.message; } })()""")
            check("a screen wake lock", wl == "granted", str(wl))
    finally:
        subprocess.run([ADB, "-s", serial, "forward", "--remove", f"tcp:{port}"], capture_output=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--serial", required=True, help="the tablet's adb serial (adb devices -l)")
    ap.add_argument("--port", type=int, default=9335)
    a = ap.parse_args()
    s = a.serial

    model = sh(s, "getprop", "ro.product.model").strip()
    release = sh(s, "getprop", "ro.build.version.release").strip()
    build = sh(s, "getprop", "ro.build.display.id").strip()
    wv = next((l.rsplit("(", 1)[1].rstrip(")").strip() for l in sh(s, "dumpsys", "webviewupdate").splitlines()
               if "Current WebView package" in l), "?")
    app = next((l.split("=")[1] for l in sh(s, "dumpsys", "package", APP).split() if l.startswith("versionName=")), "?")
    print(f"{s}: {model}, Android {release} ({build}); WebView {wv}; kiosk app {app}")

    check("device owner is the kiosk app", APP in sh(s, "dpm", "list-owners"))
    acts = sh(s, "dumpsys", "activity", "activities")
    check("the kiosk app is in front", f"{APP}/.KioskActivity" in next((l for l in acts.splitlines() if "topResumedActivity" in l), ""))
    check("lock task", "mLockTaskModeState=LOCKED" in acts.replace(" ", ""))
    asyncio.run(page_checks(s, a.port))

    for st, name, detail in results:
        print(f"{st}  {name:<40} {detail}")
    bad = sum(1 for r in results if r[0] == "FAIL")
    print(f"{len(results) - bad} pass, {bad} fail")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
