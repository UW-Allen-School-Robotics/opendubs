# AirPlay-compatible receiver (shairport-sync)

The robot can be chosen as an audio destination from an iPhone, iPad or Mac, and plays what it
receives on the face tablet's speaker. The receiver is
[shairport-sync](https://github.com/mikebrady/shairport-sync) (AirPlay 2), running on the Thor in
its official container image. "AirPlay" is Apple's trademark; shairport-sync is an independent,
AirPlay-compatible receiver and is not certified by Apple.

| Part | Where | Lane |
|---|---|---|
| The receiver: image pin, unit, config, provisioning, sudoers rule | this folder; the pin is `SHAIRPORT_IMAGE` in [`../versions.env`](../versions.env) | Thor stack |
| The relay that plays it on the tablet, and the on/off switch | dashboard `backend/airplay_relay.py` and the Head & face page | Tablet and head interface |

## The hand-off (a contract: change both sides together)

| | |
|---|---|
| Audio | FIFO `/run/opendubs/airplay/audio.pcm`: raw PCM, signed 16-bit little-endian, 44 100 Hz, 2 channels interleaved, no header |
| Metadata | FIFO `/run/opendubs/airplay/meta`: shairport-sync's metadata stream (`ssnc pbeg`/`pend`/`pfls` play begin/end/flush; `core minm`/`asar`/`asal` title/artist/album; volume). No cover art |
| Ownership | the folder `root:kiosk 0750`, both FIFOs `root:kiosk 0660`; the folder exists **only while the receiver runs**, so a missing path means "off" |
| Volume | the sender's slider is applied by the receiver in software; the robot's own ceiling is applied on the tablet |
| Latency | the receiver plays 0.3 s early (`audio_backend_latency_offset_in_seconds = -0.3`) to cancel the relay's delay, so a sender's video stays in step |
| Name | the robot's name, `OPENDUBS_ROBOT_NAME` in `/etc/opendubs/robot.env` |
| On/off | the unit's enable state (`systemctl enable --now` / `disable --now opendubs-airplay`), so "on" survives a reboot; kiosk may run only those, `is-active` and `is-enabled` |

**With no reader the receiver does not block.** shairport-sync opens the FIFO non-blocking and,
when nothing has it open for reading, discards the audio and tries again on the next write
(`audio_pipe.c`). So the receiver is safe to run before the relay exists, and while the dashboard
restarts. A reader that opens the FIFO and then *stops reading* would block it, because once open the
write side is blocking: the relay drains it at all times.

## Network

**Senders: iPhone and iPad.** These connect over IPv4, and play. A **Mac does not play** (seen
2026-10-14): it lists its timing peers as IPv6 link-local addresses without an interface scope, and
the timing helper cannot reach them, so no clock locks and nothing is output. The Mac still sees the
receiver and connects. Workplan item AP.6 holds the options.

- The receiver advertises through the Thor's own avahi, which listens on the **wired** robot network
  only. A sender must be on the same network: the robot router's local Wi-Fi, bridged with the wired
  side. It cannot be reached from a site network behind the router's NAT.
- AirPlay 2 timing uses PTP, and its helper (nqptp) needs **exclusive** use of UDP 319 and 320.
  `provision-airplay.sh` refuses to install if something else holds them. If the robot ever needs a
  PTP daemon of its own (PTP-timed Ethernet cameras, for example), the fallback is the image's
  `classic` (AirPlay 1) variant, which needs no PTP.

## Install

```sh
./deploy-all.sh <robot>                                          # ships software/stack to /opt/opendubs/stack
ssh <robot> sudo /opt/opendubs/stack/airplay/provision-airplay.sh --enable
```

## Licences, and the rule that follows from them

- shairport-sync: MIT-style, with BSD-, ISC- and MIT-licensed components (its `LICENSES` file).
- nqptp, built into the same image: **GPL-2.0**, a separate program.
- The image also contains LGPL libraries (avahi, libplist, libconfig, libsoxr, alsa-lib, PulseAudio;
  its FFmpeg is built with only the AAC and ALAC decoders and without GPL parts) and Alpine Linux's
  busybox (**GPL-2.0**).

This repository contains only our own files and a **digest reference** to the image; no GPL or LGPL
code is copied, vendored or linked, and each robot pulls the image from Docker Hub. Running it is not
distribution, so there is no conflict with this project's licences.

**Never bundle the image into anything this project ships**: not into a golden disk image cloned
for other robots, a release or studio bundle, or a registry mirror of our own. Doing so would be
distribution of GPL-2.0 and LGPL code and would oblige the project to supply its corresponding
source. A replicator pulls the image by digest, as the robot does.
