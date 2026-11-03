# The robot router: changes applied

The record of every change made on opendubs2's router (MikroTik hAP ac³, RouterOS 7.20.7), newest
first. The configuration lives on the router; this file is the answer to "what did we change, and
when?". Every change was made with the user's word and a person at the robot (CLAUDE.md, Network
lane). No passwords or keys appear here: they live in the password manager.

## 2026-10-28

**Robot Wi-Fi to channel 1 at 20 MHz (D-7).** `/interface wireless set wlan1 frequency=2412 channel-width=20mhz`.
- Before: `frequency=auto`, `channel-width=20/40mhz-XX`, running on 2447 MHz with a 40 MHz extension
  that overlapped the busy campus channels 6 and 11.
- Every client dropped for a few seconds and rejoined. The stage Pi had dropped nine minutes
  earlier, for its own reasons, and needed a power-cycle.
- Afterwards, the only other network seen near channel 1 was a faint eduroam on channel 5.

**Multicast sent as unicast (applied by the user).** `/interface wireless set wlan1 multicast-helper=full`.
- **Why:** the Thor sends about 30 ROS 2 DDS discovery packets a second (~16 KB/s, to
  239.255.0.1:7400). Without the helper, the router sends multicast to Wi-Fi at the basic rate
  (1 Mbit/s), so that traffic alone took roughly 13 % of the airtime, and it was held back for
  power-saving clients.
- **Effect:** every client dropped and rejoined within 7 s. Over the same 20 s window, before →
  after:

  | Measure | Before | After |
  |---|---|---|
  | AP packets out | 562/s | 799/s |
  | Mac → Thor ping, average | 130 ms | 113 ms |
  | Mac → Thor ping, worst | 594 ms | 463 ms |
  | Busiest laptop's retries | ×2.1 | ×1.7 |

  More packets is expected: each multicast packet now goes to every client separately, at that
  client's own rate.
- **Related:** the Thor lane is also confining DDS discovery to the Thor
  (`ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST`). Nothing on the Wi-Fi speaks DDS; the tablet and the
  Quest use rosbridge and foxglove over TCP.

**What remains slow.** Laptops at the edge of the robot's 2.4 GHz coverage retry 2–3× and slow
everyone. Placement of the router, and of the laptops, matters more than any setting.

## 2026-10-26

- **The robot Wi-Fi passphrase rotated.** The robot Wi-Fi's security profile (`default`) has a new
  WPA2 passphrase. The uplink's profile (`uwmpsk`) is unchanged. The neck took it over USB
  (`firmware/neck_dial/tools/provision.sh`, then a power-cycle), the head tablet on the tablet
  itself (reached via Android Safe Mode), the stage Pi by re-imaging. The temporary access-list
  entries that kept the old passphrase valid for a few MACs were removed the same day; the access
  list is empty.
- **Time sync on:** `/system ntp client set enabled=yes servers=pool.ntp.org`.
- **Push-button pairing (WPS) off:** `/interface wireless set wlan1 wps-mode=disabled`.
- **The read-only group `netread`** now has the `netmon` user, allowed only from the Thor
  (10.42.2.10). Its password on the Thor doesn't match yet, so `opendubs-netmon` is stopped until
  it's set again (letters and digits only; the RouterOS command line changes `$`, `;`, `\` and `"`).

## 2026-10-20

- **Telnet, FTP and the plain API off:** `/ip service disable telnet,ftp,api`.
- **HTTPS for the REST API, alongside the kept HTTP admin page:** a self-signed certificate,
  `netmon-https`, on `www-ssl`.
- **The read-only group `netread`:** policies `read` and `rest-api` only.
