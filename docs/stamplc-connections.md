# StampPLC connections

The **M5Stack StampPLC** is the base controller — it ingests RC, mixes the mecanum drive, runs the
safety I/O (e-stop contactor + weld sense), and is the telemetry hub. This page is the wiring
reference for **assembly and troubleshooting**: which bus lands on which port, and the canonical
relay / isolated-input map. It is the source of truth the dashboard labels and the block diagrams
follow.

## External buses

<figure markdown="span">
  ![StampPLC bus connections — Ethernet, CAN, SBUS, RS485, I²C, power](assets/diagrams/stamplc-buses.png)
  <figcaption>The buses landing on the StampPLC: RC (servo ↔ Grove) on the blue port, I²C (Grove)
  on the red port, plus CAN (XT30), RS485/BMS, PoE Ethernet to the router, and the 24 V logic feed.</figcaption>
</figure>

- **Diagram source (Figma):** [OpenDubs board — StampPLC buses](https://www.figma.com/board/LVbgJwMKO65TARF3m6v7I6/OpenDubs?node-id=10-268) — anyone with the link can view and export it.

Every bus below is on a fixed StampPLC pin (from the firmware `pin_config`). Pins are ESP32-S3
GPIO numbers.

| Bus / signal | Port | Pins | Notes |
|---|---|---|---|
| **RC (SBUS)** | **Blue** port | **G5** (RX), inverted | RC receiver SBUS in. The Grove-to-servo pigtail is an assembly step — see [RC receiver wiring](build/rc-receiver.md). |
| **Ethernet (PoE)** | W5500 module | SPI **SCK G7 / MISO G9 / MOSI G8**, **CS G11** | Shares the LCD/SD SPI bus. Telemetry + commands over **UDP :5005** — the USB-less transport. |
| **CAN** | CAN port | **TX G42 / RX G43**, 1 Mbps | REV PDH sniff / CAN master. |
| **RS485 (BMS)** | RS485 port | **TX G0 / RX G39 / DIR G46**, half-duplex | JBD BMS (Modbus over RS485). |
| **External I²C** | **Port A** (Grove RED) | **SDA G2 / SCL G1** (I²C_NUM_1) | PaHub mux → PRISM, 8Servo, OTOS, Pinpoint, ENV. See the [I²C map](#external-i2c-devices). |
| **Internal I²C** | on-board | **SDA G13 / SCL G15** (I²C_NUM_0) | AW9523 (relays + DIs), LM75B (temp), INA226 (power). |
| **Logic power in** | 24 V terminal | — | From the always-on **logic-24 V** bus (upstream of the e-stop contactor). |

## Relays & isolated inputs

The StampPLC's **4 relay outputs** and **8 opto-isolated digital inputs** are both driven by the
on-board **AW9523** I/O expander on the internal I²C bus. The inputs are galvanically isolated
(INPUT + COM terminals, DC 5–36 V) — reference a signal by landing its return on that input's **COM**
(see the [E-stop system](e-stop.md) page for the DI0 wiring worked example).

<figure markdown="span">
  ![StampPLC relay and input connections — e-stop button and contactor weld on the inputs, RELAY 1 to the e-stop contactor](assets/diagrams/stamplc-inputs-and-relays.png)
  <figcaption>As wired: the e-stop button and contactor-weld sense land on the digital inputs,
  RELAY&nbsp;1 (R0) drives the e-stop contactor, and the Mini PDH feeds the input COMs + the relay
  circuit.</figcaption>
</figure>

- **Diagram source (Figma):** [OpenDubs board — StampPLC relays & inputs](https://www.figma.com/board/LVbgJwMKO65TARF3m6v7I6/OpenDubs) — anyone with the link can view and export it.

!!! note "The board silk is 1-indexed"
    The StampPLC silkscreen labels inputs **IN 1–8** and relays **RELAY 1–4** (1-indexed), while the
    firmware and dashboard use **DI0–7** and **R0–3** (0-indexed). So **IN 1 = DI0** (e-stop button),
    **IN 2 = DI1** (contactor weld), and **RELAY 1 = R0** (e-stop contactor).

### Relay outputs

| Relay | Label | Function |
|---|---|---|
| **R0** | **E-STOP CONTACTOR** | Drives the GIGAVAC GX11 coil. Held closed = drive bus live; opens on e-stop / software `ESTOP` / logic-power loss. See [E-stop system](e-stop.md). |
| **R1** | **8Servo power-cycle** | Cuts + restores the 8Servo 5 V feed (`S8RESET`) — the reliable clear for a latched ESC solid-red fault. |
| **R2** | Spare | — |
| **R3** | Spare | — |

### Isolated inputs

| Input | Label | Function |
|---|---|---|
| **DI0** | **E-STOP BTN** | E-stop button sense. Polarity is the NC fail-safe convention (pressed = circuit open); see [E-stop system](e-stop.md). |
| **DI1** | **CONTACTOR WELD** | GX11 auxiliary contact → welded-contactor detection (commanded open but aux still closed ⇒ welded). |
| **DI2 – DI7** | Spare | Unused. The payload/mast is independent (driven by the Tab5), so there are no mast-limit or battery-present inputs here; drive mode is software-set (no physical jumper). |

### External I²C devices

Behind the PaHub mux on Port A: PRISM RGB (0x38), 8Servo ESC driver (0x25), ENV temp/humidity
(0x44), OTOS (0x17), Pinpoint (0x31). The live set is reported in `[I2C] scan:` telemetry and on the
dashboard.

## See also

- [E-stop system](e-stop.md) — the R0 coil chain, DI0 sensing, and DI1 weld detection in detail.
- [RC receiver wiring](build/rc-receiver.md) — the SBUS blue-port → servo-cable assembly step.
- [Electronics Bay](build/electronics-bay.md) — where the StampPLC physically mounts.
- [Power architecture](power-architecture.md) — the logic vs drive buses this controller sits across.
