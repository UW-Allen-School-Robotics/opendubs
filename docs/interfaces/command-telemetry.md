# Command & telemetry interface

*(Draft — the contract every control source speaks, and the safety rules around it.)*

The base is driven by a **single failsafe-gated command contract**: any source produces
drive / actuator commands, they pass the **StampPLC arm + failsafe gate**, and telemetry
streams back. Swapping control sources never changes the robot's safety behavior — only who's
holding the (virtual) stick.

## Control sources — all speak the same contract

| Source | Role | Notes |
|---|---|---|
| **RC** (2.4 GHz) | Independent safety backstop + manual | Deterministic, low-latency, **long range**, own radio link. |
| **On-robot touchscreen** | Local UI / status | A supported variant of the build. |
| **WiFi tablet** (PWA + gamepad) | Primary UX, teleop, richer display | Reuses the web dashboard; removable/handheld. Shares fate with the network. |
| **Autonomy / LeRobot policy** | Programmatic control | Same command topics — teleop, dataset capture, and learned policies all drive here. |

The base is **designed to fit a tablet**, and an **on-robot touchscreen variant** is also
supported — both are just UIs over this one contract.

## Safety rules (non-negotiable)

1. **Every source is heartbeat-gated.** Lose the heartbeat → motion cuts to neutral (the RC
   failsafe and the StampPLC Ethernet heartbeat watchdog are the existing pattern).
2. **The hardware e-stop is independent of every source.** The physical button → GX11 contactor
   kill path must not depend on RC, WiFi, the tablet, or Thor. Never let the thing you drive
   with be the only thing that can stop you.
3. **RC wins on determinism *and* range.** WiFi teleop is convenient but not a substitute for an
   independent, long-range control/e-stop link — so RC stays as the backstop until (if ever) a
   WiFi watchdog is proven equivalent.
4. **The control path is authenticated.** A source must be trusted (token/cert) before it can
   command motion — the same gap flagged for rosbridge/foxglove/dashboard.

## The contract

*TODO:* the actual ROS 2 topics / message types for command + telemetry, the heartbeat rate and
timeout, the arm/deadman semantics, and the auth/pairing scheme. Once specified, RC, the
touchscreen, the tablet PWA, and a policy are all just clients of it.
