#!/usr/bin/env python3
"""Touch proof-of-life visualizer for the OpenDubs panel.

Display goes through X (which works), but touch input is read DIRECTLY from the
kernel evdev device (/dev/input/eventN for "QDtech MPI7019") via python-evdev —
bypassing X's input layer entirely. This proves the hardware/kernel touch path
is alive independent of whatever is broken between libinput and the cursor, and
shows the raw coordinates so we can derive the correct transform.

Draws a dot at each active touch point and prints raw + mapped coords.
ESC or Q to quit.

Run as the kiosk user on the panel's display, e.g.:
  SDL_VIDEODRIVER=x11 DISPLAY=:0 XAUTHORITY=/run/user/2003/gdm/Xauthority \
    python3 touch_viz.py
"""
import os
import select
import sys

import evdev
from evdev import ecodes

os.environ.setdefault("SDL_VIDEODRIVER", "x11")
import pygame  # noqa: E402 (after env setup)


def find_touch():
    for path in evdev.list_devices():
        d = evdev.InputDevice(path)
        if "MPI7019" in d.name or "QDtech" in d.name:
            return d
        caps = d.capabilities()
        if ecodes.EV_ABS in caps:
            codes = [c for c, _ in caps[ecodes.EV_ABS]]
            if ecodes.ABS_MT_POSITION_X in codes:
                return d  # any multitouch device as a fallback
    return None


def abs_range(absinfo, code, default_max):
    if code in absinfo:
        return absinfo[code].min, absinfo[code].max
    return 0, default_max


def main():
    dev = find_touch()
    if not dev:
        print("ERROR: no touch device found in /dev/input/*", file=sys.stderr)
        sys.exit(1)
    print(f"reading: {dev.path}  ({dev.name})")

    absinfo = {c: info for c, info in dev.capabilities().get(ecodes.EV_ABS, [])}
    xmin, xmax = abs_range(absinfo, ecodes.ABS_MT_POSITION_X, 480)
    ymin, ymax = abs_range(absinfo, ecodes.ABS_MT_POSITION_Y, 1920)
    xspan = max(xmax - xmin, 1)
    yspan = max(ymax - ymin, 1)
    print(f"raw ranges: X[{xmin}..{xmax}] Y[{ymin}..{ymax}]")

    pygame.init()
    screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
    W, H = screen.get_size()
    print(f"screen: {W}x{H}")
    pygame.mouse.set_visible(False)
    font = pygame.font.SysFont(None, 32)
    big = pygame.font.SysFont(None, 56)
    clock = pygame.time.Clock()

    # rotate-left (90 CCW) mapping from native portrait raw -> landscape screen.
    def to_screen(rx, ry):
        nx = (rx - xmin) / xspan  # 0..1 across native width (480)
        ny = (ry - ymin) / yspan  # 0..1 across native height (1920)
        # empirically correct for this panel's landscape orientation:
        sx = int((1.0 - ny) * W)
        sy = int(nx * H)
        return sx, sy

    slots = {}      # slot -> [rawx, rawy]
    cur = 0
    last = "none"
    running = True
    while running:
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                running = False
            elif e.type == pygame.KEYDOWN and e.key in (pygame.K_ESCAPE, pygame.K_q):
                running = False

        r, _, _ = select.select([dev.fd], [], [], 0)
        if r:
            try:
                for ev in dev.read():
                    if ev.type == ecodes.EV_ABS:
                        if ev.code == ecodes.ABS_MT_SLOT:
                            cur = ev.value
                        elif ev.code == ecodes.ABS_MT_TRACKING_ID:
                            if ev.value == -1:
                                slots.pop(cur, None)
                            else:
                                slots.setdefault(cur, [0, 0])
                        elif ev.code == ecodes.ABS_MT_POSITION_X:
                            slots.setdefault(cur, [0, 0])[0] = ev.value
                        elif ev.code == ecodes.ABS_MT_POSITION_Y:
                            slots.setdefault(cur, [0, 0])[1] = ev.value
                        # single-touch fallback
                        elif ev.code == ecodes.ABS_X:
                            slots.setdefault(0, [0, 0])[0] = ev.value
                        elif ev.code == ecodes.ABS_Y:
                            slots.setdefault(0, [0, 0])[1] = ev.value
                    elif ev.type == ecodes.EV_KEY and ev.code == ecodes.BTN_TOUCH:
                        if ev.value == 0:
                            slots.pop(0, None)
            except BlockingIOError:
                pass

        screen.fill((8, 12, 18))
        if not slots:
            msg = big.render("TOUCH THE SCREEN", True, (120, 140, 160))
            screen.blit(msg, msg.get_rect(center=(W // 2, H // 2)))
        y = 10
        for slot, (rx, ry) in sorted(slots.items()):
            sx, sy = to_screen(rx, ry)
            pygame.draw.circle(screen, (0, 230, 200), (sx, sy), 45)
            pygame.draw.circle(screen, (255, 255, 255), (sx, sy), 45, 3)
            last = f"slot{slot} raw=({rx},{ry}) -> screen=({sx},{sy})"
            screen.blit(font.render(last, True, (230, 240, 250)), (12, y))
            y += 30
        pygame.display.flip()
        clock.tick(60)

    pygame.quit()


if __name__ == "__main__":
    main()
