# Firmware signing: the Thor signs every StamPLC update

The StamPLC (the robot's safety controller) installs firmware over the network when it receives an
`OTA <url>` command. Anyone on the robot's network can send that command, so the image itself has to
prove where it came from. Every image is signed on the Thor with a key that never leaves it, and the
firmware installs only an image whose signature checks against the public key compiled into it.
Design and reasoning: [notes/design/firmware-signing.md](../../../notes/design/firmware-signing.md).

| script | runs | does |
|---|---|---|
| `provision-firmware-signing.sh` | once per project, as root | creates the signing key, or `--import`s it from its backup; `--next` / `--promote` rotate it; `--show` prints key ids |
| `firmware/rc_serial_probe/ota-from-thor.sh` | every update, with `sudo` | compiles the public key into the build, signs the image, checks the signature, serves both |
| `firmware/rc_serial_probe/flash-from-thor.sh` | USB recovery only | compiles the public key in, so the recovered board accepts signed updates again |

## Where the key lives

`/etc/opendubs/firmware-signing/`, root-owned, mode `0700`:

| file | mode | holds |
|---|---|---|
| `ota.key` | `0400` | the ECDSA P-256 private key. Never printed, never in the repository |
| `ota.pub.der` | `0444` | its public key, compiled into every build |
| `ota.next.key`, `ota.next.pub.der` | | only during a rotation |
| `ota.retired-<date>.*` | | the previous key after a rotation, until you delete it |

The **key id** is the first 16 hex digits of the public key's SHA-256. `provision-firmware-signing.sh
--show` prints it, and the board reports the key it trusts as `otakey=` in its `[NET]` telemetry line,
every 5 seconds. `otakey=none` means the board accepts no network update at all.

## First robot: set it up

Run these from the dev machine, in this order, with someone at the robot.

1. **Create the key:**
   ```bash
   ssh -t opendubs2 sudo /opt/opendubs/stack/firmware-signing/provision-firmware-signing.sh
   ```
2. **Back up the key.** Do this before anything else. Without the key, the StamPLC can only be
   updated over USB:
   ```bash
   ssh opendubs2 'sudo tar -C /etc/opendubs/firmware-signing -cf - ota.key' > ~/opendubs-firmware-signing.tar
   ```
   Attach `~/opendubs-firmware-signing.tar` to a password-manager item named
   **"OpenDubs firmware signing key"**, note the key id in it, then delete the local copy:
   ```bash
   rm ~/opendubs-firmware-signing.tar
   ```
3. **Install the first signing-capable firmware.** This is the bootstrap: the board's current firmware
   doesn't check signatures, so it installs this one as before. From then on, the board checks every
   update. See the design note's *Bootstrap* section first. The robot's own `src/main.cpp` must carry
   the signature check, or the script warns that it doesn't:
   ```bash
   ssh -t opendubs2 sudo /opt/opendubs/rc_serial_probe/ota-from-thor.sh
   ```
4. **Check:** the board's `[NET]` line reports `otakey=<the id from step 1>`.

## Another robot

One project key signs for every robot. Download the backup tar from the password manager, then:

```bash
scp ~/opendubs-firmware-signing.tar NEWROBOT:/tmp/fws.tar
```
```bash
ssh -t NEWROBOT 'sudo /opt/opendubs/stack/firmware-signing/provision-firmware-signing.sh --import /tmp/fws.tar && rm /tmp/fws.tar'
```
```bash
rm ~/opendubs-firmware-signing.tar
```

## Rotate the key

The board trusts one key at a time, so the update that installs the new key is signed with the old one.

1. `sudo provision-firmware-signing.sh --next` creates the next key.
2. `sudo ota-from-thor.sh` builds firmware that trusts the **next** key and signs it with the
   **current** key, which is what the board running now accepts.
3. When the board reports `otakey=<next id>`, run `sudo provision-firmware-signing.sh --promote`.
4. Back up the new key (step 2 of *First robot*), and replace the old backup.
5. On every other robot, import the new key the same way. Delete `ota.retired-*` once every board
   reports the new id.

## If the key is lost

A lost key can't be recovered, and a board that trusts it accepts no network update from anyone. The
way back is **USB, with someone at the robot**:

1. Create a new key (*First robot*, steps 1 and 2).
2. Plug the StamPLC into the Thor over USB and run `/opt/opendubs/rc_serial_probe/flash-from-thor.sh`.
   It compiles the new public key in. USB flashing doesn't check signatures; physical access is the
   root of trust.
3. Import the new key on every other robot, and reflash each board the same way.

## Dry run on a laptop

`OPENDUBS_FW_KS_DIR=/tmp/x provision-firmware-signing.sh` runs without root against a scratch folder.
Never point it at a real key. On a robot, `sudo` drops the variable. The firmware's own check has a
host test that needs no board: `firmware/rc_serial_probe/tests/test_ota_verify.sh`.
