# The tablet as a robot device

**Status: decided by the project team, 2026-10-07** (choreography plan item 4.1, team request
E9). The tablet is a **Samsung Galaxy Tab A11**. Nothing here has been tried on it yet; claims about
Android and browsers are sourced below, and everything that only the tablet can answer is listed
as an experiment.

**What is wanted.** During a show the tablet must behave like part of the robot: never sleep, never
show an update prompt or a notification, keep the dashboard in front, keep music playing for the
whole show, offer the microphone, and react to robot state. This is about **not being
interrupted**. It is not a security measure: the robot's security stance is LAN first, and locking
the tablet itself is parked as P1.

---

## Decisions — 2026-10-07

| # | Question | Decision |
|---|---|---|
| TD-1 | **How the dashboard becomes a "secure context".** The microphone (`getUserMedia`) and Screen Wake Lock only work on a secure origin, and `http://10.42.2.10:8080` is not one — only HTTPS and `localhost` are ([MDN](https://developer.mozilla.org/en-US/docs/Web/Security/Secure_Contexts)). | **HTTPS from the Thor, with a local CA** (e.g. `mkcert`) installed once on the tablet. Works offline. Laptops that browse the dashboard need the CA too, or accept a warning. |
| TD-2 | **What the tablet runs.** | **Our own minimal, open-source kiosk app** — after a short check for an existing maintained open-source kiosk browser that already does the job, which would be used instead. A paid kiosk app (Fully Kiosk PLUS) was rejected as at odds with an open-source project. Scope is deliberately small: one screen holding a WebView of the dashboard, a device-owner receiver, and a JavaScript bridge (wake the screen, come to the front, report screen state). It never grows its own UI. See *The kiosk app* below. |
| TD-3 | **Device owner.** Lock task mode (no status bar, no notifications, no Home) and the system update policy (postpone 30 days, or a nightly window) need a device-owner app. | **Yes — a dedicated robot tablet.** Factory reset, **no Google account**, our app provisioned as device owner with `adb shell dpm set-device-owner`. Apps are installed from APKs and updated by hand. |
| TD-4 | **Is the tablet in the BOM?** | **Yes.** A Galaxy Tab A11 line in `parts.csv`, with this note as the setup guide. The app is built from this repository, so there is no licence to buy. (A `parts.csv` change, made separately with a buy link.) |

---

## The options, compared

| | no sleep | no update prompts | no notifications | always in front | mic | audio with screen off | follows robot state | effort | cost |
|---|---|---|---|---|---|---|---|---|---|
| **Chrome + installed web app** | Wake Lock (needs HTTPS; released if the page is hidden or power saving starts) | settings only | Do Not Disturb only | app pinning — the user can leave | needs HTTPS | **unverified** | the page only; cannot wake or front itself | low | free |
| **Fully Kiosk PLUS** | Keep Screen On | with device owner: postpone or windowed | kiosk; lock task with device owner | yes | yes, needs HTTPS | **unverified** | JS interface: screen on/off, bring to front, events | low–medium | ~US$11 once |
| **Custom APK (WebView)** | `FLAG_KEEP_SCREEN_ON` | only as device owner | only as device owner | only as device owner | yes, needs HTTPS | foreground `mediaPlayback` service | anything | **high**, ongoing | free |
| **Device owner (any of the above)** | stay on while plugged in | `SystemUpdatePolicy`, freeze periods | lock task hides them | lock task | — | — | — | factory reset, no account | free |

Sources: Fully Kiosk feature list and licence ([fully-kiosk.com](https://www.fully-kiosk.com/en/)),
lock task mode ([Android](https://developer.android.com/work/dpc/dedicated-devices/lock-task-mode)),
system update policy ([Android](https://developer.android.com/work/dpc/system-updates)), device
owner via adb ([AOSP](https://source.android.com/docs/devices/admin/testing-setup)), Wake Lock
([Chrome](https://developer.chrome.com/docs/capabilities/web-apis/wake-lock)).

**Samsung's own kiosk tools are not a free shortcut here.** Knox Configure's kiosk mode is paid, and
the free Knox licence is activated through an MDM console.

---

## The kiosk app

**Why our own, and what it costs.** It is free, open source and versioned with the dashboard it
wraps, and it settles the biggest unknown below (TB-1): an app can declare in its own network
security config that it trusts the robot's local CA, where a third-party WebView's trust is
uncertain. Against that, the team takes on:

- **an Android toolchain** (SDK, Gradle, Kotlin — command-line tools are enough), kept building as
  people come and go;
- **the kiosk mechanics as our code** — device-owner receiver, lock task, update policy,
  `FLAG_KEEP_SCREEN_ON`, the microphone permission, audio autoplay, a `mediaPlayback` foreground
  service if music must survive the screen going off — tested on one tablet rather than many;
- **a signing key.** Every update must be signed with the same key; lose it and the app must be
  removed, which for a device owner means a factory reset. The key lives in the team's password
  manager from day one, never in the repository;
- **One UI updates that may break it.**

**The scope rule.** A shell around the dashboard: no screens, settings or logic of its own beyond
what Android requires. Anything a user sees is the dashboard's.

## The search for an existing app (plan 4.1a), 2026-10-07

TD-2 said: check for a maintained open-source kiosk browser before writing one. Six were examined
from their source; none was run on the tablet.

| | licence | maintained | device owner + lock task | update policy | trusts our local CA | mic in the page | page ↔ tablet control |
|---|---|---|---|---|---|---|---|
| **[FreeKiosk](https://github.com/RushB-fr/freekiosk)** | MIT | yes — pushed 2026-10-07, 658★, 19 contributors | yes | postpone, and suspends Samsung's own update apps | **yes** (`network_security_config.xml` trusts user CAs — checked) | yes, granted to any origin | REST API on the tablet (`/api/screen/on`, `/api/wake`, `/api/screen`, `/api/url`, `/api/reload`), MQTT; the page-side bridge lacks wake/front |
| [Webview Kiosk](https://github.com/nktnet1/webview-kiosk) | AGPL-3.0 | yes, mostly one maintainer | yes | none | **no** (system CAs only) | yes (a setting) | MQTT commands |
| KioskZen | GPL-3.0 | small, single developer | yes | none | no | yes | none |
| WallPanel | Apache-2.0 | **archived**; needs Firebase/Google Play | no | — | — | — | — |
| kiFOSSk | MIT | new (2026-07) | no | — | — | no | — |
| Headwind MDM | Apache-2.0 (launcher) | yes | via its MDM server | — | — | — | needs a server: too heavy |

**FreeKiosk meets nearly every requirement.** Its gaps: the dashboard page cannot itself wake the
screen or come to the front (the tablet's REST API can, so the robot's backend could call it); the
update policy is postpone-only, with no nightly window; the microphone is granted to any page; and
it is large — React Native plus Kotlin, an accessibility service, overlays, MQTT, an opt-in cloud
service in its 2.0 betas — and changes quickly.

**Decided, 2026-10-07: write our own anyway.** A small native app the team fully understands, over
adopting a large, fast-moving one. Webview Kiosk is the clearest code to learn from, but it is
AGPL: **read it, do not copy it**, or our app becomes AGPL. FreeKiosk is MIT, so its update-policy
code (postpone, and suspending Samsung's update apps) may be reused with its copyright notice kept.

## Setup

**Once, at the bench:**

1. Factory-reset the tablet; skip adding a Google account.
2. Install the kiosk app from its APK.
3. `adb shell dpm set-device-owner <app>/<receiver>`. The app turns on lock task mode and sets a
   system update policy (postpone, or a window outside show hours).
4. On the robot, `deploy/make-robot-ca.sh` makes its CA and the dashboard certificate; the
   dashboard then serves HTTPS on **8443** beside the unchanged HTTP on 8080 (one process, because
   it owns the serial port). The app installs the CA itself as device owner — by hand, Android
   requires a lock-screen PIN. Over HTTPS the page reaches rosbridge through the dashboard
   (`/rosbridge`), since an HTTPS page may not open `ws://`.
5. The app opens `https://<thor>.local:8443/` full screen and keeps the screen on.

**The Samsung settings that interrupt kiosk use**, each turned off, as a second layer:

- Battery: add the kiosk app to **Never sleeping apps**; turn off **Put unused apps to sleep**.
- Software update › **Auto download over Wi-Fi** off.
- Device care › Auto restart: **Restart when needed** and **Restart on schedule** off.
- Display › **Edge panels** off.
- Developer options › **Stay awake** while charging on.
- Do Not Disturb set to **Alarms only** — not *Total silence*, which would mute the music.

**In the dashboard (code, after TD-1):** use the app's JavaScript bridge when it is present — wake
the screen and come to the front when a show is armed or the robot faults — and fall back to the
Screen Wake Lock API in a plain browser.

---

## Experiments for the tablet

Each with its question and kill criterion, for the hardware session.

| # | Question | Done when / kill criterion |
|---|---|---|
| TB-1 | Does the dashboard load as a secure context **in the kiosk app**, with the robot's CA trusted by the app's own network security config? | `window.isSecureContext === true` and `navigator.mediaDevices` exists. Chrome with the CA installed is the fallback to compare against. |
| TB-2 | Does music play for **10 minutes** without a break — screen on, then screen off, then with the app briefly in the background? | No dropout in a 10-minute track. If screen-off stops it, the setting is "screen always on" during shows, not a code fix. |
| TB-3 | Does the websocket stay connected through screen-off and Samsung's battery management? | No reconnect in 10 minutes. |
| TB-4 | Does One UI honour the device owner's update policy, or still prompt? | No update prompt over a week of normal use. |
| TB-5 | Does Do Not Disturb (Alarms only) leave media audible on this model? | The show's music is audible with DND on. |
| TB-6 | **The plan's kill criterion:** a 10-minute show, start to finish. | Not one interruption: no sleep, no prompt, no notification, no audio gap. |

**What this tablet runs today is unknown.** It shipped with One UI 7 / Android 15 and One UI 8 /
Android 16 began rolling out in November 2025 (secondary sources). Check Settings › About tablet ›
Software information before TB-1, and record it here.
