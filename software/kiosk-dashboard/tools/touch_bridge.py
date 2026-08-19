#!/usr/bin/env python3
"""OpenDubs touch bridge: evdev -> X11 XTEST absolute pointer.

X/GNOME won't deliver the QDtech MPI7019 touchscreen's events, and this Tegra
kernel has no uinput (CONFIG_INPUT_UINPUT unset), so we can't make a virtual
input device. Instead we read the real device directly and drive the X cursor
via the XTEST extension: the pointer jumps to your finger and a tap is a left
click.

Rotation is selectable via env TOUCH_ROT (left|right|inverted|normal):
  - old Thor bar-panel mount = "left" (default, backward compatible)
  - AGX Thor bar-panel mount = "right" (set by deploy/session-panel.sh)
Raw is native portrait (short axis = X ~480, long axis = Y ~1920); screen is
landscape WxH (1920x480). Transforms below map normalized raw (nx short, ny
long) -> normalized screen (sx, sy).

Resilience: the USB touch panel occasionally drops off the bus (read_loop ->
OSError). We never crash on that — we release the device and re-acquire it in a
loop. We also ONLY warp the cursor while a touch is actually down (BTN_TOUCH):
a failing/disconnecting panel spews phantom ABS samples, and warping on those
swept the cursor across the tab bar. A short debounce drops button chatter.

Run as the user owning the X session (or root), with DISPLAY + XAUTHORITY set:
    DISPLAY=:0 XAUTHORITY=/run/user/2003/gdm/Xauthority python3 touch_bridge.py
"""
import os
import time

import evdev
from evdev import ecodes
from Xlib import X, display
from Xlib.ext import xtest

SRC_MATCH = ("MPI7019", "QDtech")
DEBOUNCE_S = 0.025   # ignore button transitions faster than this (chatter guard)
ROT = os.environ.get("TOUCH_ROT", "left").lower()


def find_src():
    for path in evdev.list_devices():
        try:
            d = evdev.InputDevice(path)
        except Exception:
            continue
        if any(m in d.name for m in SRC_MATCH):
            return d
    return None


def run_once(d, W, H):
    """Acquire the touch device and pump events until it disappears. Returns
    on device loss (so the caller can re-acquire); raises only on fatal errors."""
    src = find_src()
    if not src:
        return False
    ai = {c: info for c, info in src.capabilities().get(ecodes.EV_ABS, [])}

    def rng(code, dmax):
        return (ai[code].min, ai[code].max) if code in ai else (0, dmax)

    xmin, xmax = rng(ecodes.ABS_MT_POSITION_X, 480)
    ymin, ymax = rng(ecodes.ABS_MT_POSITION_Y, 1920)
    xspan = max(xmax - xmin, 1)
    yspan = max(ymax - ymin, 1)
    print(f"source {src.name} X[{xmin},{xmax}] Y[{ymin},{ymax}] screen {W}x{H} rot={ROT}", flush=True)

    try:
        src.grab()
        print("grabbed source device", flush=True)
    except Exception as e:
        print(f"grab failed (continuing): {e}", flush=True)

    cur_slot = 0
    last = [0, 0]
    have = False
    touch = 0
    pending = None
    last_btn_t = 0.0

    def clamp(v):
        return min(1.0, max(0.0, v))

    def move():
        nx = clamp((last[0] - xmin) / xspan)   # short axis 0..1
        ny = clamp((last[1] - ymin) / yspan)   # long axis  0..1
        if ROT == "left":
            sx, sy = (1.0 - ny), nx
        elif ROT == "right":
            sx, sy = ny, (1.0 - nx)
        elif ROT == "inverted":
            sx, sy = (1.0 - nx), (1.0 - ny)
        else:  # normal
            sx, sy = nx, ny
        xtest.fake_input(d, X.MotionNotify, x=int(sx * (W - 1)), y=int(sy * (H - 1)))

    try:
        for ev in src.read_loop():
            if ev.type == ecodes.EV_ABS:
                if ev.code == ecodes.ABS_MT_SLOT:
                    cur_slot = ev.value
                elif ev.code == ecodes.ABS_MT_POSITION_X and cur_slot == 0:
                    last[0] = ev.value; have = True
                elif ev.code == ecodes.ABS_MT_POSITION_Y and cur_slot == 0:
                    last[1] = ev.value; have = True
                elif ev.code == ecodes.ABS_X:
                    last[0] = ev.value; have = True
                elif ev.code == ecodes.ABS_Y:
                    last[1] = ev.value; have = True
            elif ev.type == ecodes.EV_KEY and ev.code == ecodes.BTN_TOUCH:
                pending = ev.value
            elif ev.type == ecodes.EV_SYN and ev.code == ecodes.SYN_REPORT:
                # Button transition first, so a press warps to the touch point
                # before clicking. Debounce to drop chatter from a flaky panel.
                if pending is not None and pending != touch:
                    now = time.monotonic()
                    if now - last_btn_t >= DEBOUNCE_S:
                        if pending and have:
                            move()
                        xtest.fake_input(d, X.ButtonPress if pending else X.ButtonRelease, 1)
                        touch = pending
                        last_btn_t = now
                    pending = None
                elif touch and have:
                    move()          # drag while held
                # Only warp while a finger is down — ignore phantom ABS noise.
                have = False
                d.sync()
    except OSError as e:
        print(f"device read error ({e}) — will re-acquire", flush=True)
    finally:
        try:
            src.ungrab()
        except Exception:
            pass
        try:
            src.close()
        except Exception:
            pass
    return True


def main():
    d = display.Display()  # uses $DISPLAY / $XAUTHORITY
    scr = d.screen()
    W, H = scr.width_in_pixels, scr.height_in_pixels
    miss = 0
    while True:
        try:
            found = run_once(d, W, H)
        except Exception as e:
            print(f"bridge error: {e}", flush=True)
            found = True
        if not found:
            miss += 1
            if miss % 10 == 1:
                print("source touch device not found — waiting", flush=True)
        else:
            miss = 0
        time.sleep(1.0)


if __name__ == "__main__":
    main()
