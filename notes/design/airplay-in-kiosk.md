# AirPlay into the robot's tablet — feasibility (study, nothing built)

**Status: decided 2026-10-14 — no in-app AirPlay.** The maintainers chose not to put the extracted
AirPort Express key in this repository (condition 1 below), so there is no AirPlay receiver in the kiosk
app. Built instead: **Play to robot** (#249), a page at `/play/` that any phone on the robot's network
opens in its browser to send a sound — an upload or a link the robot fetches — which plays from the face
tablet through the show-audio path and always gives way to a show. The study below is kept as the record
of why. Tablet lane.

**The question.** Can someone AirPlay music from a phone or laptop to the robot and hear it from the
tablet, with the receiver built **into our own kiosk app**? Two other routes were ruled out by the
project: a receiver on the Thor relayed to the tablet, and a third-party receiver app on the tablet.
The constraints are that this repository's code is **Apache-2.0**, and the tablet is a device-owner
kiosk in lock task (`tablet-device.md`), unrooted (Galaxy Tab A11, Android 15/16).

## The answer

- **AirPlay 2 inside the app: not feasible.** There are two kinds of AirPlay 2 receiver.
  - The PTP-timed kind (shairport-sync with nqptp) must own UDP ports 319 and 320. An unrooted
    Android app cannot bind ports below 1024.
  - The NTP-timed kind (UxPlay's "legacy" mode, which the one Android AirPlay 2 receiver uses) needs
    Apple's FairPlay handshake. The only open implementation of it is GPL-3.0, and linking it would
    make our APK GPL-3.0.
- **AirPlay 1 (RAOP) inside the app: feasible, with two conditions.**
  - It can be built entirely from permissively licensed sources.
  - **Condition 1:** every RAOP receiver embeds the AirPort Express private RSA key, extracted from
    Apple's hardware in 2011. Nobody licenses it to anyone. It has gone unchallenged in public
    projects and Linux distributions for fifteen years, but putting it in this repository is a
    decision for the maintainers, with advice if they want it.
  - **Condition 2:** Apple still sends to AirPlay-1-only receivers, but nothing promises it will keep
    doing so. iOS 18's first beta broke it; a later beta fixed it. Whether current iOS and macOS
    list such a receiver in the audio picker has to be tested on the phones the team uses.

**Recommendation (at the time of the study; see the decision above).**
1. Decide condition 1 first. If the maintainers won't put the Apple key in the repository, stop here:
   no in-app AirPlay is possible under these constraints.
2. If they will, do a small spike before committing to the feature: advertise `_raop._tcp` from the
   app, accept a stream, and check that a current iPhone and Mac list the robot and play to it.
3. Only then build it properly (scope below).
4. If AirPlay 2, or a fully clean licence, turns out to be a hard requirement, an in-app receiver is
   the wrong approach, and the two rejected routes are the only ones left.

## Findings

Licences were read from each project's LICENSE file and source headers. **UNVERIFIED** marks what
the research could not confirm.

### Receiver cores

| core | licence | protocol | usable in an Apache-2.0 APK? |
|---|---|---|---|
| [shairport-sync](https://github.com/mikebrady/shairport-sync) | mixed. Core files MIT; `tinysvcmdns.c` BSD-3; ALAC (Hammerton) MIT; `pair_ap/` MIT. Its AirPlay 2 build pulls in GPL-2.0 nqptp headers | RAOP, or AirPlay 2 (PTP) | The **classic (RAOP) code, yes**, as source or reference. Not as a direct NDK build: it relies on `pthread_cancel`, which Android's C library lacks, and the maintainer declined an Android port ([#1848](https://github.com/mikebrady/shairport-sync/issues/1848)) |
| [nqptp](https://github.com/mikebrady/nqptp) | GPL-2.0 | AirPlay 2's PTP timing | No (licence), and it needs ports 319/320 |
| [UxPlay](https://github.com/FDH2/UxPlay) | GPL-3.0 overall. RAOP files LGPL-2.1+; crypto GPL-3+; `playfair` (FairPlay) GPL-3.0 | AirPlay 2, NTP-timed only; needs FairPlay | No: the APK would become GPL-3.0 |
| [RPiPlay](https://github.com/FD-/RPiPlay) | GPL-3.0 | as UxPlay; mirroring-focused | No |
| [jqssun/android-airplay-server](https://github.com/jqssun/android-airplay-server) | GPL-3.0 (UxPlay over JNI) | AirPlay 2 (NTP) | No, and it's a separate app anyway |
| [bencall/RPlay](https://github.com/bencall/RPlay) | BSD-3-style | RAOP, Java | **Yes**. Old (2011–13), a starting point |
| [froks/JAirPort](https://github.com/froks/JAirPort) | Apache-2.0 | RAOP, Java | **Yes**. Old (2011) |
| [fgp/AirReceiver](https://github.com/fgp/AirReceiver) | GPL-3.0 | RAOP, Java | No |
| [serezhka/java-airplay-lib](https://github.com/serezhka/java-airplay-lib) | labelled MIT, but contains Java ports of the GPL-3 FairPlay code | AirPlay 2 | **No**: the provenance is unclear (UNVERIFIED legally) |
| [Apple ALAC decoder](https://github.com/macosforge/alac) | Apache-2.0 | the codec RAOP uses | **Yes** |

OpenSSL 3.x is Apache-2.0. The licence-clean set is: shairport-sync's classic code (as reference),
RPlay or JAirPort, and Apple's ALAC.

### The Apple key, and FairPlay
- shairport-sync embeds the AirPort Express private key (`common.c`, `super_secret_key`), and the
  RAOP Java ports do the same. It was extracted from the AirPort Express ROM in 2011
  ([source](https://irq5.io/2011/04/11/apples-raop-is-cracked/)).
- The research found no takedown or legal action in fifteen years of public distribution, including
  Debian and Fedora packages. That absence is not a licence. **UNVERIFIED**; this needs the
  maintainers' decision.
- shairport-sync's AirPlay 2 path avoids that key: it uses HomeKit-style pairing and answers the
  FairPlay setup request with fixed replies.
- UxPlay's NTP-timed AirPlay 2 genuinely needs FairPlay (GPL-3 `playfair`) to decrypt the audio key.

### Ports
- Binding below port 1024 needs root or `CAP_NET_BIND_SERVICE`. Android apps have neither, and being
  device owner grants no Linux capabilities (consistent with the sandbox model; no official document
  says so).
- The nqptp maintainer states that AirPlay 2 senders always use 319/320, and an Android developer
  in the same thread could not bind them without root
  ([nqptp#42](https://github.com/mikebrady/nqptp/issues/42)).
- RAOP uses high ports only, so this doesn't affect it.

### Does Apple still send to AirPlay 1 receivers?
- shairport-sync calls AirPlay 1 "older, but still supported".
- iOS 18 beta 1 broke playback to it, and a later beta fixed it
  ([#1866](https://github.com/mikebrady/shairport-sync/issues/1866)).
- **UNVERIFIED for iOS 26 / macOS 26**: whether the Control Center picker still lists RAOP-only
  receivers. This is the first thing the spike tests.

### Advertising from the app
- Android's `NsdManager` can advertise `_raop._tcp` with its TXT records
  (`NsdServiceInfo.setAttribute`, while holding a `WifiManager.MulticastLock`); the GPL app above
  does exactly that.
- AOSP limits: each key plus value under 255 bytes, the whole TXT record under 1300 bytes, printable
  ASCII keys. A RAOP record fits.
- Samsung-specific quirks: none found (UNVERIFIED).

## What building it would take (RAOP, in the kiosk app)

- **The protocol.** An RTSP server (OPTIONS, ANNOUNCE, SETUP, RECORD, SET_PARAMETER, FLUSH,
  TEARDOWN), the Apple-Challenge signature with the key, AES-CBC decryption of the audio, ALAC
  decoding, NTP-style timing, and a jitter buffer feeding Android's `AudioTrack`. Volume and track
  metadata ride on SET_PARAMETER.
- **Advertising.** `_raop._tcp` through `NsdManager`, with a multicast lock. The name is the robot's
  (`OPENDUBS_ROBOT_NAME`, as the dashboard shows it). The phone must be on the robot's own network:
  the robot is behind NAT, and mDNS doesn't cross to the site Wi-Fi.
- **Sharing the speaker with shows.**
  - A show always wins. When the runner loads a show (the face page already knows, through
    `show_audio.js`), the kiosk app pauses AirPlay and refuses new sessions until the show ends.
  - AirPlay also pauses ambient music, then hands back to it.
  - The robot's volume ceiling (`vol_max`, the Head & face page) applies to AirPlay too.
- **Lock task.** No change. The receiver is a component of our own app, with no UI of its own, and
  its status (playing, from whom) goes on the Head & face page through the JS bridge.
- **Risks.**
  - The Apple key (condition 1).
  - Apple dropping AirPlay 1 (condition 2).
  - Timing quality against the tablet's audio clock. The show-audio work found the audio clock can
    stall when the tablet is handled (`show_audio.js`), so a jitter buffer is needed.
  - Multicast reliability and power on Wi-Fi.

## Sources
The research behind this was done on 2026-10-13. Every claim above links to its source, or is marked
UNVERIFIED.
