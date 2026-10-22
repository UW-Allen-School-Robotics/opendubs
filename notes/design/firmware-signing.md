# Signed firmware updates: the StamPLC and the neck Dial

**The problem.** Two boards update themselves over the network when told to:

- **The StamPLC**, the safety controller: `OTA <url>` on UDP 5005.
- **The neck Dial**, the head's pan/tilt: `OTA <url>` on UDP 4242.

Neither command is authenticated, so anyone on the robot's network could install any firmware on
either board. Once the repository is public, the command set is public too.

**The goal.** Each board installs only an image signed on the Thor **for that board**. A bad, missing
or wrong-board signature means the image is rejected, the board says so, and the running firmware is
untouched and still the one that boots.

## The pieces

| | what | where |
|---|---|---|
| **one check** | shared by both boards: ECDSA P-256 over SHA-256, plain mbedTLS, a boot self-test | `firmware/common/ota_verify/` |
| **one signing step** | `sign-firmware.sh <board> <image>` writes `<image>.sig`, served beside the image | `software/stack/firmware-signing/` |
| **one key** | the private half only on the Thor; the public half committed, so every builder compiles in the same key | Thor: `/etc/opendubs/firmware-signing/`; repo: `firmware/common/ota_verify/keys/` |
| StamPLC side | `otaHttpPull()` fetches the `.sig`, hashes as it writes, decides before `Update.end()` | `firmware/rc_serial_probe` (this lane) |
| Dial side | the same pattern, written against the library's interface | `firmware/neck_dial` (the Tablet lane) |

## Decisions

**An app-level check in the OTA handler, not ESP-IDF's signed apps.** Both boards use the Arduino
core, with ESP-IDF prebuilt. The StamPLC is on espressif32 7.0.1, ESP-IDF 4.4 and mbedTLS 2.28; the
Dial on a newer platform with mbedTLS 3.x. ESP-IDF's signed-app verification is an `sdkconfig` option
compiled into those prebuilt libraries, so it would mean a hybrid build or a framework fork on each
board. The app-level check needs neither. It's one source file that both boards and a laptop build
unchanged, and it uses only mbedTLS's `md`/`pk` calls, which are the same in 2.28 and 3.x. mbedTLS
2.28 has no Ed25519, so the algorithm is P-256.

**One project key, with the board's name inside every signature.** The signed message is
`"OPENDUBS-FW-SIG-1" \0 <board> \0 <image>`.

- Per-board keys would add a second private key to back up and rotate without separating anything,
  because both would sit on the same Thor.
- What per-board keys *would* prevent is a cross-board install. The board name in the signature
  prevents exactly that. Both boards are ESP32-S3s: without it, a correctly signed StamPLC image sent
  to the Dial would pass the check, boot, and strand a board that has no USB cable.

**The public key is committed.** It isn't secret. Committing it means the Dial, which is built on a
laptop, and the StamPLC, built on the Thor, compile in the same reviewed key, and its id is in the
repository's history. Trusting a new key is a reviewed change.

**Fail closed, except where failing closed would strand a board.**

- A build with no key rejects every update; the Dial's won't even build.
- A failed self-test rejects everything on the StamPLC, which has USB. On the Dial it falls back to
  report-only, below.

## How an update flows

1. The Thor builds (the StamPLC) or receives (the Dial) the image, and runs
   `sudo sign-firmware.sh <board> <image>`. The script signs, checks the signature against the key's
   public half, and writes `<image>.sig`.
2. The image and its `.sig` are served over HTTP. The board gets `OTA <url>`.
3. The board does the rest, in this order:
   - It refuses at once if it trusts no key, or if its self-test failed and it can be recovered over
     USB (the StamPLC).
   - It fetches `<url>.sig`.
   - It streams the image into the inactive slot, hashing every byte.
   - It runs `otaVerifyFinish(...)` **before** `Update.end(true)`. On failure, `Update.abort()` runs:
     the new slot is never selected, and the running app stays the one that boots.
4. Each board reports the key ids it trusts and whether its self-test passed. The StamPLC does this
   in its `[NET]` telemetry line; the Dial in its `PING` reply, as the Tablet lane decides.

