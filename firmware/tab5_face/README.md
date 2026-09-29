# Tab5 Face & Payload Controller

The `tab5_face` firmware runs on the M5Stack Tab5 (ESP32-P4) and serves as the primary payload controller and dynamic UI for the OpenDubs robot.

## Features
- **Dynamic Face UI:** Displays real-time robot state and smooth animations.
- **Wireless Telemetry (UDP):** Streams Time-of-Flight (ToF) distance and UI events back to the Thor via a CAN-over-UDP protocol.
- **Autonomous Head Tracking:** Receives UDP pan/tilt commands from Thor's YOLOv8 node and drives the I2C servos.
- **Over-The-Air (OTA) Updates:** Allows for wireless flashing of firmware.

## Build and Upload (USB)

By default, the `platformio.ini` is configured to upload over USB. Ensure your Tab5 is connected via the USB-C port.

```bash
# Upload via USB (Default)
pio run -t upload
```

## Over-The-Air (OTA) Upload

**Use `./ota-from-thor.sh` — do not improvise.** It builds, serves the binary, tells the head to
pull it, and confirms it came back:

```bash
./ota-from-thor.sh                    # resolves opendubs-tab5.local
./ota-from-thor.sh 10.42.2.14         # or pass an address
```

Requires **Python >= 3.10** for PlatformIO (pioarduino is the only platform that builds the
ESP32-P4 and refuses 3.9) and `intelhex` for esptool's image packer. The script finds a usable
`pio` itself, including via `uv`.

The host needs mDNS to resolve `.local`: `sudo apt install -y avahi-daemon libnss-mdns`. Without
it the dashboard cannot find the head either, and the head sits showing no state.

If the head is reachable but never re-associates after a reboot, **power-cycle it** — the Wi-Fi is
behind the ESP-Hosted co-processor and a soft reset does not always bring it back.

### Do not use `espota`

`ArduinoOTA`'s listener sits behind the ESP-Hosted co-processor and `begin()` returns void, so
every failure is silent: `espota` reports "no response from device" even with the head pingable,
and no amount of firewall or network configuration on the sending host changes that. The
`tab5_ota` environment is kept in `platformio.ini` only so this note has something to point at.
See the comment above `otaHttpPull()` in `src/main.cpp`.

## Known issues

**The controller reports `Init` while the right bumper is first held**, and stick movement does
nothing during that time. Keep the bumper held until the mode reads `Manual` again; the sticks
respond normally from then on.
