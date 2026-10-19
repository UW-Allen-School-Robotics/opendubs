# Work plan — network: the robot router, its Wi-Fi and its campus uplink

**DRAFT, 2026-10-19, written by the Network lane. Not yet agreed.** Once it is, it becomes the plan
of record and finished items are struck through with a date, as in
[`workplan-tablet.md`](workplan-tablet.md). Only the Network lane edits this file (CLAUDE.md
"Lanes", rule 3); findings from other lanes come as messages.

**Scope.** The robot's router, the robot's own Wi-Fi, and the router's uplink to the campus
network. The router's configuration lives on the router. The dashboard, rosbridge and the ROS
units belong to the Tablet and Thor lanes; this lane measures their traffic and sends findings
through the orchestrator.

**Background.** The design is [`docs/network-architecture.md`](../docs/network-architecture.md):
one dedicated onboard router, a station uplink to the site network on one band and the robot's own
access point on the other, NAT, and default-deny inbound.

**Jargon.** AP: access point. Station (STA): a radio that joins another network as a client.
RSSI: received signal strength, in dBm (−50 is strong, −67 is the usual floor for good throughput,
−80 is the edge). SNR: signal-to-noise ratio. CCQ: MikroTik's link-quality estimate. Safe Mode:
a RouterOS terminal mode that undoes every change made in it if the session drops. CAKE and
fq_codel: queue disciplines that keep latency low while a link is full. WMM: Wi-Fi multimedia,
the 802.11 traffic-priority classes.

**Lane rules.**

1. **No change on the router without the user's explicit word in the same session, and a person
   at the robot.** A bad change can cut off every way in. Reading is fine.
2. The user types any router password. Credentials live in the user's password manager, never in
   chat or in the repository.
3. Hardening items are recommendations with effort and lockout risk. None of them blocks other
   lanes' work: the robot LAN and Wi-Fi are the first line of defence by design.
4. Never interfere with downloads or processes another session started.

---

## The robot's network today (read 2026-10-19, nothing changed)

| | |
|---|---|
| Router | MikroTik hAP ac³, RouterOS 7.20.7 (long-term), gateway of the robot LAN 10.42.2.0/24 at 10.42.2.1 |
| Robot Wi-Fi | `wlan1`, 2.4 GHz only, 20/40 MHz, AP mode, WPA2-PSK (AES) |
| Uplink | `wlan2`, 5 GHz, a station on the campus network "UW MPSK" |
| Wired | the Thor on `ether3`; `ether1` is in the WAN list and unused |
| Clients on Wi-Fi | the head tablet (−48 dBm), an ESP32-S3 (−39 dBm), a Mac, a phone, a watch |
| Router clock | about 18 h behind the Thor: no NTP client is set, and the hAP has no battery clock |
| Management access | the admin key on the project Mac's `opendubs2-router` SSH alias works (read-only use so far) |

Because the 5 GHz radio is the uplink, the robot's own Wi-Fi has no 5 GHz.

---

## U — the uplink

### What was measured

- Associated at **−80 to −84 dBm, SNR 25 dB**, 20 MHz on a 5 GHz channel, CCQ 87 %, about
  43 Mbit/s receive rate, 4–5 MB/s in practice. That is the edge of usable coverage.
- Other campus APs in a scan were weaker (−85 to −91 dBm). **Pinning a different AP would make it
  worse.** The access point is not the problem; the signal is.
- In the 1 h 43 min the uplink had been associated, it carried **23.9 GB down** (≈ 31 Mbit/s
  average): the link ran at about its capacity the whole time, filled by the Voice lane's model and
  image downloads.
- Latency to the internet was **300–570 ms while the link was full and 2.4 ms when idle**. The delay
  is queueing behind the downloads, not the campus network.
- No queue is configured on the router (no simple queues, no queue tree), and WMM is off on both
  radios.

### Options