## Keys, rotation and loss

- **The private key** lives in `/etc/opendubs/firmware-signing/` on the Thor (root, 0700; key 0400),
  the same place and style as the kiosk signing key. There is one project key for every robot, and a
  second robot imports it from the backup. **The backup is what keeps a Dial reachable:** take it
  before the first signed image ships, and keep it in the password manager.
- **Rotation** never leaves a board trusting only a key we can't use:
  1. `--next` creates the next key, and its public half is committed beside the current one.
  2. Every board is updated, signed with the current key, to firmware that trusts **both**.
  3. Only then does `--promote` make the next key current.
  4. Updates are now signed with the new key, which every board already trusts.
  5. Finally the old public key is removed, and the next update narrows each board to the new key.

  If anything goes wrong in the middle, every board still trusts a key we hold.
- **A lost key** can't be recovered. The way back is USB: on the robot for the StamPLC
  (`flash-from-thor.sh`), at a desk for the Dial. Both are made near-impossible by the backup, by
  rotation never dropping a held key, and by the Dial refusing to build without a key.

## Bootstrap

**The StamPLC:**

1. Create the key, back it up, and commit its public half.
2. Make sure the robot's own `src/main.cpp` carries the check. It's **per robot**, because it holds
   that robot's drive calibration and deploys never overwrite it, and the OTA handler lives in it.
   Either merge it by hand once, or (**recommended**) move the per-robot constants into a per-robot
   header so `main.cpp` deploys normally. `ota-from-thor.sh` warns until this is done.
3. Run `sudo ota-from-thor.sh` once. The running firmware predates signing, ignores the `.sig` and
   installs the new image the old way. From then on, it checks every update.

**The Dial.** No USB cable means every step is proven before the next one depends on it:

1. **Image A: report-only** (`-DOTA_SIG_REPORT_ONLY`). It checks every update and reports the verdict,
   but installs anyway. It's installed by the Dial's current, unsigned OTA, the documented recipe,
   with the user present. It reports its key id and `otaverify=ok`.
2. **Prove the check on this unit, still on A:**
   - a correctly signed update of A must report *signature valid* and install;
   - an update of A with a corrupted `.sig` must report *would reject* and install anyway. That's
     harmless, because it's the same firmware.
3. **Image B: enforcing**, built from the same check. It's installed signed, so A's verdict on it must
   be *valid*.
4. **Prove enforcement on B:** a corrupted `.sig` must be **rejected**, with B still running. A signed
   update must install.

Never put an enforcing image on a Dial whose report-only image hasn't passed step 2. And if B's
self-test ever fails, B falls back to report-only and says so: a bug in the check cannot strand the
head.

## What this does not cover

- **Denial of service.** Anyone on the network can still send `OTA`. The StamPLC opens its drive
  contactor before it can know whether the image is good, so the robot stops until the e-stop is
  cleared. That's a stop, not code execution. Checking first would mean buffering the whole image.
- **Rollback.** An older image that was signed correctly can be reinstalled. There is no version
  floor yet.
- **Physical access.** USB flashing isn't checked; there's no secure boot, by choice, because eFuses
  are irreversible.
- **Confidentiality.** Images travel over plain HTTP. The signature covers integrity, and the firmware
  is not secret.

## Tests (desk only; nothing is flashed from a desk)

- `firmware/common/ota_verify/tests/test_ota_verify.sh` builds mbedTLS and the check on a laptop, and
  signs with the Thor's own `sign-firmware.sh` against scratch keys. 14 cases:
  - the boot self-test;
  - this board's image passes, and the other board's image is refused;
  - a changed image byte, a changed signature byte, an untrusted key, and truncated, random or missing
    signatures are refused;
  - a build that trusts no key is refused;
  - during a rotation, both keys pass; after it, the retired key is refused.
- StamPLC firmware builds against its own toolchain, trusting no key and trusting two keys: both
  compile and link the shared library through the symlink.
- `provision-firmware-signing.sh`, the key lifecycle, as a laptop dry run.
