# Tablet kiosk app

A minimal Android app that turns the operator tablet into part of the robot: the dashboard, full
screen, awake, locked in front, with no notifications or update prompts. Why it exists and what it
must do: [`notes/design/tablet-device.md`](../../notes/design/tablet-device.md).

**It is a shell, never a UI of its own.** One screen holding a WebView of the dashboard, a
device-owner receiver, and a five-method JavaScript bridge. Anything a user sees is the dashboard.

## What it does

| | how |
|---|---|
| dashboard full screen, landscape | one `WebView`; system bars hidden |
| never sleeps | `FLAG_KEEP_SCREEN_ON`; as device owner also *stay on while plugged in* |
| locked in front — no status bar, notifications, Home or Overview | lock task mode, as device owner |
| no update prompts | system update policy: postpone (Android allows 30 days per update) |
| comes back after a reboot | it is the launcher (HOME), set as device owner |
| microphone for the dashboard | granted as device owner; the page gets audio capture **only** for the dashboard's own origin |
| music starts on cue | autoplay allowed (`mediaPlaybackRequiresUserGesture = false`) |
| HTTPS on an offline LAN | installs the robot's own CA itself (`ca_b64`, device owner) and trusts it (`res/xml/network_security_config.xml`); cleartext stays allowed so `http://…:8080` still works |

Every device-owner step is a no-op when the app is not device owner, so it also runs as an ordinary
app on any tablet for development.

**The bridge**, as `window.OpenDubsKiosk` in the page (absent in a plain browser):

| method | |
|---|---|
| `keepAwake(on)` | keep the screen on, or let it sleep |
| `wake()` | turn the screen on and bring the dashboard to the front (no reload) |
| `screenState()` | `"on"` or `"off"` |
| `isDeviceOwner()` | whether lock task and the update policy are in force |
| `version()` | the app's version |

## Build

Needs a JDK 17+ (Android Studio's bundled one works) and the Android SDK.

```bash
export JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home"
echo "sdk.dir=$HOME/Library/Android/sdk" > local.properties
./gradlew assembleDebug        # app/build/outputs/apk/debug/app-debug.apk
```

The first build downloads Gradle, the Android Gradle plugin, and any missing SDK packages.

**Debug builds are `testOnly`.** A device owner provisioned from one can be removed again without a
factory reset (below). **A release build must be signed**, and every later release with the *same
key* — lose it and the app can only be replaced by a factory reset. Keep the keystore in the team's
password manager, never in this repository (`.gitignore` excludes `*.jks` and `*.keystore`).

## Set up a tablet

At the bench, with the tablet on USB and USB debugging on. **This erases the tablet.**

1. Factory-reset it and finish setup **without adding a Google account** (device owner requires no
   accounts on the device).
2. Install and make it device owner:

   ```bash
   adb install app/build/outputs/apk/debug/app-debug.apk
   adb shell dpm set-device-owner org.opendubs.kiosk/.KioskAdminReceiver
   ```

3. On the robot, once: `sudo software/kiosk-dashboard/deploy/make-robot-ca.sh`, then
   `sudo systemctl restart opendubs-dash`. That makes this robot's own CA and the dashboard's
   certificate, and brings HTTPS up on **8443** beside the unchanged HTTP on 8080.
4. Install the robot's CA and point the app at the HTTPS dashboard (both remembered). The app, as
   device owner, installs the CA itself — by hand Android would demand a lock-screen PIN:

   ```bash
   adb shell am start -n org.opendubs.kiosk/.KioskActivity \
       --es ca_b64 "$(ssh <robot> base64 -w0 /etc/opendubs/tls/ca.crt)" \
       --es url https://<robot>.local:8443/
   ```

   Without HTTPS it also runs against `http://<robot>:8080/`, minus the microphone and wake lock.
5. Turn off the Samsung settings listed in `tablet-device.md` (app sleeping, auto restart, edge
   panels, auto-download of updates; Do Not Disturb on *Alarms only*).

**Maintenance.** Leave lock task until the app restarts:

```bash
adb shell am start -n org.opendubs.kiosk/.KioskActivity --ez unlock true
```

Remove the device owner (debug builds only):

```bash
adb shell dpm remove-active-admin org.opendubs.kiosk/.KioskAdminReceiver
```

## Status

Builds (Android Gradle plugin 9.4.1, Gradle 9.8.1, compile and target SDK 36, min SDK 28). **Not
yet run on the tablet**: experiments TB-1 to TB-6 in `tablet-device.md` are the acceptance test.
Not built yet, by design until a test shows the need: a foreground service to keep audio alive with
the screen off (TB-2), and suspending Samsung's own update apps (FreeKiosk's MIT code, if the update
policy alone is not enough — TB-4).
