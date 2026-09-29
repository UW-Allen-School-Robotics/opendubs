# Network architecture

**Status: design — not fully implemented.** How the robot connects, keeps its internal ROS network
private, and controls who can reach what.

<div style="position: relative; padding-bottom: 60%; height: 0; overflow: hidden; border: 1px solid var(--md-default-fg-color--lightest); border-radius: 0.2rem; margin: 1em 0;">
  <iframe style="position: absolute; inset: 0; width: 100%; height: 100%; border: 0;" src="https://embed.figma.com/board/LVbgJwMKO65TARF3m6v7I6/OpenDubs?node-id=46-2423&embed-host=opendubs-docs" allowfullscreen loading="lazy" title="OpenDubs network architecture (Figma)"></iframe>
</div>

- **Diagram source (Figma):** [OpenDubs board — network architecture](https://www.figma.com/board/LVbgJwMKO65TARF3m6v7I6/OpenDubs?node-id=46-2423) — anyone with the link can view and export it.

## Two boundaries — keep them separate

- **A — Uplink:** how the robot reaches the internet / your site network.
- **B — Onboard island:** the private LAN where the Thor, StamPLC, cameras, and teleop live.

Decide them independently.

## The onboard island — self-hosted, but not on the Thor

**Run a private LAN on the robot.** ROS 2's **DDS discovery is multicast and chatty**, and
camera / LIDAR / pointcloud topics are fat — that traffic must **not** cross your site WiFi (it
floods it, and client isolation breaks DDS discovery). So the ROS graph lives on a private on-robot
LAN. You get internal comms that stay solid regardless of site coverage (including outdoors), and
**one controllable wall** between the robot internals (never internet-facing) and the outside.

**Don't make the Thor the access point** — it's a *client* on the LAN. AP+STA on one radio is
unreliable / half-duplex, it couples network uptime to the autonomy compute, and it gives you no
wired switch ports for the Thor, Ethernet cameras, or a docking uplink.

**Use one dedicated onboard router** — a single box (router + switch + AP), not a stack, because the
power/packaging budget is tight (one battery + DC/DC rails) so device count matters. What it needs:

- **≥3 wired ports** — the built-in switch is the wired fan-out (Thor, cameras, docking uplink).
- **Dual-band = "two radios"** — a station (STA) uplink to your site network on one band, and a
  **local-control AP** (its own SSID, works anywhere) on the other. Don't run uplink + AP on one band.
- **External antennas** — the base is mostly metal, so mount the radio inside and relocate just the
  antennas to a non-occluded spot.
- **A VPN** (e.g. WireGuard) for secure remote / fleet access with zero inbound exposure.
- **Power it off the always-on logic rail** (upstream of the e-stop contactor) so the network
  survives an e-stop. Confirm the router's DC-input range and add a buck from the logic rail if needed.
- PoE-out is a nice-to-have (powers a PoE camera; most depth cams are USB anyway).

*(The baseline uses a single small MikroTik hAP; any router meeting the above works.)*

## The uplink

The router's **WAN interface is the only thing that touches your site network** — from that
network's view, one robot = one device (the router), with all internal traffic NAT'd out behind it.
Use whatever uplink your site offers, in priority order — a **wired dock**, **WiFi**, or **cellular**
(later). Reaching *into* the robot is done by connecting **to the router** over the VPN, which routes
to the private subnet; internals are never directly addressable from outside.

## Firewall zones

The router is the **gateway, NAT boundary, and site-facing identity.**

**"Outbound-only" = stateful NAT, not "nothing comes back."** The firewall blocks *unsolicited*
inbound, not replies — so updates/downloads (apt, pip, ROS packages, container pulls, NTP) work
outbound with **no inbound hole needed.**

| From → To | Policy |
|---|---|
| LAN-core → WAN | **ALLOW** (stateful) — updates/downloads live here |
| WAN → internal | **DROP new**; ESTABLISHED/RELATED only |
| LAN-core → LAN-core | ALLOW (the ROS island) |
| Admin (VPN peer) → LAN-core | ALLOW |
| Guest → dashboard `:8080` (+ optional teleop / Foxglove read-only) | ALLOW (narrow allowlist) |
| Guest → rest of LAN-core (ROS services, StamPLC, SSH) | **DENY** |
| Guest → internet | **DENY** (the uplink is for the robot, not a hotspot) |
| Guest → Guest | DENY (client isolation) |

## Access control

- **Users never touch robot internals** — only a defined application surface: the kiosk dashboard
  (HTTP), an optional teleop endpoint, optional Foxglove read-only. ROS actuator services, StamPLC
  command endpoints, and SSH stay LAN-only / admin-only.
- **Cut-off is one action** — disable the guest SSID, revoke a VPN peer, flip a router firewall rule,
  or disable the dashboard's guest role — instantly, and fleet-wide if centrally managed.

## Fleet (several robots)

- Per-robot subnet (e.g. `10.42.<robot-id>.0/24`) — no collisions; each robot's dashboard is
  predictably addressable.
- A small **fleet VPN** (each robot a VPN peer to a coordinator) for management.

## Secure it before you expose it

The baseline runs some services open on the trusted LAN. **Before exposing the robot to any
untrusted user, close these:**

1. **The dashboard has no authentication** (`uvicorn 0.0.0.0:8080`) — fine on a trusted LAN; **add
   auth before any user-facing exposure.**
2. **rosbridge (`:9090`) and Foxglove (`:8765`) bind `0.0.0.0` with no auth** — bind them to the LAN
   interface and firewall them off the WAN.
3. **Default-deny inbound at the router WAN** — the robot initiates outbound only (updates, NTP, VPN
   dial-out); ROS / DDS / StamPLC are never WAN-reachable.

## See also

- [Control authority & modes](control-authority.md)
- [Power & data interface](interfaces/power-data.md) — the comms buses a payload can use.
