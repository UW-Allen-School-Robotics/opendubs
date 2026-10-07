# The tablet as a robot device

**Status: proposed, for the project team to decide** (choreography plan item 4.1, team request
E9). The tablet is a **Samsung Galaxy Tab A11**. Nothing here has been tried on it yet; claims about
Android and browsers are sourced below, and everything that only the tablet can answer is listed
as an experiment.

**What is wanted.** During a show the tablet must behave like part of the robot: never sleep, never
show an update prompt or a notification, keep the dashboard in front, keep music playing for the
whole show, offer the microphone, and react to robot state. This is about **not being
interrupted**. It is not a security measure: the robot's security stance is LAN first, and locking
the tablet itself is parked as P1.

---

## Decisions for the team

| # | Decision | Recommendation |
|---|---|---|
| TD-1 | **How the dashboard becomes a "secure context".** The microphone (`getUserMedia`) and Screen Wake Lock only work on a secure origin, and `http://10.42.2.10:8080` is not one — only HTTPS and `localhost` are ([MDN](https://developer.mozilla.org/en-US/docs/Web/Security/Secure_Contexts)). This blocks the mic in every option below, Fully Kiosk included. | **Serve the dashboard over HTTPS from the Thor**, with a certificate from a local CA (e.g. `mkcert`) installed on the tablet as a user CA. Chrome on Android trusts user-added CAs ([its network config](https://chromium.googlesource.com/chromium/src/+/main/chrome/android/java/res_base/xml/network_security_config.xml)). **Stopgap** until then: Chrome's "Insecure origins treated as secure" flag for the dashboard's address, which Chromium documents for testing. Works offline; no internet needed. |
| TD-2 | **What the tablet runs.** | **Fully Kiosk Browser, PLUS licence** (one-time, about €9 / US$11 per tablet). It keeps the screen on, runs as a locked kiosk, autoplays audio, grants the mic, and has a JavaScript interface so the dashboard can follow robot state (wake the screen, bring itself to the front). The free tier lacks kiosk mode, the JS interface and the mic. **Not recommended:** a custom APK — the same result for far more code to maintain — and plain Chrome, which the operator can leave and which cannot stop update prompts. |
| TD-3 | **Make Fully the tablet's "device owner".** Android's strongest kiosk features — **lock task mode** (no status bar, no notifications, no Home button) and the **system update policy** (postpone 30 days, or a nightly window) — need a device-owner app. Without it Fully itself warns that reliable kiosk protection is impossible on Android 12+. | **Yes, on a dedicated robot tablet.** The cost: a **factory reset**, and **no Google account** on the tablet (an `adb` requirement), so Fully is installed from its own APK and updated by hand. Set up once with `adb shell dpm set-device-owner …` — free, no MDM server. |
| TD-4 | **Is the tablet in the BOM?** Already open on the punch list: nothing in `parts.csv` says what to buy. | **Yes** — a Galaxy Tab A11 line, plus Fully Kiosk PLUS, and this note linked as the setup guide. A replicator should not have to rediscover any of it. (A `parts.csv` change, done separately.) |

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

## Setup, if TD-1 to TD-3 are accepted

**Once, at the bench:**

1. Factory-reset the tablet; skip adding a Google account.
2. Install Fully Kiosk from its APK; buy and apply the PLUS licence.
3. `adb shell dpm set-device-owner` → Fully. Turn on lock task mode, status bar off, and a system
   update policy (postpone, or a window outside show hours).
4. Install the robot's local CA as a user certificate; point Fully at `https://<thor>:8080`.
5. Fully settings: Keep Screen On, autoplay audio, microphone allowed, JavaScript interface on.

**The Samsung settings that interrupt kiosk use**, each turned off, as a second layer:

- Battery: add Fully to **Never sleeping apps**; turn off **Put unused apps to sleep**.
- Software update › **Auto download over Wi-Fi** off.
- Device care › Auto restart: **Restart when needed** and **Restart on schedule** off.
- Display › **Edge panels** off.
- Developer options › **Stay awake** while charging on.
- Do Not Disturb set to **Alarms only** — not *Total silence*, which would mute the music.

**In the dashboard (code, after TD-1):** use Fully's JavaScript interface when it is present —
wake the screen and come to the front when a show is armed or the robot faults — and fall back to
the Screen Wake Lock API in a plain browser.

---

## Experiments for the tablet

Each with its question and kill criterion, for the hardware session.

| # | Question | Done when / kill criterion |
|---|---|---|
| TB-1 | Does the tablet's installed CA make the dashboard a secure context in **Chrome and in Fully**? | `window.isSecureContext === true` and `navigator.mediaDevices` exists in both. If Fully's WebView will not trust the user CA, fall back to Chrome + flag and record why. |
| TB-2 | Does music play for **10 minutes** without a break — screen on, then screen off, then with the app briefly in the background? | No dropout in a 10-minute track. If screen-off stops it, the setting is "screen always on" during shows, not a code fix. |
| TB-3 | Does the websocket stay connected through screen-off and Samsung's battery management? | No reconnect in 10 minutes. |
| TB-4 | Does One UI honour the device owner's update policy, or still prompt? | No update prompt over a week of normal use. |
| TB-5 | Does Do Not Disturb (Alarms only) leave media audible on this model? | The show's music is audible with DND on. |
| TB-6 | **The plan's kill criterion:** a 10-minute show, start to finish. | Not one interruption: no sleep, no prompt, no notification, no audio gap. |

**What this tablet runs today is unknown.** It shipped with One UI 7 / Android 15 and One UI 8 /
Android 16 began rolling out in November 2025 (secondary sources). Check Settings › About tablet ›
Software information before TB-1, and record it here.
