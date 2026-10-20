# Work plan — network: the robot router, its Wi-Fi and its campus uplink

**Written by the Network lane, 2026-10-19; the user's decisions of 2026-10-20 are recorded at the
end.** Items not yet decided stay drafts. Finished items are struck through with a date, as in
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

## A — reading the router: a diagnostic and a dashboard status block

Design: [`design/network-router-api.md`](design/network-router-api.md). Read-only REST over HTTPS,
from the Thor only, with a dedicated read-only account whose password lives in
`/etc/opendubs/network.env` (root, 0600) and never in the repository.

| # | Item | Effort | Needs |
|---|---|---|---|
| A-1 | Router side: a self-signed certificate and `www-ssl`, a `netmon` group (`read`, `rest-api` only) and user limited to the Thor's address | 15 min | The user's word, someone at the robot; done with H-2/H-4 |
| A-2 | `opendubs-netmon` poller in `tools/network/`: reads the router every 5 s, probes from the Thor, writes `/run/opendubs-network/network.json`, and keeps the uplink history the router loses on reboot. **Built 2026-10-20**, not yet deployed | A day | Install hook in the Thor lane's deploy scripts |
| A-3 | `netdiag`: one command on the Thor that names the first failing layer. **Built 2026-10-20** | Half a day, after A-2 | — |
| A-4 | The dashboard's `status.net.uplink` field and a one-line display, built from the contract in the design note §4 | Tablet lane | Relayed by the orchestrator |

## J — joining other networks (taking the robot off campus)

Design: [`design/network-uplinks.md`](design/network-uplinks.md) §1–3. The robot's own Wi-Fi and
10.42.2.0/24 never change; only the WAN side does. **The uplink radio is 5 GHz only**, because the
robot's Wi-Fi must stay 2.4 GHz for its ESP32 devices. So a hotspot must be set to 5 GHz, and a
2.4 GHz-only venue needs a cable.

