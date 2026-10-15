# Kiosk signing: the Thor signs and serves the tablet's app

The tablet's kiosk app (`software/tablet-kiosk/`) updates itself from the robot: it asks the
dashboard for `/api/kiosk/latest` and installs a newer build when one is published. This folder makes
the Thor the place that build is **signed**, so the signing key lives on the robot (root-only) and
not on whichever laptop happened to build the app.

| script | runs | does |
|---|---|---|
| `provision-kiosk-signing.sh` | once per robot, as root | installs a headless Java, `apksigner` and `zipalign` (Ubuntu packages), creates the folders, and generates an RSA 4096 release key, or imports the project's key with `--import` |
| `rotate-kiosk-key.sh` | **once per project**, as root | proves that the release key succeeds the debug key that signed the tablet's first kiosk (below) |
| `sign-kiosk.sh <unsigned.apk> <kiosk-version.json>` | every app release, as root (`deploy-all.sh` calls it) | signs, checks the result, and publishes it |
| `common.sh` | sourced | paths and the pinned debug certificate |

## Where things live on the robot

| path | owner, mode | holds |
|---|---|---|
| `/etc/opendubs/kiosk-signing/` | `root`, `0700`; files `0400` | `release.p12` (the key), `release.pass` (its password), `release.sha256` (the certificate's fingerprint, public), `lineage` (the rotation proof), and `debug.keystore` once, for the rotation |
| `/var/lib/opendubs/kiosk/` | `root:kiosk`, `0755`; files `0644` | `opendubs-kiosk.apk` and `latest.json`, which the dashboard serves |

`latest.json` is `{"versionCode", "versionName", "sha256", "signer"}`: the version from the build's
`kiosk-version.json`, the SHA-256 of the signed APK, and the release certificate's fingerprint. The
APK is written first and `latest.json` last, each by rename, so the dashboard never offers a build
that isn't fully there.

The scripts never print the password and never copy the key off the robot. The only way it leaves is
the backup step below, which you run yourself.

## Why there is a rotation step

Android installs an update only if it is signed by the same key as the app already installed. The
tablet's first kiosk (0.1.0) was signed by the build Mac's default Android **debug** key, fingerprint
`e781fe8c…7777` (pinned in `common.sh`). The Android SDK generates that key at random on each
machine the first time it builds, so it is unique to the build Mac; only its password (`android`) is
a public default. Until the rotation it is the **only** key the tablet trusts: anyone holding the file
can sign an update the tablet will install, so keep it secret.

APK Signature Scheme v3 lets an app move to a new key: the new APK carries a **lineage**, a record
signed by the old key that names the new one as its successor. `rotate-kiosk-key.sh` makes that
record once. `sign-kiosk.sh` then signs every build with the release key plus the lineage. The
tablet accepts the first one as an update and, from then on, trusts only the release key: the
lineage grants the old key no rollback capability, so a build signed by the debug key alone is
refused. The debug key is not needed again.

`sign-kiosk.sh` refuses to publish unless the signed APK:

- verifies with v3;
- is signed by the release certificate;
- carries a lineage that starts at the pinned debug certificate;
- has its uncompressed entries 4-byte aligned, which Android 11 and later require.

## First robot: set it up

All of this is done by the maintainers at the robot, in this order. Commands are run from the dev Mac.

1. **Deploy and provision** (installs the tools, creates the folders, generates the release key):
   ```bash
   ./deploy-all.sh opendubs2
   ```
   ```bash
   ssh -t opendubs2 sudo /opt/opendubs/stack/kiosk-signing/provision-kiosk-signing.sh
   ```
   It prints the release certificate's SHA-256. That value is public; note it in the
   password-manager item below.

2. **Back up the release key**: do this before anything else. Without it, a lost or reinstalled Thor
   means the tablet can never be updated again without uninstalling the app:
   ```bash
   ssh opendubs2 'sudo tar -C /etc/opendubs/kiosk-signing -cf - release.p12 release.pass lineage' > ~/opendubs-kiosk-signing.tar
   ```
   Attach `~/opendubs-kiosk-signing.tar` to a password-manager item named
   **"OpenDubs kiosk signing key"**, then delete the local copy:
   ```bash
   rm ~/opendubs-kiosk-signing.tar
   ```
   (`lineage` doesn't exist until step 4. Repeat this backup after step 4, so that the item holds all three files.)

3. **Move the debug key to the robot, once.** Back it up first: attach `~/.android/debug.keystore`
   to the same password-manager item. Then copy it into the keystore folder:
   ```bash
   scp ~/.android/debug.keystore opendubs2:/tmp/odk-debug.keystore
   ```
   ```bash
   ssh -t opendubs2 'sudo install -m 0400 -o root -g root /tmp/odk-debug.keystore /etc/opendubs/kiosk-signing/debug.keystore && rm /tmp/odk-debug.keystore'
   ```

4. **Rotate:**
   ```bash
   ssh -t opendubs2 sudo /opt/opendubs/stack/kiosk-signing/rotate-kiosk-key.sh
   ```
   It refuses any debug key other than the pinned one. Now repeat the backup in step 2.

5. **Sign and publish the first release.** Build the unsigned app on the Mac
   (`./gradlew assembleRelease` in `software/tablet-kiosk/`, which also writes `kiosk-version.json`
   beside the APK), then deploy. `deploy-all.sh` finds the build and runs `sign-kiosk.sh`. Builds in
   another checkout are passed by path:
   ```bash
   OPENDUBS_KIOSK_APK=/path/to/app-release-unsigned.apk ./deploy-all.sh opendubs2
   ```

6. **Check on the tablet** that the rotated build installed over 0.1.0 as an update. Then shred the
   debug key on the robot. It stays only in the password-manager backup:
   ```bash
   ssh -t opendubs2 sudo shred -u /etc/opendubs/kiosk-signing/debug.keystore
   ```

## Another robot

One project key signs the kiosk on every robot, so that a tablet can be updated by any of them. A
second robot **imports** the key from the backup and does not rotate again: the lineage travels with
the key. Download the tar from the password-manager item to the Mac, then:

```bash
scp ~/opendubs-kiosk-signing.tar NEWROBOT:/tmp/odk.tar
```
```bash
ssh -t NEWROBOT 'sudo /opt/opendubs/stack/kiosk-signing/provision-kiosk-signing.sh --import /tmp/odk.tar && rm /tmp/odk.tar'
```
```bash
rm ~/opendubs-kiosk-signing.tar
```

`--import` refuses to overwrite a key that is already installed. Running `provision-kiosk-signing.sh`
on a new robot **without** `--import` generates a different key, and a tablet updated by the first
robot will refuse that robot's builds.

## Dry run on a laptop

Every path can be overridden, which allows a run with throwaway keys and no root:
`OPENDUBS_KIOSK_KS_DIR`, `OPENDUBS_KIOSK_OUT_DIR`, `OPENDUBS_KIOSK_OUT_GROUP`,
`OPENDUBS_KIOSK_DEBUG_SHA256` (the throwaway debug key's fingerprint), and `APKSIGNER` (for example
`java -jar <sdk>/build-tools/<ver>/lib/apksigner.jar`). Never point these at a real key. On a robot,
`sudo` drops them.

## Licences

`openjdk-17-jre-headless` (GPL-2.0 with the Classpath exception), `apksigner` and `zipalign` (Apache-2.0) are
installed from Ubuntu's archive at provision time and run only on the robot. Nothing from them is
committed here or redistributed.
