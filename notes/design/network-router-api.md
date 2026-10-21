# Reading the robot router: a network diagnostic and a status block for the dashboard

**DRAFT, 2026-10-19, Network lane.** Design only. Nothing here is built or configured on the router.
Plan items N-A1…N-A6 in [`../workplan-network.md`](../workplan-network.md).

**The problem.** When the robot's network acts up, nobody can tell which layer failed. It might be
the robot's own Wi-Fi, the router's uplink to the site, the site network, or a full link.
Today the only way to tell is to SSH into the router and read its tables by hand. The operator at
the dashboard sees nothing at all about the uplink.

**What this adds.**
- A small poller on the Thor reads the router every few seconds, read-only, and keeps a history
  the router itself loses on reboot.
- One diagnostic command reports the first layer that fails.
- The dashboard gets one line of uplink health. The Tablet lane builds it; this note specifies
  the data.

Sources are MikroTik's RouterOS documentation: the
[REST API](https://help.mikrotik.com/docs/spaces/ROS/pages/47579162/REST+API),
[API](https://help.mikrotik.com/docs/spaces/ROS/pages/47579160/API),
[Services](https://help.mikrotik.com/docs/spaces/ROS/pages/103841820/Services) and
[Wireless Interface](https://help.mikrotik.com/docs/spaces/ROS/pages/8978446/Wireless+Interface)
pages, read 2026-10-19. The help.mikrotik.com site is now marked frozen, and newer pages live at
manual.mikrotik.com. Facts about this router come from a read-only session on 2026-10-19.

---

## 1. What the router can report

| Question | Where on the router | Notes |
|---|---|---|
| Uplink signal, SNR, CCQ, rates, throughput estimate, associated AP | `interface/wireless/registration-table`, the `wlan2` entry: `signal-strength` (and per chain), `signal-to-noise`, `tx-ccq`, `rx-rate`, `tx-rate`, `p-throughput`, `mac-address` (the AP), `uptime`, `bytes` | Reading the table refreshes signal and CCQ. `p-throughput` is recomputed every 5 s |
| When the uplink last dropped or reassociated | `uptime` of the `wlan2` entry (time since this association), plus `wireless` log lines ("lost connection, extensive data loss", "established connection on …") | **The log is held in RAM only** (`system/logging` action `memory`) and is lost at reboot, so the poller keeps its own history (§3). On 2026-10-19 the log showed **two uplink drops in three hours**, each about 18 s, each ending on a different campus AP |
| Robot Wi-Fi clients and their signal | the `wlan1` entries of the same table: `mac-address`, `last-ip`, `signal-strength`, `tx-ccq`, rates, `bytes`, `uptime` | DHCP leases give host names (`ip/dhcp-server/lease`) |
| Traffic now | `interface` counters (`rx-byte`, `tx-byte`) for `wlan2`, `ether1`, `wlan1`; the rate is the difference between two reads | |
| Queue depth and drops | `queue/simple` or `queue/tree` statistics (`queued-bytes`, `dropped`) | **No queue exists yet.** This becomes available with workplan item U-5 |
| Uplink address, gateway, DNS, lease | `ip/dhcp-client` | Gives which uplink is active (wireless or `ether1`) |
| Router uptime, CPU, memory, clock | `system/resource`, `system/clock` | The router clock is about 18 h off until H-5 turns on NTP |

The REST API has **no continuous commands**. `monitor` must be called with `"once"`, every request
times out at 60 s, and every value comes back as a string. Polling the tables above suits it.

## 2. How it is read

| Choice | Decision | Why |
|---|---|---|
| Protocol | **REST over HTTPS** (`www-ssl`, `https://10.42.2.1/rest/...`, HTTP Basic auth) | JSON, plain `GET`s, any language. The binary API needs a client library, and its `api-ssl` without a certificate falls back to anonymous Diffie-Hellman. REST over plain HTTP is allowed from v7.9, but MikroTik advises against it: the password crosses the Wi-Fi in clear |
| Certificate | A self-signed certificate made on the router. **The Thor pins its SHA-256 fingerprint** rather than trusting a CA | A CA would be one more thing to rotate. The pin is in the same robot-local file as the credentials |
| Who can reach it | `www-ssl` "available from" 10.42.2.0/24. The firewall already drops input from the WAN side | The same change as hardening item H-4 |
| Account | **A dedicated read-only user, `netmon`**, in its own group with `read` and `rest-api` only: no `write`, `sensitive`, `ssh`, `winbox`, `ftp`, `telnet`, `policy` or `sniff`. Its allowed address is the Thor's reserved address only | The built-in `read` group on this router includes `sensitive`, which reveals stored secrets such as the Wi-Fi passphrases. It also includes `ssh` and `winbox`. MikroTik's user-policy page could not be read (404), so whether REST also needs `web` is checked when the user is made: add only the policy a refused `GET` names |
| Credentials | **`/etc/opendubs/network.env`, root:root 0600, on the Thor only**: `ROUTER_URL`, `ROUTER_USER`, `ROUTER_PASS`, `ROUTER_CERT_SHA256`. The user types the password when the account is made, and it goes into the file without passing through chat | The same convention as the voice service's key file. Never in the repo, an image or the browser |
| Load on the router | One poll every 5 s: about five small `GET`s. The full log every 60 s | The hAP's CPU sat at 6 % while the uplink was full |

None of this changes the router until the user says so with someone at the robot. The router
side needs three steps: the certificate and `www-ssl`, the `netmon` group and user, and
"available from".

## 3. The poller: `opendubs-netmon` on the Thor

- A systemd service with `DynamicUser=` and `EnvironmentFile=/etc/opendubs/network.env`, so
  systemd reads the root-only file and the process never runs as root.
- Every 5 s it reads §1 and probes from the Thor itself: ping the router, ping the uplink gateway,
  resolve a name, fetch an HTTPS URL, and do a captive-portal check (an HTTP request that should
  answer 204; a redirect or a page means a portal).
- It writes **`/run/opendubs-network/network.json`** atomically (write, then rename). Readers need
  no credentials and no network access of their own.
- It appends uplink events to **`/var/lib/opendubs-network/events.jsonl`**: the uplink lost,
  reassociated (including a change of access point, which catches a drop shorter than a poll),
  back, or switched between wired and Wi-Fi, and the internet lost or back. The file is capped at
  5000 lines. This is the history the router loses on reboot.
- It never writes to the router. If the router is unreachable, it says so in the file and keeps
  probing.
- **Built:** [`tools/network/netmon.py`](../../tools/network/netmon.py) and
  `opendubs-netmon.service` (2026-10-20). The router is read every 5 s; the internet probes run
  every 15 s.

## 4. The contract for the dashboard (for the Tablet lane)

The dashboard's `status.collect_slow()` reads `/run/opendubs-network/network.json` and adds:
- **`status.net.uplink`**: the file's `uplink` object, plus `age_s` (now − the file's `ts`);
- **`status.net.robot_wifi`**: the file's `robot_wifi` list.

If the file is missing, or `age_s` is over 15, the uplink is `{"state": "UNKNOWN", "say": "No
network data"}`. The `status` block already reaches the page about once a second, next to
`net.wired` and `net.wifi` for the Thor's own links. There is no new endpoint, and the dashboard
never talks to the router.

**What the page shows (D-1, the user's request):** the uplink's `state` as a coloured badge, the
`say` line, and `signal_dbm`. The tablet's own link strength comes from finding the tablet's own
IP address (the request's client address) in `robot_wifi`.

A real example is [`tools/network/sample-network.json`](../../tools/network/sample-network.json),
built from 2026-10-19's readings. The fields:

```jsonc
{
 "ts": 1760900005.0,                 // when the poller wrote it (Unix seconds, the Thor's clock)
 "router": {"ok": true, "uptime_s": 11410, "cpu_pct": 6, "version": "7.20.7 (long-term)"},
 "uplink": {
  "state": "WEAK",                   // GOOD | WEAK | BUSY | CAPTIVE | DOWN | NO_ROUTER (UNKNOWN: added by the reader)
  "say": "Uplink weak: -83 dBm on UW MPSK", // one operator line; show it as is
  "via": "wifi",                     // wifi | wired | none
  "network": "UW MPSK",              // the SSID, "wired", or null if unknown
  "ap": "AC:23:16:ED:A5:91",         // the campus access point, while on Wi-Fi
  "associated": true,
  "signal_dbm": -83, "snr_db": 25, "ccq_pct": null,   // null on a wired uplink, or when not reported
  "rate_mbps": 52.0,                 // the radio's receive rate (PHY); real throughput is ~50–65 % of it
  "down_mbps": 4.8, "up_mbps": 0.64, // measured since the last poll; null on the first
  "since_s": 6216,                   // time since this association
  "address": "10.19.194.172/15", "gateway": "10.18.0.1",
  "clients": 2,                      // devices on the robot's own Wi-Fi
  "drops_24h": 2,                    // uplink losses and reassociations in the last 24 h
  "internet": true, "captive": false,// from the probes
  "latency_ms": 11.8                 // ping to the uplink gateway; null if it doesn't answer ping
 },
 "robot_wifi": [                     // strongest first
  {"name": "Galaxy-Tab-A11", "ip": "10.42.2.13", "signal_dbm": -48, "ccq_pct": 83, "connected_s": 11328}
 ]
}
```

**States, in order of precedence** (the first that holds wins):

| State | Rule | The operator should |
|---|---|---|
| `NO_ROUTER` | The Thor can't reach the router | Check the router's power and the Thor's cable |
| `DOWN` | No association or wired link, no uplink address, or the HTTPS probe has failed 3 times in a row | Read the `say` line; move the robot or plug in |
| `CAPTIVE` | The captive-portal probe gets a redirect or a page | Open any page from a device on the robot Wi-Fi and accept the portal |
| `BUSY` | Downstream traffic above half of `rate_mbps` (the radio's PHY rate) for two polls in a row, about 10 s. On 2026-10-19 a full link ran at 31 of 52 Mbit/s | Expect slow cloud features; something is downloading |
| `WEAK` | Signal below −75 dBm, or SNR below 20 dB | Expect drops; move the robot if it matters |
| `GOOD` | Otherwise | — |

The thresholds are a first guess, to be tuned on the robot (`netmon.py` has them at the top). The page needs only `state` and
`say`; the numbers are for a details view if the Tablet lane wants one.

## 5. The diagnostic: `netdiag`

One command, run **on the Thor** (`sudo netdiag`, since it reads the root-only file). It walks the
layers bottom-up and stops at the first failure with a plain sentence:

1. Thor ↔ router: the link, a ping, and REST reachable (credentials work, certificate pin matches).
2. Router ↔ site: associated or not, AP, signal, SNR, CCQ, rate, time since association, drops in
   the poller's history and in the router's log.
3. Uplink address: DHCP bound, gateway, DNS servers. Also flags an uplink subnet that overlaps
   the robot LAN 10.42.2.0/24.
4. Gateway ping, DNS resolution, HTTPS to the internet, captive-portal check.
5. Load: traffic now against the link estimate, and queue drops once U-5 exists.
6. The robot's own Wi-Fi: each client's name, address, signal and CCQ, with the weak ones flagged.

It prints a one-line verdict first ("The uplink is up but weak: −81 dBm, 2 drops today") and the
detail under it. `--json` gives the same data for a ticket or a chat. It sends no traffic test that
would fill the link, and it never writes to the router.

## 6. Where it lives

The Network lane's path **`tools/network/`** (CLAUDE.md "Lanes"), holding the poller, `netdiag`, `netjoin`, the systemd unit,
and tests for the state rules only. Installing the unit and the env file's directory goes through
the Thor lane's deploy scripts (`deploy-all.sh`, `provision-all.sh`). The dashboard change in §4
is the Tablet lane's: about ten lines in `status.py` and a line on the page.

Tests that earn their place: the state rules (§4) against recorded router replies, since a wrong
state misleads the operator, and the stale-file rule. Not needed: a live-router test in CI.
