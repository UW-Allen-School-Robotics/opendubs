# Taking the robot off campus: joining other networks, and eduroam

**DRAFT, 2026-10-19, Network lane.** Design only; nothing is configured. Plan items N-J1…N-J5 and
N-E1…N-E4 in [`../workplan-network.md`](../workplan-network.md).

**The goal.** The router should join a venue's network as its uplink the same way it joins
UW MPSK today. The robot's own Wi-Fi ("Opendubs2") and its LAN (10.42.2.0/24) must stay exactly
as they are. An operator adds or switches a network without the router's admin web page and
without any risk of locking themselves out.

MikroTik facts come from the
[Wireless Interface](https://help.mikrotik.com/docs/spaces/ROS/pages/8978446/Wireless+Interface)
and [WiFi](https://help.mikrotik.com/docs/spaces/ROS/pages/224559120/WiFi) pages, read
2026-10-19. Facts about this router come from a read-only session the same day.

---

## 1. Constraints that shape every option

- **The uplink radio is 5 GHz only.** `wlan2` is the 5 GHz radio, and the robot's own Wi-Fi uses
  the 2.4 GHz one. The robot Wi-Fi **must stay on 2.4 GHz**, because its ESP32 devices (the neck
  dial, the head's Wi-Fi co-processor) have no 5 GHz. So a 2.4 GHz-only network cannot be the
  uplink: many phone hotspots by default, and some older venue networks. Ways round it are a
  wired uplink, a hotspot set to 5 GHz, or a separate uplink radio (workplan U-4).
- **The router runs MikroTik's legacy `wireless` package.** It has no WPA3, so a WPA3-only venue
  network can't be joined. Mixed WPA2/WPA3 networks are fine. The newer `wifi-qcom-ac` package
  supports the hAP ac³, but switching means uninstalling `wireless`, uses more memory, and has
  no connect-list (§2). §3 explains why eduroam may still be a reason to switch.
- **Nothing on the LAN side changes.** Every option here touches only the WAN side: `wlan2`'s
  security profiles and connect list, and the `ether1` DHCP client. The bridge, `wlan1`, the
  DHCP server, 10.42.2.0/24 and the firewall are untouched. The Thor always reaches the router
  over the LAN, so a bad uplink entry can't lock anyone out.
- **The uplink keeps the radio's own hardware MAC address, everywhere.** That is what UW MPSK
  registered, and it means one MAC address to register at any venue that asks.
- **Uplink subnet overlap.** If a venue hands out addresses in 10.42.2.0/24, routing breaks. This
  is rare; `netdiag` flags it ([`network-router-api.md`](network-router-api.md) §5).

## 2. Kinds of uplink

| Uplink | How the router joins | Registration | Notes |
|---|---|---|---|
| **UW MPSK** (today) | WPA2-PSK, its own security profile | **Yes**: a per-device passphrase bound to the radio's MAC (see §3 for UW's terms) | |
| **A venue's WPA2-PSK network** | One security profile (WPA2-PSK, the venue's passphrase) and one connect-list entry | Usually none | 5 GHz only (§1) |
| **Open or captive-portal network** | A security profile with no encryption, and a connect-list entry | None, but the portal acceptance is bound to the router's MAC address | Because of NAT, the portal sees one device: the router. Accept it once from any device on "Opendubs2" (a laptop, or the tablet), and the whole robot is through. The poller's `CAPTIVE` state says when to accept it again. Inbound stays blocked by the router's firewall; the Wi-Fi hop is unencrypted, as for any device there |
| **Phone hotspot** | WPA2-PSK like a venue | None | **Set the hotspot to 5 GHz**: on an iPhone, turn "Maximize Compatibility" off; on Android, choose the 5 GHz band. Mind the phone's data cap: a model download will use it up |
| **Wired, into `ether1`** | `ether1` is already in the WAN list with a DHCP client (idle with no cable) | At UW: a campus wired port normally needs the port's device registered with the department's IT, by `ether1`'s MAC. Elsewhere: whatever the venue asks | **Recommended change:** give the `ether1` route distance 1 and the `wlan2` route distance 2 (both are 1 today), with `check-gateway=ping`. A plugged-in cable then wins automatically, and pulling it falls back to Wi-Fi |
| **USB tethering from a phone** | The hAP ac³ has a USB port | None | **Unverified** whether RouterOS 7.20 takes Android tethering on this board. Only worth checking if a 2.4 GHz-only venue becomes a real case |

## 3. How an operator adds or switches a network

**A. Saved networks with a priority order (the base, set up once).** In the legacy package, the
station's connect list holds one entry per known network, each with its own security profile. The
interface's own SSID is left empty. When several known networks are in range, the station joins
the one **highest in the list**, and "only the first matching rule is applied". Per entry,
`signal-range` can refuse a network that is too weak. An example order: wired always wins by
route distance; then the lab's venue network, UW MPSK, eduroam (if §4 lands), and the team's
hotspot last. Switching networks = moving an entry, or disabling the ones above it.

**B. `netjoin`, a command on the Thor (recommended for adding a network in the field).**
`sudo netjoin add "Venue WiFi"` asks for the network's passphrase, with no echo. It then asks for
the router's admin password, which the operator types from the password manager, so **no
write-capable router credential is ever stored on the robot**. It creates the security profile
and connect-list entry, tagged with the comment `netjoin`. Other subcommands:

| Subcommand | What it does |
|---|---|
| `list` | the saved networks, in order |
| `prefer` | move a network to the top |
| `disable` / `remove` | switch a network off, or delete it |
| `scan` | list visible 5 GHz networks |
| `status` | the same as `netdiag`'s first line |

- It only ever touches entries tagged `netjoin`, plus the one-time empty-SSID change on `wlan2`.
  It never touches the bridge, `wlan1`, DHCP or the firewall.
- After a change it waits up to 60 s for the new uplink to work, then reports the result. If the
  new network fails, the previous entries are still there, so the robot falls back on its own.
- Warning: **`scan` interrupts the uplink while it runs**, because the radio leaves its network to
  scan. The command says so and asks before scanning.

**C. A form on the dashboard.** Not now. It would need a write path from the dashboard to the
router. Revisit only after B has been used in the field.

**Recommendation:** A as the base, set up once at the robot with the user's word. Then B, built in
`tools/network/` with the poller ([`network-router-api.md`](network-router-api.md) §6).

## 4. eduroam with credentials not tied to one person

UW's Wi-Fi pages now live in the UWConnect knowledge base (`uwconnect.uw.edu`, cited by article
number, read 2026-10-19). Each claim is marked **documented** (on the cited page) or **inferred**
(this lane's reading).

### 4.1 What UW offers

- **UW eduroam is EAP-TLS, not a password.** A device onboards through UW's SecureW2 "JoinNow"
  tool (onboard.wifi.uw.edu), which installs a device certificate signed by the "University of
  Washington Device Intermediate CA". Documented in
  [KB0034255](https://uwconnect.uw.edu/it?id=kb_article_view&sysparm_article=KB0034255).
  - Onboarding needs a current student, faculty or staff NetID with the "Eduroam Radius Access"
    service turned on.
  - Manual setup ("non-specific OS") works but UW-IT doesn't support it. Documented in the
    [FAQ, KB0034256](https://uwconnect.uw.edu/it?id=kb_article_view&sysparm_article=KB0034256).
  - The certificate lasts 4 years by the eduroam page and 6 years by the FAQ. **The two pages
    disagree.**
- **The RADIUS server** is `radius.uw.edu`. Its chain is InCommon RSA Server CA 2, under the
  USERTrust RSA root. The FAQ's published server fingerprint expired on 2026-03-14, so it is
  stale. Documented, KB0034256. **Inferred:** check the CA chain, not that fingerprint.
- **UW MPSK** is WPA2-Personal with one key per registered MAC address, meant for IoT and
  headless devices. Documented in
  [KB0034261](https://uwconnect.uw.edu/it?id=kb_article_view&sysparm_article=KB0034261).
  - Registration is at register.wifi.uw.edu, with an expiry date, and is **owned by a NetID**.
  - **Faculty and staff may register devices under a departmental shared NetID.**
  - Randomized MAC addresses don't work.
- **Shared NetIDs** are owned by faculty or staff, and their administrators can include students.
  Request one with UWConnect's "Request a Shared Account" form; extra services are added through
  the Provisioning Request Tool. Shared NetIDs "should not be used in place of individual
  accounts". Documented in
  [KB0033938](https://uwconnect.uw.edu/it?id=kb_article_view&sysparm_article=KB0033938).
- **No UW page says a shared NetID can get Eduroam Radius Access or an eduroam certificate.** That
  is the open question.
- **The most promising lead (documented, KB0034256):** a sysadmin who needs eduroam on "multiple
  machines" should email help@uw.edu and ask about "the eduroam managed server solution". The
  request is routed to the Wireless Services team. The process isn't described publicly.
- **Ruled out:**

  | Option | Why not | Source |
  |---|---|---|
  | Temporary NetIDs | They cover only the "University of Washington" SSID, which is being retired | [KB0034264](https://uwconnect.uw.edu/it?id=kb_article_view&sysparm_article=KB0034264) |
  | Admin and sponsored NetIDs | Not device accounts | [KB0033932](https://uwconnect.uw.edu/it?id=kb_article_view&sysparm_article=KB0033932) |
  | Guest access to eduroam | "Not currently" offered | KB0034256 |
  | A personal NetID on the robot | "Against policy" to share a personal NetID | [KB0036752](https://uwconnect.uw.edu/it?id=kb_article_view&sysparm_article=KB0036752) |

### 4.2 Can this router join eduroam?

- **EAP-TLS is the right method for this router** (inferred from MikroTik's docs, not yet
  tried).
  - In the legacy package, a station's WPA2-EAP profile takes a client certificate
    (`tls-certificate`). With `tls-mode=verify-certificate` it checks that the server's
    certificate is "signed by a known certificate authority", with "no additional identity
    verification".
  - So the router would trust whatever CAs are imported (only the USERTrust/InCommon chain),
    without checking the server's name.
  - With EAP-TLS that weakness costs little: the robot has no password to give away, and a fake
    network learns no more than an open network would.
- **PEAP would be worse.** MikroTik's legacy docs don't say a station can validate the server for
  PEAP at all. UW doesn't use PEAP anyway.
- **The `wifi-qcom-ac` package** has an explicit server-certificate policy
  (`eap-certificate-mode`, default `dont-verify-certificate`). It still documents no CA choice and
  no name check, and it loses the connect list. It isn't a reason to switch.
- **To verify, on the spare field router, never on opendubs2:**
  1. Export the SecureW2 certificate with its key as a PKCS#12 file.
  2. Import it, with the CA chain, into RouterOS's certificate store.
  3. Associate once to eduroam.

  It's one attempt, and the only test here that earns its place: if it fails, the request to UW
  changes.

### 4.3 Away from UW

- **Documented** ([eduroam.org](https://eduroam.org/how/)): eduroam forwards authentication by
  realm to the home institution's RADIUS server, so the robot's UW credential works at any eduroam
  site. The visited site's own firewall applies, and visitors "may have access to fewer services".
- **Inferred:** a few sites block outbound ports or isolate clients. The robot only needs outbound
  HTTPS and DNS, so it should rarely matter. Venues' eduroam is almost always also on 5 GHz, which
  §1 requires.
- **For a team building this robot elsewhere:** ask your campus IT for a device or service
  eduroam credential, which under EAP-TLS is a client certificate. eduroam Managed IdP is for
  institutions with no identity system of their own
  ([eduroam.org](https://eduroam.org/eduroam-managed-idp-launched-and-in-production/)), not for
  individuals. eduroam-US policy on device accounts couldn't be read (the site wouldn't load).

### 4.4 Bigger than eduroam: UW's rule on departmental access points

**Documented**, in UW's Wi-Fi policy
([KB0034266](https://uwconnect.uw.edu/it?id=kb_article_view&sysparm_article=KB0034266)):

- "Installing departmental or DIY Wi-Fi access points is prohibited" without a UW-IT exemption.
  Research exemptions are requested at help@uw.edu.
- "Only devices authenticated via UW NetID may access network resources outside the UW network."

The coexistence guideline for exempted networks
([KB0034258](https://uwconnect.uw.edu/it?id=kb_article_view&sysparm_article=KB0034258)) requires:

- approval before installing;
- a unique SSID;
- WPA2 or 802.1X;
- 2.4 GHz channels 1, 6 or 11 only, at 20 MHz;
- no "mesh or wireless repeater technology".

**Inferred:**
- The robot's router serves its own access point on campus and is itself a client of campus
  Wi-Fi. It likely needs this exemption whatever its uplink credential.
- Its laptops and phones reach the internet through the router's registration rather than their
  own, which could be read as a repeater.
- Today the robot's AP is set to 20/40 MHz on an automatic channel. Under the guideline it would
  be **20 MHz on channel 1, 6 or 11**. That is a two-minute change, and clients re-associate for
  a few seconds.
- An exemption request is also the natural place to ask the eduroam question, in one ticket.

### 4.5 The path, for the user (nothing is requested or sent by this lane)

1. **A departmental shared NetID owned by a faculty or staff member** of the department: an
   existing one through the department's IT, or UWConnect's "Request a Shared Account".
   Move the robot's UW MPSK registration under it, so it doesn't lapse with one person's NetID.
2. **One ticket to UW-IT** (help@uw.edu, asking for routing to Wireless Services), covering:
   - (a) a research exemption for the robot's onboard access point under KB0034266, describing
     it as a mobile, WPA2, 2.4 GHz, 20 MHz, NAT'd robot network;
   - (b) whether that shared NetID can get Eduroam Radius Access and an EAP-TLS device
     certificate, or the "eduroam managed server solution" for a fleet of robots.
3. **Ask the department's own IT** (support@cs.washington.edu) whether it already holds an
   exemption or a device-registration process for research robots. Its lab pages are behind a
   login.
4. Then the bench test in §4.2, then add eduroam to the connect list (§3 A).
