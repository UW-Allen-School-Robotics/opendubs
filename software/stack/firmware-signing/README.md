# Firmware signing: the Thor signs every board's updates

Two boards install firmware over the network when told to:

- **the StamPLC**, the robot's safety controller (`OTA <url>` on UDP 5005);
- **the neck Dial**, which drives the head's pan and tilt (`OTA <url>` on UDP 4242).

Anyone on the robot's network can send those commands, so the image itself has to prove where it
came from. Every image is signed on the Thor, for one named board, with a key that never leaves the
Thor. Each board installs only an image whose signature checks against a key compiled into it, for
its own name.

Design and reasoning: [notes/design/firmware-signing.md](../../../notes/design/firmware-signing.md).
The check the boards run: [firmware/common/ota_verify](../../../firmware/common/ota_verify/README.md).

| script | runs | does |
|---|---|---|
| `provision-firmware-signing.sh` | once per project, as root | creates the signing key, or `--import`s it from its backup; `--next` / `--promote` rotate it; `--show` prints key ids |
| `sign-firmware.sh <board> <image>` | every update, as root | **the one signing step**: signs `<image>` for `stamplc`, `neck` or `stage`, checks the signature, writes `<image>.sig` |
| `firmware/rc_serial_probe/ota-from-thor.sh` | every StamPLC update, with `sudo` | builds, signs (with `sign-firmware.sh stamplc`), serves the image and its `.sig`, sends `OTA` |
| the neck Dial's OTA recipe (`firmware/neck_dial/README.md`) | every Dial update | the same, with `sign-firmware.sh neck` |
| `software/stage-pi/release/publish-bundle.sh` | every deploy that changes `software/stage-pi`, from `deploy-all.sh` | signs the stage box's bundle manifest with `sign-firmware.sh stage`; the box verifies it against the same committed public keys |

## Where the key lives

On the Thor, in `/etc/opendubs/firmware-signing/` (root-owned, mode `0700`):

| file | mode | holds |
|---|---|---|
| `ota.key` | `0400` | the ECDSA P-256 private key. Never printed, never in the repository |
| `ota.pub.der` | `0444` | its public half |
| `ota.next.key`, `ota.next.pub.der` | | only during a rotation |
| `ota.retired-<date>.*` | | the previous key after a rotation, until you delete it |

**The boards trust the public keys committed in `firmware/common/ota_verify/keys/`**, not the files
above. A public key isn't secret, and committing it means every machine that builds firmware compiles
in the same, reviewed key.

The **key id** is the first 16 hex digits of the public key's SHA-256. `--show` prints it, it names the
file in `keys/`, and each board reports the ids it trusts. The StamPLC reports `otakeys=` in its `[NET]`
telemetry line every 5 seconds.

## First robot: set it up

Run these from the dev machine, in this order, with someone at the robot.

1. **Create the key:**
   ```bash
   ssh -t opendubs2 sudo /opt/opendubs/stack/firmware-signing/provision-firmware-signing.sh
   ```
2. **Back up the key.** Do this before anything else. Without it, the StamPLC can only be updated over
   USB, and the neck Dial, which normally runs without a USB cable, only after a visit to fit one:
   ```bash
   ssh opendubs2 'sudo tar -C /etc/opendubs/firmware-signing -cf - ota.key' > ~/opendubs-firmware-signing.tar
   ```
   Attach `~/opendubs-firmware-signing.tar` to a password-manager item named
   **"OpenDubs firmware signing key"**, note the key id in it, then delete the local copy:
   ```bash
   rm ~/opendubs-firmware-signing.tar
   ```
3. **Commit the public half,** named by its id (the id step 1 printed), in a pull request, so every
   build trusts it:
   ```bash
   ssh opendubs2 'sudo cat /etc/opendubs/firmware-signing/ota.pub.der' > firmware/common/ota_verify/keys/<id>.der
   ```
   ```bash
   python3 firmware/common/ota_verify/tools/ota_keys.py
   ```
   Commit the `.der` and the regenerated `src/ota_keys.h`. Once it's merged, deploy `main` to the robot.
4. **Bootstrap each board** as the design note's *Bootstrap* section says. For the StamPLC:
   `ssh -t opendubs2 sudo /opt/opendubs/rc_serial_probe/ota-from-thor.sh`. The Dial goes through a
   report-only image first.
5. **Check** that each board reports the key id from step 1.

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

The order matters: a Dial that ends up trusting only a key we don't use needs a visit with a USB cable.

1. `sudo provision-firmware-signing.sh --next` creates the next key. Back it up.
2. Commit its public half **beside** the current one in `firmware/common/ota_verify/keys/` (as in
   step 3 above), so new firmware trusts **both**.
3. Update **every** board, on every robot, to that firmware. It's still signed with the current key,
   which every board trusts. Check that each one reports both ids.
4. Only then: `sudo provision-firmware-signing.sh --promote`. Updates are now signed with the new
   key, which every board already trusts.
5. Remove the old key's `.der` from `keys/`, and update every board once more, so each trusts only
   the new key. Then delete `ota.retired-*` on the Thor, and replace the password-manager backup.

If a rotation is interrupted at any point, every board still trusts a key we hold.

## If the key is lost

A lost key can't be recovered, and a board that trusts only it accepts no network update from anyone.
The way back is USB:

1. Create a new key (*First robot*, steps 1 to 3).
2. Reflash each board over USB. Neither USB path checks signatures, because physical access is the
   root of trust.
   - **The StamPLC:** on the robot, with `/opt/opendubs/rc_serial_probe/flash-from-thor.sh`.
   - **The Dial:** with a USB cable fitted for the visit (`firmware/neck_dial/README.md`).
3. Import the new key on every other robot.

The backup, and a rotation that never drops a key we hold, exist so that this never happens to a Dial.

## Dry run on a laptop

`OPENDUBS_FW_KS_DIR=/tmp/x provision-firmware-signing.sh` and `OPENDUBS_FW_KS_DIR=/tmp/x
sign-firmware.sh neck image.bin` run without root against a scratch folder. Never point them at a real
key. On a robot, `sudo` drops the variable. The boards' check has a host test that needs no board:
`firmware/common/ota_verify/tests/test_ota_verify.sh`.
