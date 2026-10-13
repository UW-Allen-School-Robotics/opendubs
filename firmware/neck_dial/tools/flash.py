#!/usr/bin/env python3
"""Flash the neck's M5Dial over USB, reliably.

    python3 tools/flash.py            # build with build.sh first, then flash

WHY THIS EXISTS. A plain `pio run -t upload` does not work on this board, and the reasons are the
maintainers' own, learned on their M5Dial project (~/Documents/dev/m5dial-roller485) and adopted
here rather than re-derived:

  * esptool's "stub" helper restarts the Dial's USB mid-upload, so the Mac loses the connection and
    the write dies with "Serial data stream stopped" -> ALWAYS --no-stub. It is a GLOBAL esptool
    option and must come before the subcommand, which is why it is not in platformio.ini's
    upload_flags (those land after `write_flash`, where esptool ignores them).
  * Resetting the Dial into download mode makes it VANISH from USB for a second or two, and esptool
    gives up before it returns -> reset, WAIT for it to come back, then write, and retry.
  * Afterwards it needs a WATCHDOG reset. A normal hard reset leaves it stuck in download mode with
    a black screen.

IF IT STILL FAILS, the program on it is probably crashing too fast to catch. Put the Dial into
download mode by hand -- hold the small button on the back while plugging the USB in, screen stays
black -- and run this again.
"""
import os, subprocess, sys, time
from serial.tools import list_ports

VID = 0x303A                 # every Espressif USB chip reports this
ROUNDS = 4
HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(os.path.dirname(HERE), ".pio", "build", "neck_dial")
BOOT0 = os.path.expanduser(
    "~/.platformio/packages/framework-arduinoespressif32/tools/partitions/boot_app0.bin")


def find():
    for p in list_ports.comports():
        if p.vid == VID:
            return p.device
    return None


def wait(timeout=25.0, settle=0.7):
    """Wait until it is on USB and has STAYED there briefly -- it reappears before it is ready."""
    end = time.time() + timeout
    while time.time() < end:
        if find():
            time.sleep(settle)
            if find():
                return find()
        time.sleep(0.2)
    return None


def esptool(port, *args):
    cmd = [sys.executable, "-m", "esptool", "--chip", "esp32s3", "--port", port,
           "--baud", "115200", "--no-stub", *args]
    return subprocess.run(cmd).returncode


def main():
    for f in ("bootloader.bin", "partitions.bin", "firmware.bin"):
        if not os.path.exists(os.path.join(BUILD, f)):
            print(f"missing {f} -- run ./build.sh image first"); return 1

    for attempt in range(1, ROUNDS + 1):
        port = wait()
        if not port:
            print("The Dial isn't on USB. Is it plugged in with a DATA cable?"); return 1

        # WRITE FIRST, RESET ONLY IF THAT CANNOT CONNECT. The obvious order -- reset into
        # download mode, then write -- is WRONG when the board is ALREADY in download mode,
        # because the reset kicks it straight back out. That is not hypothetical: it cost this
        # project most of an afternoon. The board would be sitting in download mode after someone
        # held the button, a helpful `usb_reset` would throw it out, and every write afterwards
        # failed while the board looked fine on USB.
        #
        # So: try the write. Only if it could not even connect is the board not in download mode,
        # and only then is a reset the right thing to do.
        print(f"[{attempt}/{ROUNDS}] writing to {port}")
        # APP ONLY, at 0x10000. Writing the COMBINED image at 0x0 also erases NVS -- esptool's
        # merged image pads the gaps with 0xff, and the Wi-Fi credentials live in that gap, so a
        # full write silently un-provisions the board every time. Observed: the neck came back up
        # with `ssid=(unset)` after a flash that was only meant to change the app. The bootloader
        # and partition table do not change between builds, so there is nothing to gain by
        # rewriting them. Pass --full to do it anyway on a virgin board.
        rc = esptool(port, "--before", "no_reset", "--after", "watchdog_reset",
                     "--connect-attempts", "5",
                     "write_flash", "-z", "--flash_mode", "dio", "--flash_freq", "80m",
                     "--flash_size", "8MB",
                     "0x0", os.path.join(BUILD, "bootloader.bin"),
                     "0x8000", os.path.join(BUILD, "partitions.bin"),
                     "0xe000", BOOT0,
                     "0x10000", os.path.join(BUILD, "firmware.bin"))
        if rc == 0:
            print("\nDONE. It restarts itself. If the screen stays black, unplug and replug.")
            return 0

        # Only now: it would not take a write, so try to put it into download mode for the next
        # round. This often reports a failure of its own, which is fine.
        print(f"[{attempt}/{ROUNDS}] no good -- trying to reset it into download mode")
        esptool(port, "--before", "usb_reset", "--after", "no_reset", "flash_id")
        wait()

    print("\nCould not flash after several tries. Put it into download mode BY HAND --")
    print("hold the small button on the back while plugging the USB in -- and run this again.")
    return 1


sys.exit(main())
