# Tablet kiosk app

A minimal Android app that turns the operator tablet into part of the robot: the dashboard, full
screen, awake, locked in front, with no notifications or update prompts. Why it exists and what it
must do: [`notes/design/tablet-device.md`](../../notes/design/tablet-device.md).

**It is a shell, never a UI of its own.** One screen holding a WebView of the dashboard, a
device-owner receiver, and a small JavaScript bridge. Anything a user sees is the dashboard.

## What it does

| | how |
|---|---|
| dashboard full screen, landscape | one `WebView`; system bars hidden |
| awake on the robot's power | `FLAG_KEEP_SCREEN_ON`; as device owner also *stay on while plugged in* |
| on battery, saves power and then sleeps (from 0.5.0) | the tablet is powered from the robot's drive bus, so on battery the robot is off or e-stopped. At once: battery mode (the face dims to half, draws half the frames and stops camera tracking). The screen then goes off (`lockNow`) after **2 min** if the robot's dashboard does not answer (off), or **10 min** if it does (e-stopped, so the stopped face stays up a while). Below **15 %** on battery it sleeps at once and stays asleep; woken by hand it shows "Battery low" for 10 s. When the robot's power returns the screen **wakes by itself**. `Power.kt` |
| sleep and restart from the dashboard (from 0.5.0) | Head & face page: SLEEP TABLET, and RESTART TABLET (asks twice; refused during a show). The app asks the robot for them itself every 5 s, from native code, so a restart reaches a tablet whose page is dead; the page passes them on at once when it is alive |
| a Zoom call from the dashboard (from 0.6.0) | Head & face: START CALL with a Zoom meeting link opens it in the **Zoom app**, the one other app lock task lets in front; END CALL brings the face back and stops Zoom, which ends the call. While a call is up the face, the head's tracking and the robot's voice are off, the app does not self-update, and shows and RESTART TABLET are refused. `Call.kt` |
| locked in front — no status bar, notifications, Home or Overview | lock task mode, as device owner. One system feature is allowed (from 0.5.0): the **power menu**, so holding the side key offers Power off and Restart (setup step 5) |
| no update prompts | system update policy: postpone (Android allows 30 days per update) |
| comes back after a reboot | it is the launcher (HOME), set as device owner |
| comes back after an update | an update stops the app; it brings itself back to the front, locked (`UpdateReceiver`) |
| microphone for the dashboard | granted as device owner; the page gets audio capture **only** for the dashboard's own origin |
| cameras and location | granted as device owner; the page gets video capture (both cameras) and geolocation **only** for the robot's own origin. The face shows an indicator whenever a camera is open (`cameras()` below) |
| music starts on cue | autoplay allowed (`mediaPlaybackRequiresUserGesture = false`) |
| waits for the robot | if the dashboard does not answer (the tablet often starts before the Thor), a waiting page says so and retries — 2 s, backing off to 10 s; 30 s if the robot's certificate is not trusted — then loads the dashboard by itself |
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
| `setBrightness(v)`, `setMediaVolume(v)`, `getMediaVolume()` | the screen's brightness and the media volume, 0..1 (head protocol v5 `BRIGHT`, `VOLUME`) |
| `motion()` | the accelerometer samples since the last call, `[[ax, ay, az, age_ms], …]` (m/s², gravity included, the tablet's axes; no gyroscope). Read by the app, not the page's DeviceMotion, which stopped after every app restart |
| `light()` | ambient light, `{"lux": n, "age_ms": n}`, or `null` with no reading |
| `cameras()` | each camera, `[{"id", "facing": "screen"\|"back"\|"external", "open": bool}]`; `open` is true while ANY app has it open, so the face's camera indicator follows the hardware |
| `thermal()` | `{"status": "none"…"shutdown"\|null, "battery_c": n}`: Android's throttling level and the battery's temperature |
| `power()` | `{"battery_mode", "low", "level", "robot_answers", "sleep_in_s"}` (from 0.5.0): the face saves power while `battery_mode` is true |
| `sleep()`, `command(id, cmd)` | the screen off now; a dashboard command (`"sleep"`, `"restart"`) passed on by the page, run once by its id |
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
   device owner, installs the CA itself — by hand Android would demand a lock-screen PIN.
   Every setting goes to `ProvisionActivity`, which only adb can start (it requires
   `android.permission.DUMP`, which the adb shell holds and no ordinary app can be granted), so
   another app on the tablet cannot repoint the kiosk, add a CA or leave lock task. The app logs
   what it did under the tag `OpenDubsKiosk` (`adb logcat -s OpenDubsKiosk`).

   ```bash
   adb shell am start -n org.opendubs.kiosk/.ProvisionActivity \
       --es ca_b64 "$(ssh <robot> base64 -w0 /etc/opendubs/tls/ca.crt)" \
       --es url https://<robot>.local:8443/
   ```

   Without HTTPS it also runs against `http://<robot>:8080/`, minus the microphone and wake lock.
   To **replace** a CA — a bench CA before the tablet goes to a robot, or a robot's renewed one —
   add `--ez ca_clear true` to the same command: it removes every CA the app installed before
   installing the new one. On its own, `ca_clear` removes them all.
5. Do Not Disturb on *Alarms only*: `adb shell cmd notification set_dnd alarms`. Then the Samsung
   settings in `tablet-device.md` by hand (step into Settings with the maintenance commands below):
   *Put unused apps to sleep* off, auto-download of updates off, auto restart off, Edge panels off,
   and **Battery › Battery protection** on (it stops charging at 80%; a tablet that lives on the
   mount's power is otherwise held at 100%). Check it with `adb shell settings get global
   protect_battery` — `0` means off.
   **The side key** (from 0.5.0): holding it opens the power menu (Power off, Restart) only with
   `adb shell settings put global power_button_long_press 1`; Samsung's default sends the hold to its
   assistant, which lock task blocks, so it did nothing. A short press still turns the screen off and
   on. Check it with `adb shell settings get global power_button_long_press` (`1`).
   The app is called **OpenDubs** on the tablet. It does not appear in Samsung's *Never sleeping
   apps* list: it is already exempt (device owner, and step 2's battery-optimisation exemption).
6. **For calls (optional, from 0.6.0):** install **Zoom** (`us.zoom.videomeetings`) from the Play
   Store and sign in, in maintenance (below); the app lets it in front of lock task by itself. Join a
   test meeting once by hand so Zoom's camera and microphone permission prompts are answered.
7. **Name the robot** the tablet belongs to — on the robot, `OPENDUBS_ROBOT_NAME` in
   `/etc/opendubs/robot.env` (`software/kiosk-dashboard/deploy/provision.sh` prints how). The
   dashboard shows it top left, so a tablet never drives the wrong robot.

**Maintenance.** Leave lock task, and later return to it:

```bash
adb shell am start -n org.opendubs.kiosk/.ProvisionActivity --ez unlock true
adb shell am start -a android.settings.SETTINGS                       # e.g. to change a setting
adb shell am force-stop com.android.settings                          # close it when done
adb shell am start -n org.opendubs.kiosk/.ProvisionActivity --ez lock true
```

`lock=true` is how maintenance ends: **a device-owner app cannot be force-stopped** (Android refuses,
"has an active device admin"), so waiting for the app to restart would wait forever. Close any
screen you opened, too: after an update the app brings itself back, but a screen left open behind
it comes to the front first.

**Is it still a kiosk?** `uv run --with websockets python3 kiosk_check.py --serial <serial>` checks device owner, lock task, the secure context, the bridge, the microphone, camera and location grants and a wake lock — run it before and after any update. Updating the tablet's **WebView**: `notes/design/tablet-device.md`, *Updating the robot tablet's WebView*.

**Updates come from the robot, over Wi-Fi** (from 0.3.0). The app checks the robot 30 s after it starts
and every 10 minutes (`Updater.kt`). When the robot serves a newer build, the app installs it itself,
silently, as device owner, and comes back to the front:

- build anywhere: `./gradlew assembleRelease` gives `app-release-unsigned.apk` and `kiosk-version.json`;
- the deploy copies both to the Thor, whose `sign-kiosk.sh` signs the APK with the release key (kept
  root-only on the Thor, never in this repository; its setup and backup are in that script's README) and
  the dashboard serves it at `/api/kiosk/latest` and `/api/kiosk/apk`;
- the app installs it only if its versionCode is higher, its sha256 is the one announced, it is this
  package, and it is signed by the app's own key or by a key rotated from it (APK Signature Scheme v3
  lineage). It never installs while a show is loaded or armed. `OpenDubsKiosk.updateStatus()` says
  what it last decided, and the Head & face page shows the installed and served versions.

There is no automatic rollback (an app cannot downgrade itself): a bad build is replaced by a newer,
fixed one. A release build is not `testOnly`, so its device owner can only be removed by a factory reset.

**By cable** (a first install, or a build that broke the updater): `adb install -r app.apk` with an APK
signed as above (`-t` as well for a debug build). It keeps the device owner and every setting. To reach
the tablet without the cable, `adb tcpip 5555` while it is on USB, then `adb connect <tablet-ip>:5555`;
that stays open on the network until the next reboot, so reboot it when done.

Remove the device owner (debug builds only):

```bash
adb shell dpm remove-active-admin org.opendubs.kiosk/.KioskAdminReceiver
```

## Set up a display (the projector)

The same app runs on **Android TV / Google TV** as a **display**: a screen that only shows one page,
such as the projector's stage page (`/stage/`, the Choreography lane's). A set-up TV has an account, so
the app cannot be its device owner; in the `display` role it therefore leaves out what needs one: **no
self-updater** (it would put an install prompt on screen mid-show; update it with `adb install`), **no
sensor bridge** and **no lock task**. Keep-awake, autoplay with sound, the waiting page and the heartbeat
watchdog are the same as on the tablet. The page must call `window.OpenDubsKiosk.alive()` every few
seconds, or the watchdog reloads it. It also has a tile on the Google TV home screen, so the remote can
open it as a fallback.

**Unverified on the projector itself** (the checks are done with someone at it): that wireless debugging
survives a reboot and a Wi-Fi change, and whether the projector's own power saving still switches it off.

One time, with the remote, on the projector: **Settings › System › About › Android TV OS build**, press
OK seven times (developer options); then **Developer options › Wireless debugging** on, and **Pair device
with pairing code**. On the robot's computer, with the code and the addresses the screen shows:

```bash
adb pair <projector-ip>:<pairing-port>
```

Then, from the robot's computer, with the port Wireless debugging shows (it changes when it is turned on):

```bash
adb connect <projector-ip>:<port>
```

```bash
adb install -r app-release.apk
```

```bash
adb shell am start -n org.opendubs.kiosk/.ProvisionActivity --es role display --es url http://<thor>:8080/stage/
```

`ProvisionActivity` remembers both and opens the page; after that the app opens on it by itself.

## Status

Builds (Android Gradle plugin 9.4.1, Gradle 9.8.1, compile and target SDK 36, min SDK 28). **Runs on
the team's Galaxy Tab A11 (SM-X133, Android 15, One UI 7)**, set up 2026-10-08 as above. Against a
bench dashboard on a laptop: TB-1, TB-2, TB-3 and TB-5 pass (`tablet-device.md` has the results);
TB-4 is a week of observation, begun that day; TB-6 needs the robot. The foreground service for audio
with the screen off is **not needed**: TB-2 played ten minutes straight through, four of them with
the screen off, on battery. Suspending Samsung's own update apps waits on TB-4.
