# Stamp the image with the commit it was built from, so a PING says WHICH firmware is running.
#
# This must be a `pre:` script. CPPDEFINES set from a `post:` script is applied after the sources
# are compiled, so it silently does nothing -- the image still carried the default zeros, and an
# OTA could not have been told from a no-op. That mattered here: proving OTA works is the gate on
# pulling the USB cable, and the proof needs the version to visibly change.
Import("env")  # noqa: F821
import subprocess

try:
    d = env.subst("$PROJECT_DIR")  # noqa: F821
    sha = subprocess.check_output(["git", "rev-parse", "--short=12", "HEAD"], cwd=d, text=True).strip()
    if subprocess.call(["git", "diff", "--quiet", "HEAD"], cwd=d) != 0:
        sha += "+"          # built from a dirty tree: say so rather than imply a clean commit
except Exception:
    sha = "unknown"

env.Append(CPPDEFINES=[("NECK_FW_SHA", env.StringifyMacro(sha))])  # noqa: F821
print(f"neck_dial: firmware stamped {sha}")