| # | Item | Effort | Lockout risk |
|---|---|---|---|
| J-1 | Turn the uplink into saved networks with a priority order (the station's connect list; the interface SSID left empty), starting with UW MPSK and the team's hotspot | 15 min at the robot | None: the LAN side is untouched |
| J-2 | Make a cable win: `ether1` route distance 1, `wlan2` distance 2, with gateway checking | 5 min | None |
| J-3 | `netjoin` in `tools/network/`: add, list, prefer, disable, remove and scan saved networks from the Thor. The operator types the router admin password each time; no write credential is stored on the robot. **Built 2026-10-20**; first use at the robot | A day | None (it touches only its own tagged entries) |
| J-4 | Bring a 5 GHz hotspot setting and a short cable in the travel kit; record each venue's registration needs in the venue's entry comment | — | — |

## E — eduroam, and UW's rule on onboard access points

Design: [`design/network-uplinks.md`](design/network-uplinks.md) §4, cited to UW's own pages.
UW eduroam is **EAP-TLS with a device certificate**, which suits this router. No UW page says a
non-person account can get one. More pressing: **UW policy prohibits departmental or DIY access
points without a UW-IT exemption** (KB0034266), and the robot's router is one.

| # | Item | Who |
|---|---|---|
| E-1 | A departmental shared NetID, owned by faculty or staff; move the robot's UW MPSK registration under it | The user, with the department's IT |
| E-2 | One UW-IT ticket: a research exemption for the robot's onboard AP, and whether the shared NetID can get Eduroam Radius Access and a device certificate (or the "eduroam managed server solution") | The user. This lane sends nothing |
| E-3 | Meet the coexistence guideline now: the robot AP to 20 MHz on channel 1, 6 or 11 (today 20/40 MHz, automatic channel) | This lane, with the user's word at the robot; 2 min, clients drop for seconds |
| E-4 | Bench test once a certificate exists: EAP-TLS association on the spare field router, never on opendubs2 | This lane |

---

## Decisions

Taken by the user on 2026-10-20 unless marked otherwise. "At the robot" means the next session with
the user's word and a person there (lane rule 1). Nothing is applied from a desk.

| # | Decision | Outcome |
|---|---|---|
| D-1 | Which uplink path to pursue | **No coverage test now.** Instead, **an uplink Wi-Fi strength indication on the tablet and dashboard**: the poller (A-2) plus the Tablet lane's `status.net.uplink` (A-4) |
| D-2 | What to do at the robot | **H-0 backup: yes. H-1 (telnet, FTP, plain API off): yes. H-5 time sync: discuss first** (below). **H-6 WPS: not needed**, left as is. **U-5/U-6 queueing: declined** as too risky |
| D-3 | Sanitized router export in the repo | **Yes** (2026-10-19), under `notes/design/network-*` |
| D-4 | WebFig over HTTPS, or off | **Keep the page as it is** (HTTP). H-2 is dropped |
| D-5 | Restrict where `admin` can log in from | **Not agreed: needs more exploration.** Explored below |
| D-6 | UW-IT exemption and eduroam (E-1, E-2) | **Parked on the punch list** (`punch-list.md` §4 E) |
| D-7 | Robot AP to 20 MHz on channel 1, 6 or 11 (E-3) | **Yes**, at the robot |
| D-8 | Build order for `tools/network/` | **The logical order, all of it now**: the REST client and poller, `netdiag`, then `netjoin`. Not waiting for travel |

### Next at-robot session (the Network lane's list)

Each step still needs the user's word on the day.

1. **H-0**: a binary backup and an `/export`, both kept off the robot and out of the repository.
   A sanitized copy of the export goes in `notes/design/` after checking it for secrets.
2. **H-1**: turn off telnet, FTP and the plain API.
3. **E-3**: set the robot AP to 20 MHz on a fixed channel 1, 6 or 11. Pick the quietest from a scan.
4. **A-1**: create the read-only `netmon` account the poller needs for D-1
   ([`design/network-router-api.md`](design/network-router-api.md) §2).
   - It is the only route to the uplink's signal; nothing else on the robot can see it.
   - The router change has no lockout risk: `admin` is not touched.
   - The user types the account's password into `/etc/opendubs/network.env`.
   - Options: REST over the HTTP page that D-4 keeps, or `www-ssl` turned on alongside it. HTTPS
     is recommended, since the read password would otherwise cross the robot Wi-Fi in clear. The
     page stays either way.
5. **J-1 and J-2**, using `netjoin init` and `netjoin wired-first`: turn the uplink into a saved,
   ordered list of networks, and make a cable win over Wi-Fi. No LAN-side change.

### D-5 explored: restricting where `admin` can log in from

**What it does.** RouterOS refuses a login for that user from any address outside the account's
allowed list, on every service.

**What it would add, here:**
- **Against the campus side: nothing new.** The firewall already drops every management
  connection that doesn't come from the robot's LAN.
- **Against devices on the robot's own Wi-Fi: nothing, if the list is the whole LAN
  (10.42.2.0/24).** Students, guests and their laptops are on that subnet.
- **Real protection needs a list of specific hosts**: the Thor (10.42.2.10, already a fixed
  lease) and named admin machines.
  - The Mac now gets a dynamic address on a private (randomized) Wi-Fi MAC, so it would need a
    fixed lease.
  - Any other laptop would be refused and would have to go through the Thor (`ssh -J`).
- **The bigger exposure is elsewhere.** The admin password crosses the robot Wi-Fi in clear on
  every login to the HTTP admin page, which D-4 keeps. A restriction doesn't change that.

**How it could lock everyone out, and the ways back:**

| Failure | Way back |
|---|---|
| A typo, noticed in the same session | Safe Mode undoes the change when the session drops |
| A host list that's wrong the next day (the admin laptop got a new address) | Log in from the Thor, if it's on the list |
| Nothing on the list works | MAC-Winbox from a laptop on the robot LAN. **Unverified** whether RouterOS applies the address check to MAC-layer logins too |
| Nothing works | The reset button returns the router to defaults: the robot has no uplink until the H-0 backup is restored (about 10 min with a laptop on the LAN) |

**Revised recommendation: don't do it now.** It adds little while the robot Wi-Fi is open to the
team and the admin page stays on HTTP. Revisit it with the parked security discussion, where
"who may administer the robot" is the real question. H-1 (closing the plaintext services) is the
useful step and has been agreed.

### H-5 time sync, for discussion

- **What it is.** The router sets its clock from internet time servers while the uplink is up.
  The campus DHCP may hand out a time server, and the router already accepts one
  (`use-peer-ntp=yes`). `pool.ntp.org` is the fallback.
- **What it fixes.** The router's clock is about 18 h off, because it has no battery clock and
  starts from a fixed date at every boot. Its log, its last-login times and the times of uplink
  drops are unreadable. The poller keeps its own history on the Thor's clock, so the dashboard
  doesn't depend on this.
- **What it doesn't fix.** Offline, the router still has no source of time. The Thor's own clock
  problem offline (its RTC reads 1970) is the Thor lane's, and a router with no time can't solve
  it.
- **Risk.** No lockout risk. The router's log times jump forward once when it first syncs.
- **An option to decide with it:** the router could also serve time to the robot's devices (the
  ESP32s, the tablet, the Thor), so that they agree with each other whenever the robot is online.
  That touches how the Thor keeps time, so it's agreed with the Thor lane first.
