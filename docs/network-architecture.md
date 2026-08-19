# Network Architecture — Connectivity, Access Control & Security

**Status: design — not yet implemented.** Deployment context: the robot(s) run
mostly at **UW / Allen School (CSE)**. Companion to
[control-authority.md](control-authority.md),
hardening-roadmap.md (track 6 deploy/ops),
capability-backlog.md (#3 onboard WiFi).

> UW network *policy* specifics (client isolation, NAT, inbound rules) should be
> **confirmed with CSE-IT / UW-IT** — the postures below are the usual shape, not
> a guarantee.

## Two boundaries (keep them separate)

- **A — Uplink:** how the robot reaches campus/internet.
- **B — Onboard island:** the private LAN where the Thor, StampLC, Pixhawk,
  cameras, and teleop live.

Decide them independently: **CSE for A, self-hosted dedicated router for B.**

## Part 1 — Uplink: which campus network

| Network | Auth | Inbound reachability | Use as |
|---|---|---|---|
| **eduroam** | 802.1X, per-*user* creds | none (isolated + NAT'd); also bakes a personal NetID into a shared robot | last-resort egress only |
| **UWMPSK** | per-*device* PSK (reg. to NetID) | usually isolated/NAT'd → none | clean headless egress (no 802.1X supplicant) |
| **CSE / Allen School** (wired + wireless) | MAC-registered → `XXX.cs.washington.edu` | **yes** — stable DNS, CSE-IT firewall policy, on-campus/VPN-gated | **management + access home** |

**Decision:** home network = **CSE** (registered hostnames; CSE-IT owns firewall
policy = the institutional "cut access" lever; the only option with real
reachability + governance). **UWMPSK = opportunistic egress** when roaming out of
CSE wireless coverage (better than eduroam for headless: per-device PSK, no
enterprise supplicant, no personal credential on the robot). **eduroam = fallback
uplink only**, never an access path. On eduroam/UWMPSK inbound is impossible
anyway — don't fight it; reach the robot via its CSE hostname or the fleet VPN.

## Part 2 — Onboard island: self-host, but NOT on the Thor

**Self-host: yes.**
- ROS 2 **DDS discovery is multicast + chatty** and camera/LIDAR/pointcloud topics
  are fat — must NOT traverse campus WiFi (floods it; client isolation breaks
  discovery). The ROS graph lives on a private LAN.
- **Mobility:** internal comms stay solid regardless of campus coverage (incl.
  outdoors between buildings).
- **One controllable security wall** between robot internals (never internet-facing)
  and the outside.

**NOT the Thor as AP** — the Thor is a *client* on the LAN:
- AP+STA on one radio is unreliable/half-duplex (want uplink + local AP on separate
  radios); hostapd couples network uptime to the autonomy compute; no wired switch
  ports for Thor / Ethernet cameras / docking uplink.

**Dedicated onboard router — chosen: MikroTik hAP ax³** ([product page](https://mikrotik.com/product/hap_ax3)).
A single box, not a router+switch+AP stack — the power/packaging budget is 2×DeWalt
20 V packs, so device count matters. Why it fits the stated requirements:
- **One device, ≥3 ports:** 4× GbE + 1× 2.5G (the 2.5G → Thor). The built-in switch
  ports are the wired fan-out (cameras, Pixhawk-if-Ethernet, docking uplink). An
  *external DIN-rail managed switch* (e.g. Teltonika TSW202) is acceptable **only as
  an experimentation add-on** if a class needs many wired devices — not the baseline.
- **Dual-band = the "two radios":** STA uplink to campus on one band, local-control
  AP (own SSID, works anywhere) on the other. Don't run uplink + AP on the same band.
- **PoE-out (15 W total)** on the 2.5G port — powers one modest PoE device (verify
  vs. the actual camera; most depth cams are USB, not PoE).
- **External antennas** (relocatable via RP-SMA extensions) — the base is mostly
  metallic, so mount the radio in the base and move just the antennas to a
  non-occluded spot (corner post / short stub). WiFi 6, WPA3.
- **WireGuard** (RouterOS native) for secure remote/fleet access, zero inbound exposure.
- **Powered off the logic mini-PDH** (upstream of the e-stop contactor) so the
  network survives an e-stop. **Verify the ax³ DC-input range** and add a buck from
  the ~20–24 V logic rail if needed (unlike the Teltonika RUTX10's native 9–50 V,
  the ax³'s input is narrower — confirm before wiring).

*Runner-up was the Teltonika RUTX10* (industrial DIN box, native 9–50 V reverse-
polarity input — bolts straight to the battery rail) — lost on no PoE-out, WiFi 5,
and higher cost. Reconsider if industrial packaging / direct-battery input ever
outweigh PoE + cost. (Not a "standard"; chosen on requirement fit.)

## Connectivity plan & firewall zones

The router is the **gateway, NAT boundary, and the campus-facing identity**. The
thing that associates to CSE (registered MAC → `robotN.cs.washington.edu`) is the
router's WAN interface; the Thor and internals are **never individually on campus**
— from CSE-IT's view one robot = one registered host (the router), with all internal
traffic NAT'd out behind it. To reach *into* the robot, an admin connects **to the
router** (WireGuard) which routes to the private subnet; internals are never directly
addressable from outside.

```
   WAN (uplinks, priority): ① wired dock → CSE wired  ② WiFi-STA → CSE/UWMPSK  ③ LTE (later)
                                   │  NAT · stateful firewall · DNS/DHCP · WireGuard endpoint
                 ┌─────────────────┴─────────────────┐
        LAN-CORE (trusted 10.42.N.0/24)        STUDENT VLAN (guest SSID, isolated)
        Thor(2.5G) · cameras · Pixhawk ·       student laptops/phones
        StampLC-bridge · ROS/DDS island
```

**"Outbound-only" = stateful NAT, not "nothing comes back."** The firewall blocks
*unsolicited* inbound, not replies. Updates/downloads (apt, pip, ROS pkgs, container
pulls, NTP, model weights) are **outbound-initiated**, so they're permitted by the
`LAN-core → WAN` rule + ESTABLISHED/RELATED — **no inbound hole needed**. Campus auth
(UWMPSK PSK / CSE MAC-reg / eduroam 802.1X with a *service* identity, not a personal
NetID) happens at the router's STA uplink, invisible to the internals.

| From → To | Policy |
|---|---|
| LAN-core → WAN | **ALLOW** (stateful) — updates/downloads live here |
| WAN → internal | **DROP new**; ESTABLISHED/RELATED only |
| LAN-core → LAN-core | ALLOW (the ROS island) |
| Admin (WireGuard peer) → LAN-core | ALLOW |
| Student → dashboard :8080 (+ optional teleop / Foxglove RO) | ALLOW (narrow allowlist) |
| Student → rest of LAN-core (ROS svcs, StampLC, SSH) | **DENY** |
| Student → WAN (internet) | **DENY** (robot uplink is for the robot, not a hotspot) |
| Student → Student | DENY (client isolation) |

**Policy levers:**
- **Student internet = denied by default** (decided) — students reach the application
  surface only, not the internet (avoids campus AUP/bandwidth issues + NAT'ing student
  traffic under our registered host). Easy to relax later.
- **Bulk-download / update gating = noted, NOT enforced** (decided) — egress is allowed
  continuously; too much dev work ahead to bottleneck updates to the dock. Revisit as
  an ops policy (update-while-docked) once the build settles.
- *Later hardening:* **egress allowlisting** (restrict the Thor's outbound to known
  destinations) to shrink blast radius — not now; wide-open egress + default-deny
  ingress is the fine baseline.

## Access control — grant/revoke as a policy knob

- **Students never touch robot internals.** Defined application surface only: the
  kiosk dashboard (HTTP), optional teleop endpoint, optional Foxglove read-only.
  ROS actuator services, StampLC command endpoints, SSH → LAN-only / admin-only.
- **Cut-off = one action:** disable the student SSID / revoke a WireGuard peer /
  flip a router firewall rule / disable the dashboard student role — instantly,
  fleet-wide if centrally managed.

## Fleet (several robots for a class)

- Per-robot subnet plan, e.g. `10.42.<robot-id>.0/24` — no collisions; robot N's
  dashboard predictably addressable.
- CSE-IT-registered hostnames per unit; small **fleet VPN** (each robot a WireGuard
  peer to a coordinator) for management.

## Security debt in what we already run (fix before exposure)

1. **Dashboard has no authN** (`uvicorn --host 0.0.0.0:8080`). Fine on a trusted
   LAN; **add auth before any student-facing exposure.**
2. **rosbridge (:9090) + foxglove (:8765) bind `0.0.0.0`, no auth** → bind to the
   LAN interface + firewall from WAN (enforces the existing "onboard network only"
   convention).
3. **Default-deny inbound at the router WAN.** Robot initiates outbound only
   (updates, NTP, VPN dial-out). ROS/DDS/StampLC/Pixhawk never WAN-reachable.

## Open decisions

- ~~Router SKU~~ → **decided: MikroTik hAP ax³** (single device). Pre-buy checks:
  exact DC-input range (buck from logic rail?), RP-SMA connector match for antenna
  extensions, and whether 15 W PoE-out covers the actual camera (or it's USB anyway).
- **Cellular** for true off-campus — deferred (out of scope now). If needed later,
  add via a USB LTE modem on the ax³ or a cellular-capable router (Teltonika RUTX11).
- Central fleet management: CSE-IT-managed policy vs. our own coordinator/VPN vs. both.
- Dashboard auth mechanism (CSE SSO / OIDC vs. local accounts vs. reverse-proxy auth).
- Whether teleop/VR (capability-backlog #7) gets a dedicated high-rate path/VLAN.
- Docking connector (power + gigabit) mechanical design.
