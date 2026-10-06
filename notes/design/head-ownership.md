# Who owns the head

Decided 2026-10-06, after `head-turn-gaze.md` produced a *third* potential owner of pan/tilt and
nothing reconciled them. **Not built.** Pan/tilt is behind `OPENDUBS_PANTILT`, default `0`, so the
first IDF flash leaves the head inert — see §6.

## 1. The owners, and the verified topology

| owner | path to the head | verified |
|---|---|---|
| **On-face touch** (the HMI's pan/tilt pad) | local, on the Tab5 | exists; currently greyed |
| **RC stick** | **StamPLC → Tab5 DIRECTLY**, UDP 4242, 20 Hz, full stick = 60 °/s, expiring after 600 ms on the head | `rc_serial_probe/src/main.cpp`: `kTab5Port 4242`, `kHeadLocalPort 5007`, `kHeadDps 60.0f`, `kHeadSendMs 50` |
| **Thor YOLO + D415** | Thor → Tab5, CAN-over-UDP (`CAN_ID_PAN_TILT_CMD` / `_RATE`), from the pinned peer | `tab5_face/src/main.cpp:464` |
| **Tab5 local tracker** | on-head perception (esp-dl, ~1.1 m) | new, this branch |

**RC does not go through the Thor.** That was assumed and is wrong: the StamPLC addresses the head
itself. It matters — an RC latch survives a Thor outage, so the hierarchy below is not all hanging
off one box.

## 2. Precedence

    on-face touch  >  RC stick  >  Thor YOLO  >  Tab5 local tracker

**Touch is highest, above RC.** Not an aesthetic call: the utility of the on-face pad is mostly to
aim the *screen* at a reader, and the person touching the glass is physically present while the
stick operator may be across the bay. Nothing should outrank the person who needs to read it.

**The Tab5's tracker is lowest** because that is what its ~1.1 m range suits — a close-range,
always-available fallback and the idle "nobody else wants it" behaviour, not a competitor to the
D415. The two detectors have genuinely different strengths (the Thor's sees further and classifies
better; the Tab5's has no network dependency and keeps working while the Thor reboots), which is
why the answer is precedence rather than picking one.

## 3. Lease vs latch — the part the old firmware lacked

The Arduino firmware had only leases: `rcOwnsHead()` is `millis() - g_rcCmdMs < kManualHoldMs`
(600 ms). That is right for nudging but wrong for commitment — **an authority that expires is not
authority.** So two kinds of ownership:

- **Lease** — a transient input takes the head for a hold window and hands back. This is what makes
  "nudge the head while YOLO keeps tracking" work with nobody changing modes.
- **Latch** — an explicit selection that holds until explicitly released. "RC only" means YOLO never
  takes it back. A latch at any level blocks everything below it indefinitely.

**A latch is released easily, not ceremonially** — from the face and from the dashboard, and *not*
by an idle timeout. A latch that silently expires is the original problem on a longer fuse. A reboot
is not required either; requiring one is over-protective for a device that cannot hurt anyone.

## 4. The head is the authority; the dashboard asks

The latch is shared state, so something has to be the source of truth. **The head.** The dashboard
*requests* a mode and displays what the head reports. The alternative — dashboard as authority —
means a stale page or a reload can silently reassign the head, which is the mode confusion this
design exists to remove.

**The head must report ownership outward, which it has never done.** `CAN_ID_TRACK_MODE` is
**inbound only**: the Thor decides `tracking=0` from its own view of RC and tells the head. The head
says nothing about who owns it, and there is no "nobody owns me" message at all. That one-way flow
is precisely why mode confusion is possible, and closing it is new work rather than a port.

Keeping the Thor informed is still worth it: without it the Thor keeps commanding a suppressed
channel and its own logs claim it is tracking.

## 5. Boot owner: the face

The **Tab5 local tracker** owns the head at boot. The Thor's presence at t=0 is a race, so making it
the default makes boot behaviour nondeterministic. The local tracker is the only owner guaranteed to
exist. `OFF` is a valid mode but not the default — a face that tracks people is the friendly
resting state.

## 6. The authority boundary, and what it changes

**The head cannot realistically damage people or property**, unlike the base and the arms. That is
the reasoned boundary: the safety bar here is lower, and over-gating it is paternalistic rather than
careful.

The consequence, and it is a correction to what was first built: the StamPLC state gate binds
**autonomous owners only**. Do not move on your own without knowing whether the base is stopped —
but never refuse a human standing there pressing a button because a link is down. Slew limits,
hard bounds and boot-centring stay unconditional; they cost nothing and they are what keep
autonomous motion legible.

## 7. Bidirectional flow, and keeping video off the control path

**In, on 4242:** `STATE` (exists), RC rate from the StamPLC, YOLO commands from the Thor, mode and
latch requests. All small and latency-sensitive.

**Out:** the current owner and latch state, head angles, `tracking` so the Thor stops shouting at a
suppressed channel, and the front ToF once it is ported.

**Video gets its own port, over HTTP, and never shares 4242.** MJPEG over HTTP needs no new client
plumbing — an `<img src>` is the whole dashboard side — and it keeps a stream from starving
commands. The hardware JPEG encoder is idle (`tab5-platform-not-slow.md`), so the encode is nearly
free. This is a nice-to-have; it is recorded here so the control channel is not designed in a way
that later has to carry it.

## 8. A hazard found while verifying this, not yet confirmed on hardware

The Arduino head pins `g_thorIp` to the **first** peer that sends a recognised frame *or* a `STATE `
message (`tab5_face/src/main.cpp:490`). The StamPLC also talks to port 4242. So whichever speaks
first becomes "the Thor", and afterwards genuine YOLO commands fail the `from == g_thorIp` check.
If YOLO head tracking has ever been intermittent without explanation, this is where to look. The
IDF build must identify peers by **role**, not by arrival order.
