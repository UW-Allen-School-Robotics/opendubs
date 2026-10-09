# Deployment drift — the repo is not what is running

There is no deployment mechanism on this fleet. Files reach the robots by hand, one at a
time, usually mid-debug. So the repo and the robots diverge, and nobody finds out until
something breaks.

Run `tools/deploy-audit.py <robot>` to see the current state. It classifies rather than
just diffing, because some divergence is correct.

## What it cost us — 2026-09-08

`platform = espressif32` was pinned to `@7.0.1` in **PR #9, merged 2026-09-01**, precisely
because an unpinned platform resolves forward and breaks M5StamPLC's Modbus code against
the newer ESP-IDF.

opendubs2 never received it. A week later, building an unrelated project on the same
machine caused PlatformIO to resolve the newer platform, and the StamPLC firmware — the
thing that runs the drive, the e-stop and the lift — stopped building entirely, mid-session,
for reasons that had nothing to do with what we were changing. The fix had been merged for
seven days.

The cost was an hour of confused debugging at exactly the wrong moment. The next one might
not be a build failure.

## Known state of opendubs2, as of 2026-09-08

Fixed that day:

- `rc_serial_probe/platformio.ini` — platform pinned on the robot (backup:
  `platformio.ini.pre-pin`). `intelhex` installed into `/opt/opendubs/piovenv`, which the
  newer toolchain had left behind.
- `kiosk-dashboard/backend/status.py` — the robot still had `RELAY_LABELS` reading
  `["E-STOP CONTACTOR", "8SERVO POWER-CYCLE", "SPARE", "SPARE"]`. R1 and R2 are the lift's
  DOWN and UP legs. A stale label on a relay that presses a lift button is not cosmetic.
- `kiosk-dashboard/backend/stamplc.py`, `app.py`, `frontend/app.js` — brought current.

Expected to differ, and must not be blindly synced:

- `rc_serial_probe/src/main.cpp` — carries this robot's drive calibration
  (`kDriveInvert`, `kEscSpanUs`, `kEscSlewUs`, `kEscRevDwellTicks`), the `-str` inversion,
  and two features that exist only on the robot: the PWM mirror diagnostic and the
  contactor auto-arm. **The uncommitted features are a second kind of drift** — work that
  exists nowhere else, and the next flash from the repo overwrites it.
- `rc_serial_probe/platformio.ini` — has a local `monitor_port`.

## The real fix

An audit tells you that you have drifted. It does not stop you drifting.

What is actually missing is a deploy path that knows the difference between a file that
should be identical everywhere and a file that holds a per-robot value — the same
distinction `tools/access.py` makes for accounts. Until that exists, run the audit before
trusting the robot to match a merged PR, and especially before diagnosing anything strange.

**First step, 2026-10-09:** `deploy-all.sh` reads the audit's `PER_ROBOT` list
(`deploy-audit.py --rsync-protect`) and never rsyncs over those files. Before that, its
`rsync --delete` replaced the robot's `main.cpp`, and the drive calibration in it, with the
repo's worked example. It also protects the robot-side backups (`*.bak*`, `*.pre-*`), and it
deploys the dashboard through `deploy.sh`, which no longer has its `docs-site/` deleted or a
dangling `assets/arm` symlink copied in. Still missing: one manifest for every deploy path,
and per-robot values that live outside the source tree altogether (NVS on the StamPLC), which
is a firmware change.
