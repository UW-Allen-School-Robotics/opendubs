# Tablet kiosk app

A minimal Android app that turns the operator tablet into part of the robot: the dashboard, full
screen, awake, locked in front, with no notifications or update prompts. Why it exists and what it
must do: [`notes/design/tablet-device.md`](../../notes/design/tablet-device.md).

**It is a shell, never a UI of its own.** One screen holding a WebView of the dashboard, a
device-owner receiver, and a six-method JavaScript bridge. Anything a user sees is the dashboard.

## What it does

| | how |
|---|---|
| dashboard full screen, landscape | one `WebView`; system bars hidden |
| never sleeps | `FLAG_KEEP_SCREEN_ON`; as device owner also *stay on while plugged in* |
| locked in front — no status bar, notifications, Home or Overview | lock task mode, as device owner |
| no update prompts | system update policy: postpone (Android allows 30 days per update) |
| comes back after a reboot | it is the launcher (HOME), set as device owner |
| comes back after an update | an update stops the app; it brings itself back to the front, locked (`UpdateReceiver`) |
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
| `battery()` | the tablet's own battery, `{"level": 0..100, "charging": bool, "plugged": "ac"\|"usb"\|"wireless"\|"dock"\|null}` — the dashboard shows it beside the robot's. `plugged` is whether power is connected, which a charge limit makes different from charging |

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

1. Factory-reset it and finish setup **without adding any account** — no Google, no Samsung (device
   owner requires none on the device). Join the robot's Wi-Fi. Then turn on USB debugging (About
   tablet → Software information → tap *Build number* seven times → Developer options).
2. Install and make it device owner. **`-t` is required**: debug builds are `testOnly`, and without
   it `adb install` fails with `INSTALL_FAILED_TEST_ONLY`.

   ```bash
   adb install -t app/build/outputs/apk/debug/app-debug.apk
   adb shell dpm set-device-owner org.opendubs.kiosk/.KioskAdminReceiver
   adb shell dumpsys deviceidle whitelist +org.opendubs.kiosk     # exempt from battery optimisation
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
   To **replace** a CA — a bench CA before the tablet goes to a robot, or a robot's renewed one —
   add `--ez ca_clear true` to the same command: it removes every CA the app installed before
   installing the new one. On its own, `ca_clear` removes them all.
5. Do Not Disturb on *Alarms only*: `adb shell cmd notification set_dnd alarms`. Then the Samsung
   settings in `tablet-device.md` by hand (step into Settings with the maintenance commands below):
   *Put unused apps to sleep* off, auto-download of updates off, auto restart off, Edge panels off.
   The app is called **OpenDubs** on the tablet. It does not appear in Samsung's *Never sleeping
   apps* list: it is already exempt (device owner, and step 2's battery-optimisation exemption).
6. **Name the robot** the tablet belongs to — on the robot, `OPENDUBS_ROBOT_NAME` in
   `/etc/opendubs/robot.env` (`software/kiosk-dashboard/deploy/provision.sh` prints how). The
   dashboard shows it top left, so a tablet never drives the wrong robot.

**Maintenance.** Leave lock task, and later return to it:

```bash
adb shell am start -n org.opendubs.kiosk/.KioskActivity --ez unlock true
adb shell am start -a android.settings.SETTINGS                       # e.g. to change a setting
adb shell am force-stop com.android.settings                          # close it when done
adb shell am start -n org.opendubs.kiosk/.KioskActivity --ez lock true
```

`lock=true` is how maintenance ends: **a device-owner app cannot be force-stopped** (Android refuses,
"has an active device admin"), so waiting for the app to restart would wait forever. Close any
screen you opened, too: after an update the app brings itself back, but a screen left open behind
it comes to the front first.

**Updates** keep the device owner and every setting: `adb install -r -t app-debug.apk`. To reach
the tablet without the cable, `adb tcpip 5555` while it is on USB, then `adb connect <tablet-ip>:5555`;
that stays open on the network until the next reboot, so reboot it when done.

Remove the device owner (debug builds only):

```bash
adb shell dpm remove-active-admin org.opendubs.kiosk/.KioskAdminReceiver
```

## Status

Builds (Android Gradle plugin 9.4.1, Gradle 9.8.1, compile and target SDK 36, min SDK 28). **Runs on
the team's Galaxy Tab A11 (SM-X133, Android 15, One UI 7)**, set up 2026-10-08 as above. Against a
bench dashboard on a laptop: TB-1, TB-2, TB-3 and TB-5 pass (`tablet-device.md` has the results);
TB-4 is a week of observation, begun that day; TB-6 needs the robot. The foreground service for audio
with the screen off is **not needed**: TB-2 played ten minutes straight through, four of them with
the screen off, on battery. Suspending Samsung's own update apps waits on TB-4.
