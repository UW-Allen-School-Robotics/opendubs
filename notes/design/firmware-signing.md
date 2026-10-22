# Signed firmware updates for the StamPLC

**The problem.** The StamPLC, the robot's safety controller, reflashes itself when it receives
`OTA http://<host>:<port>/<path>` on UDP 5005. The command is unauthenticated, so anyone on the robot's
network could install any firmware on the safety controller. Once the repository is public, the
command set and the unsigned OTA are visible to anyone.

**The goal.** The board installs only images signed with the project's key. A bad or missing
signature means the image is rejected, the board says so, and it keeps running its current firmware.

## The choice: an app-level check in the OTA handler

| | ESP-IDF signed apps | app-level ECDSA check (**chosen**) |
|---|---|---|
| where the check runs | inside ESP-IDF's OTA and bootloader code, set by `sdkconfig` | `otaHttpPull()`, before `Update.end()` makes the image bootable |
| fits this build | **no.** The firmware uses the Arduino core (espressif32 7.0.1, Arduino 2.0.17, ESP-IDF 4.4) with ESP-IDF prebuilt. Changing `sdkconfig` means a hybrid Arduino+IDF build or a framework fork | yes. Plain mbedTLS, already in the image |
| testable on a laptop | no | yes, the same source file (`tests/test_ota_verify.sh`) |
| also covers USB flashing | no (that needs secure boot, which burns eFuses irreversibly) | no |

The app-level check is the more robust choice **for this toolchain**. It needs no framework change
that a platform update could silently undo, and it's small enough to test exactly as it ships.

**What it is:** ECDSA P-256 over SHA-256, the detached signature in DER, checked with mbedTLS's
generic `md`/`pk` calls. They are the same in mbedTLS 2.28 on the board and 3.x on a laptop. Ed25519
would be smaller, but mbedTLS 2.28 doesn't have it.

## How an update flows

1. `sudo ota-from-thor.sh` on the Thor (still the one command) does four things.
   - It writes `include/ota_pubkey.h` from the Thor's public key. The header is generated on every
     build and never committed.
   - It builds, then signs `firmware.bin` into `firmware.bin.sig`.
   - It checks the signature itself.
   - It serves both files and sends `OTA <url>`.
2. The board does the rest, in this order.
   - It refuses at once if it has no compiled-in key (`otakey=none`). It doesn't touch the drive bus.
   - It opens the drive contactor, as before.
   - It fetches `<path>.sig` (at most 160 bytes).
   - It streams the image into the inactive OTA slot, hashing as it writes.
   - It verifies the signature before `Update.end()`. If the check fails, `Update.abort()` runs, the
     new slot is never selected, `[OTA] REJECTED: <why>` goes out, and the current firmware keeps
     running.
3. The board reports the key it trusts as `otakey=<id>` in its `[NET]` telemetry every 5 s.

**A build without the key header trusts no key and refuses every update.** It fails closed: a laptop
build, or a USB recovery on a Thor with no key, can never produce firmware that accepts anything.

## Keys

The private key lives only on the Thor, in `/etc/opendubs/firmware-signing/` (root, 0700; key 0400).
That's the same place and the same style as the kiosk signing key: one project key for every robot, a
password-manager backup taken by hand, and `--import` on another robot. The public key is compiled in;
it isn't secret. Procedures: `software/stack/firmware-signing/README.md`.

## Bootstrap

1. Create the key and back it up.
2. Run `ota-from-thor.sh` once. The board's current firmware doesn't fetch the `.sig` and installs the
   signing-capable image the unsigned way. The script reports which of the two happened.
3. From then on, enforcement is on.

**This needs one decision first: `src/main.cpp` is per robot.** Each robot keeps its own copy, because
it carries that robot's drive calibration (`notes/per-robot-calibration.md`). Deploys never overwrite
it, and the OTA handler lives in it. So this repository's change does not reach a robot until that
robot's `main.cpp` has it. `ota-from-thor.sh` warns plainly while it doesn't. There are two ways to get
it there, both at the robot with the maintainers:

- **(A) Merge by hand, once:** diff the robot's `main.cpp` against the repository's (`tools/deploy-audit.py`
  shows it), and bring the robot's copy to the repository's version with its per-robot constants
  kept. That's quick, but it repeats every time `main.cpp` changes.
- **(B) Move the per-robot constants into a per-robot header** (`src/robot_config.h`, never deployed;
  the repository has an example), so `main.cpp` becomes an ordinary deployed file. That's a little
  more work once, and it's the fix `per-robot-calibration.md` already names as the real one.

The recommendation is **(B)**, done at the same robot session as the bootstrap. Until either is done,
the robot is exactly as it is today: unsigned updates are still accepted.

## Recovery and rotation

- **A lost key** can't be recovered. The way back is USB with someone at the robot:
  `flash-from-thor.sh` with a new key. USB flashing is not signature-checked, because physical access
  is the root of trust. There is no secure boot, deliberately (eFuses can't be undone).
- **Rotation** works because the board trusts one key at a time. `--next` creates the next key, and
  `ota-from-thor.sh` builds firmware that trusts the next key, signed with the current one. When the
  board reports the new id, run `--promote`.

## What this does not cover

- **Denial of service.** Anyone on the network can still send `OTA`. The board opens the drive
  contactor before it can know whether the image is good, so the robot stops until the e-stop is
  cleared. That is a stop, not code execution, and it matches the network-first security stance.
  Checking the signature before opening the contactor would need the whole image buffered first.
- **Rollback.** An older image that was signed correctly can be reinstalled. There is no version
  floor yet.
- **USB and physical access**, as above.
- **Confidentiality.** The image travels over plain HTTP. That's fine, because the signature covers
  integrity, and the firmware is not secret.

## Tests

- `firmware/rc_serial_probe/tests/test_ota_verify.sh` builds mbedTLS and the board's own
  `ota_verify.cpp` on a laptop. It signs with OpenSSL exactly as the Thor does, and checks eight cases:
  - accepted: the signed image only;
  - refused: a changed image byte, a changed signature byte, another key, a truncated signature,
    random bytes as the signature, no signature, and a build with no key.
- Desk builds of the firmware with and without a key header both compile and link against the board's
  own toolchain.
- Nothing is flashed from a desk. The first real run is `sudo ota-from-thor.sh`, with someone at the
  robot.

## Later: the neck Dial

The Dial pulls its updates the same way (`OTA <url>` on UDP 4242) and should get the same check. But
it has no USB cable on the robot: **a mistake there can't be undone without taking the head apart.**
So, before it's enabled:

- prove the check on a bench Dial;
- make a keyless or wrong-key Dial image impossible to build, rather than just warned about;
- prove a full signed round trip (sign, install, report the key id, reject a bad image) on the robot
  while the old path is still there;
- consider letting the Dial trust **two** keys during a rotation, so a half-finished rotation can't
  lock it out.

None of that is built yet.
