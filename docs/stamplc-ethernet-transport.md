# StamPLC ↔ Thor over Ethernet (PoE UDP)

The StamPLC has its own 24 V supply and a PoE/W5500 Ethernet port, so it does **not** need USB
for power or for talking to the Thor. This moves the StamPLC's telemetry + command link off USB
(and off the shared USB hub, which has been a reliability liability — see the lidar hub notes) onto
Ethernet, leaving USB for **firmware flashing only**.

## ⚠️ First flash is USB; reflashes are OTA over Ethernet

**`esptool` cannot flash over Ethernet** — it talks to the ESP32-S3 bootloader over the USB-CDC
port, and the PoE link only carries the runtime telemetry/command protocol. But once the
OTA-capable firmware is on the board, **reflashing goes over Ethernet** (no USB):

- **Only the very first flash of a StamPLC is over USB.** When replicating a robot, wire and flash
  the StamPLC over USB first, confirm it boots, then move to Ethernet for normal operation.
- **After that, reflash with OTA** — `firmware/rc_serial_probe/ota-from-thor.sh` builds, serves the
  `.bin`, and sends the `OTA` command so the board pulls it and reboots into it. No USB, no touching
  the robot.
- **Keep a USB port reachable only as a recovery fallback.** A complete-but-corrupt OTA image needs
  a USB reflash to recover — USB is *not* needed for routine reflashing.
- USB flash procedure (initial + recovery): stop `opendubs-dash` (frees the port), `pio run -t
  upload`, restart the dash — or just run `flash-from-thor.sh`. See the firmware README.

## How it works

Both transports carry the **same `[TAG]` text protocol** (`[SENS] … [PDH] … [RC] … [DRIVE] …`), so
the Thor-side parser (`kiosk-dashboard/backend/stamplc.py`) is transport-agnostic.

- **Telemetry (StamPLC → Thor):** every `[TAG]` line is teed to USB Serial **and**, when the PoE
  link is up, broadcast as a UDP datagram on **port 5005** (`tprintf()`/`temit()` in the firmware).
  The UDP send is **independent of the USB TX buffer** — with USB unplugged the CDC buffer fills and
  `availableForWrite()` → 0, so gating UDP on it would silence Ethernet exactly when it's needed.
- **Commands (Thor → StamPLC):** the Thor unicasts command lines (`LED_COLOR …`, `PAGE …`,
  `PDH_SWITCH …`) to the StamPLC's IP:5005. The firmware's `pollNetCommands()` reads them and
  routes them through the **same** `handleHostCommand()` dispatcher as the USB serial path. Lines
  starting with `[` are ignored (own telemetry echoed on broadcast).
- A compact-JSON heartbeat also broadcasts on 5005 (`netTelemetry()`); the Thor ignores it
  (doesn't match a `[TAG]` regex). Harmless.

## Transport selection (Thor side)

`stamplc.py` picks a transport via `OPENDUBS_STAMPLC_TRANSPORT`:

| value | behavior |
|---|---|
| `auto` (default) | prefer Ethernet — probe UDP 5005 ~4 s; if telemetry arrives use it, else fall back to USB serial |
| `udp` | force Ethernet (wait for UDP telemetry) |
| `serial` | force USB serial (the pre-migration path) |

Also: `OPENDUBS_STAMPLC_UDP_PORT` (default `5005`). The StamPLC's IP is learned from the source
address of its telemetry (survives DHCP changes). `snapshot()` now reports `"transport": "udp"|"serial"`.

## Apply / verify runbook (next time at the Thor)

1. **Flash the firmware over USB** (this build adds the UDP tee + command receiver):
   `pio run -t upload --upload-port <stamplc by-id>` (dash stopped). Confirm `[NET] eth UP ip=…`
   appears on the USB serial at boot — that's the DHCP-assigned IP and confirms the PoE link + a
   reachable DHCP server on the Thor's subnet.
2. **Deploy the dashboard side:** copy `kiosk-dashboard/backend/stamplc.py` to
   `/opt/opendubs/kiosk-dashboard/backend/`, restart `opendubs-dash`.
3. **Verify Ethernet telemetry reaches the Thor:**
   `sudo tcpdump -n -i any udp port 5005` → expect `[TAG]` datagrams from the StamPLC IP.
4. **Force UDP and confirm the dashboard is still fully populated** (power/RC/DRIVE/thermal/BMS):
   set `OPENDUBS_STAMPLC_TRANSPORT=udp` in the dash unit's environment, restart, check
   `/api/status` shows `stamplc.transport == "udp"` and all panels live.
5. **Verify a command round-trips over Ethernet:** trigger a PRISM color change (state change) and
   confirm the LEDs update with USB **unplugged**.
6. Only after 3–5 pass, unplug the StamPLC USB for normal operation.

## Gotchas

- The PoE link must get a **DHCP lease on the same subnet as the Thor** and its broadcast must reach
  the Thor (no VLAN/subnet split between the StamPLC's switch port and the Thor). If `tcpdump` sees
  nothing on 5005, the link is down or on a different segment — check first.
- If Ethernet drops mid-run, `auto` mode falls back to USB serial automatically **if** USB is still
  plugged; with USB unplugged the dashboard StamPLC panels go stale until Ethernet returns.
- Command ACK lines (`[PDH] cmd …`, `[PRISM] color …`) stay USB-only (debug), not teed — the
  dashboard doesn't parse them, so nothing is lost over Ethernet.