| # | Option | What it buys | Effort | Lockout risk | At the robot? |
|---|---|---|---|---|---|
| U-1 | **Move the robot or turn it**, watching the uplink's signal in the router's registration table. Aim for −67 dBm or better | Up to several times the throughput, no cost | Minutes, needs the robot to be movable | None (no config change) | Yes |
| U-2 | **Better 5 GHz antennas**: the hAP ac³'s 5 GHz antennas are external on connectors (check the connector type on the unit before buying). Higher-gain antennas, or a short low-loss extension that lifts them clear of the metal base | Several dB, cheaply | An hour and a part order | None | Yes |
| U-3 | **A wired uplink** at a dock (the design's preferred uplink): a campus port into `ether1`, which is already in the WAN list | The full wired rate, no radio contention; frees `wlan2` | Needs a registered port from the site's IT; config is a DHCP client on `ether1` | Low; the LAN side is untouched | Yes |
| U-4 | **A separate uplink radio** (a small outdoor-capable client bridge on PoE from the hAP), placed for signal | Best roaming signal, and frees `wlan2` for a 5 GHz robot AP | A part, mounting, and a new MAC to register with the campus network | Low | Yes |
| U-5 | **Queueing**: CAKE (or fq_codel) on the router, with the download limit set a little under the link's typical rate, so a big download cannot starve interactive traffic | Interactive latency (SSH, the cloud voice brain, updates checks) stays low during a download; downloads take a few percent longer | About 15 min | Low (queues act on forwarded traffic; management from the LAN is not shaped) | Yes, by rule 1 |
| U-6 | **Turn WMM on** for both radios, so priority classes work over the air | Small; makes U-5 effective on the radio hop | 2 min; clients re-associate (the tablet drops for seconds) | None | Yes |

U-5 interacts with the link rate: on a weak, varying link a fixed limit is either too high (no
effect when the rate drops) or too low (wasted capacity). Set it from U-1's result, and revisit it
after U-2–U-4.

**Recommendation:** U-1 first (free, minutes). Then U-5 + U-6 together in one sitting, since any
uplink stays a bottleneck at some point. Of the hardware options, U-3 where a dock exists; U-4 for
roaming. U-2 only if U-1 can't reach −70 dBm.

---

## H — router hardening (recommendations; none applied)

Already good:
- The default firewall is intact. Input from anything not on the LAN list is dropped; forwarding
  from the WAN is dropped unless destination-NATed.
- The LAN list holds only the bridge, so no management service answers from the campus side.
- Neighbor discovery and the MAC server (MAC-Telnet, MAC-Winbox) are limited to the LAN list.

Every item is done from the LAN, over SSH, **in Safe Mode**, with a binary backup taken first
(H-0). A reset to defaults is the last-resort recovery and erases the uplink's settings, so H-0
comes first.

| # | Change | Why | Effort | Lockout risk |
|---|---|---|---|---|
| H-0 | **Take a backup**: an encrypted binary backup and a plain `/export`, both kept off the router and outside the repository (see decision D-3 for a sanitized copy in the repo) | Recovery from any later mistake without reconfiguring the uplink by hand | 5 min | None |
| H-1 | **Disable telnet, FTP and the plain API** (port 8728); also `api-ssl` if nothing uses it | Plaintext services nobody uses | 1 min | None if done over SSH |
| H-2 | **WebFig over HTTPS only**: enable `www-ssl` with a self-signed certificate and disable `www`; or disable both and manage over SSH and Winbox. WebFig is in use from the Mac today, so this is the user's choice | The admin password crosses the Wi-Fi in clear on every WebFig login | 10 min | Low (SSH stays up) |
| H-3 | **Limit where `admin` can log in from**: set its allowed address to 10.42.2.0/24 (or one admin host). Optionally make a named full account and disable `admin` once the new one is proven | Defence in depth if the firewall is ever loosened | 10 min | **Medium**: a typo locks out every login. Safe Mode undoes it if the session drops; MAC-Winbox on the LAN is the fallback |
| H-4 | **Set "Available From" 10.42.2.0/24** on ssh, winbox, www/www-ssl and api-ssl | Same as H-3, per service | 5 min | Medium, same mitigation |
| H-5 | **Turn on the NTP client** | The router's clock is ~18 h off, so its logs and last-login times are unreadable. The "02:33" admin login in the handover was this project's own read-only session: this lane's own login just now was recorded as 03:11, matching that offset | 2 min | None |
| H-6 | **Turn off WPS push-button** on `wlan1` | Anyone who can press the router's button can join the robot Wi-Fi | 1 min | None |
| H-7 | **Check for a newer long-term RouterOS** and the RouterBOARD firmware; upgrade only at the robot with nothing moving | Security fixes | 10 min plus a reboot: the robot's Wi-Fi is down for 1–2 min | Low, with H-0 done |

**Recommendation:** one sitting at the robot for H-0, H-1, H-5, H-6 (about 10 minutes, no lockout
risk). H-2 once the user picks HTTPS or SSH-only. H-3/H-4 together, in Safe Mode. H-7 when the robot
can be idle for a few minutes.

---

## R — rosbridge traffic (measured read-only 2026-10-19; findings go to the Tablet and Arms lanes)

### What was measured

On opendubs2, the dashboard (`opendubs-dash`, up 2 h 10 min) held three connections to rosbridge
on 127.0.0.1:9090:

| Connection | Direction | Total | Now |
|---|---|---|---|
| `/rosbridge` relay for one dashboard page | rosbridge → page | 1.23 GB | 196 KB/s |
| `/rosbridge` relay for a second dashboard page | rosbridge → page | 1.23 GB | 196 KB/s |
| the tablet relay (`tablet_relay.py`), publishing tablet sensors | dashboard → rosbridge | 227 MB | idle during the sample (≈ 29 KB/s average, so bursty) |

**Both heavy relays belong to two dashboard pages open on a Mac on the robot Wi-Fi** (plain HTTP,
:8080). The head tablet (HTTPS, :8443) had no `/rosbridge` relay open. The "190 MB from rosbridge"
in the handover was this third connection, in the other direction: the tablet's sensor data going
into ROS.

What each dashboard page subscribes to (subscribing through rosbridge exactly as the page does,
for 15 s):

| Topic | Rate delivered | JSON per message | Bandwidth |
|---|---|---|---|
| `/scan` (front LIDAR) | 6.9 Hz | 14.2 KB | 96 KB/s |
| `/scan_rear` | 7.7 Hz | 11.2 KB | 85 KB/s |
| `/joint_states` (published at 400 Hz) | 9.9 Hz | 1.5 KB | 14.5 KB/s |
| **total per page** | | | **≈ 195 KB/s ≈ 1.6 Mbit/s** |

The page's `throttle_rate: 100` works: `/joint_states` arrives at 10 Hz, and the LIDARs publish
below the 10 Hz cap anyway.

### What it costs

- **On the Wi-Fi, little.** The dashboard's server compresses its websockets (permessage-deflate),
  and LIDAR JSON compresses about 12×: each page's relay reached the Mac at **≈ 16 KB/s
  (0.13 Mbit/s)**. In 1 h 48 min the router sent the Mac 543 MB, which also includes the Mac's own
  internet use; the tablet received 70 MB in 3 h. The 2.4 GHz Wi-Fi is nowhere near capacity from
  robot traffic.
- **On the Thor's CPU, some.** rosbridge used ~50 % of one core continuously and the dashboard
  ~23 %. How much of rosbridge's share is encoding scans as JSON, and how much is taking in
  `/joint_states` at 400 Hz before throttling, was not separated. On a 14-core Thor this is a few
  percent of the machine.
- **Every forgotten tab is another 195 KB/s of JSON** on the Thor: the page subscribes on load and
  keeps the subscriptions whether or not the LIDAR or teleop view is on screen.

### Suggested fixes (not this lane's paths; sent through the orchestrator)

| # | Change | Owner | Worth it? |
|---|---|---|---|
| R-1 | Subscribe to `/scan`, `/scan_rear` and `/joint_states` only while the view that draws them is showing, and unsubscribe when the page is hidden (`visibilitychange`) | Tablet lane (`frontend/app.js` is shared; the `/joint_states` consumer is the Arms lane's teleop view) | Yes, cheap: removes the forgotten-tab cost |
| R-2 | If rosbridge's CPU ever matters: measure it with and without a `/joint_states` subscriber. If the 400 Hz intake dominates, a 10 Hz copy of the topic for displays is the fix; if scan encoding dominates, a lower scan rate or rosbridge's CBOR encoding | Thor lane (rosbridge), Arms lane | Not now; only if the Thor is short of CPU |

No network-side change is needed for this traffic.

---

## Decisions for the user

| # | Decision | Recommendation |
|---|---|---|
| D-1 | Which uplink path to pursue | U-1 (reposition) now; then U-3 at a dock, or U-4 for roaming |
| D-2 | When to do the at-robot sitting for U-5/U-6 and H-0/H-1/H-5/H-6 (~25 min, no lockout risk, the tablet drops for seconds) | The next time someone is at the robot with nothing moving |
| D-3 | **Keep a sanitized router export in the repository?** CLAUDE.md says config that changes runtime behaviour belongs in the repo; this lane's brief says the config lives on the router. RouterOS 7's `/export` hides passwords and keys by default, and that would be checked before every commit | Yes: a sanitized export at `notes/design/network-router.rsc`, refreshed after each change, so "what changed on the router?" has an answer |
| D-4 | H-2: WebFig over HTTPS, or SSH and Winbox only | HTTPS, since WebFig is in use |
| D-5 | H-3: keep `admin`, or a named account | Keep `admin`, restricted to the LAN; a named account adds little for one shared admin |
