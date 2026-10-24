"""Minimal stateful supervisor for the robot FSM (docs/state-machine-review.md R1).

`robot_state.derive()` is intentionally a pure function of the current telemetry
frame — so on its own it has NO memory and nothing can *latch* (review findings
1, 4; D2, D3). This module is the small stateful layer that holds memory between
frames and feeds `derive()` *latched* inputs:

- **E-stop latch:** a SOFTWARE e-stop latches until an explicit, guarded operator
  re-arm (review R4 / D2) — nothing physical happened, so the clear is the only
  acknowledgement there is. The physical BUTTON does *not* latch: it is a
  twist-to-unlock control that cannot release itself, so the operator unlatching it
  IS the acknowledgement, and demanding a second software ack only teaches people to
  click past safety prompts. Same treatment STALE TELEMETRY already gets.
- **Fault latch (DEGRADED):** faults latch until an explicit operator clear (CLEAR FAULT on
  Operate), and only clear if the underlying condition is absent AND telemetry is ready (review
  R4 / D3). Fatal faults (welded contactor, PDH hardware) are non-clearable from
  the UI — they require physical service.
- **INIT readiness:** boot completes on real ready-checks (StamPLC fresh + RC link
  live), not a wall-clock timer; if checks don't pass within a timeout, emit a
  fault rather than silently declaring ready (review R5 / D6).

Enforcement still lives in hardware + StamPLC — this only governs the *indicator*
+ the software-estop re-arm path, per docs/control-authority.md.
"""
from __future__ import annotations

import logging
import time
from collections import deque

_log = logging.getLogger(__name__)

READY_TIMEOUT_S = 30.0          # no ready-checks within this → TELEMETRY NOT READY fault
FATAL_FAULTS = {"CONTACTOR WELDED", "PDH hardware"}   # non-clearable from the UI
# A fault that reports its own recovery clears itself when its condition goes; every other one latches
# until an operator clears it. The lift answering again is that report (status.py, 2026-10-19).
LIFT_SILENT = "lift module not responding"
SELF_CLEARING = {LIFT_SILENT}
# E-stop sources tracked live rather than latched — see step() for the reasoning.
LIVE_ESTOP_SOURCES = {"STALE TELEMETRY", "BUTTON", "STAMPLC"}


class Supervisor:
    def __init__(self):
        self.estop_latched = False
        self.estop_sources_held: list[str] = []
        self.fault_latched: list[str] = []
        self.raw_faults: list[str] = []    # the last frame's faults: which latched ones are still present
        self.ready = False                 # latches True once ready-checks first pass
        # Audit trail. The live indicator deliberately does not latch BUTTON, so
        # without this there would be no record that an e-stop ever happened.
        # Edges are the right shape for incident review, and unlike a latch they
        # can never disagree with the machine's present state.
        self.events: deque[dict] = deque(maxlen=100)
        self._prev_raw_sources: set[str] = set()

    # ---- operator actions -------------------------------------------------
    def clear_faults(self) -> dict:
        """The operator's DEGRADED clear, guarded against the last frame: a fault clears only if it is
        not fatal and its condition is gone, and nothing clears before telemetry is ready. Returns
        {"cleared": [fault], "kept": [(fault, why)]}, why being "not ready", "needs service" or
        "still present"."""
        if not self.ready:
            return {"cleared": [], "kept": [(f, "not ready") for f in self.fault_latched]}
        kept = [(f, "needs service" if f in FATAL_FAULTS else "still present")
                for f in self.fault_latched if f in FATAL_FAULTS or f in self.raw_faults]
        cleared = [f for f in self.fault_latched if f not in dict(kept)]
        self.fault_latched = [f for f, _ in kept]
        if cleared:
            _log.info("fault cleared by the operator: %s", ", ".join(cleared))
        return {"cleared": cleared, "kept": kept}

    def clear_estop(self):
        """Drop the e-stop latch. Callers MUST check estop_rearm_guard() first."""
        self.estop_latched = False
        self.estop_sources_held = []

    def estop_rearm_guard(self, *, button_pressed, stale, welded, ch5):
        """Guard for software e-stop re-arm (review R4/§5.4). SOFTWARE is being
        cleared by the action itself, so it is NOT a blocker; the *physical* and
        verification conditions are. Returns (ok, [(code, sentence)]); each code names its
        entry in the troubleshooting registry (docs/reference/registry/)."""
        blockers = []
        if button_pressed:
            blockers.append(("ESTOP_BUTTON", "E-STOP BUTTON still pressed"))      # @fault ESTOP_BUTTON
        if stale:
            blockers.append(("LINK_STAMPLC_LOST",                                 # @fault LINK_STAMPLC_LOST
                             "STALE TELEMETRY (safety state unknown)"))
        if welded:
            blockers.append(("CONTACTOR_WELDED", "CONTACTOR WELDED"))             # @fault CONTACTOR_WELDED
        # Positively OFF, or no re-arm. An unknown CH5 (no trustworthy RC frames) fails CLOSED: it
        # used to pass as authority None, and a board-held latch reports mode=ESTOP, which is None
        # too, so a re-arm went through with CH5 in AUTO.
        if ch5 is None:
            blockers.append(("ESTOP_REARM_CH5",                                   # @fault ESTOP_REARM_CH5
                             "CH5 position unknown (no RC frames) — transmitter on, CH5 at OFF"))
        elif ch5 != "OFF":
            blockers.append(("ESTOP_REARM_CH5", "CH5 not OFF"))
        return (not blockers), blockers

    # ---- event log --------------------------------------------------------
    def _record(self, action: str, source: str):
        self.events.append({"t": time.time(), "action": action, "source": source})
        # WARNING so it lands in the journal at a level that survives casual
        # filtering: `journalctl -u opendubs-dash -g E-STOP` is the incident trail.
        _log.warning("E-STOP %s source=%s", action, source)

    def recent_events(self, n: int = 20) -> list[dict]:
        return list(self.events)[-n:]

    # ---- per-frame step ---------------------------------------------------
    def step(self, *, raw_estop_sources, raw_faults, fresh, control_ok, uptime):
        """Fold raw telemetry into latched inputs for derive(). Returns
        {booting, estop_sources, faults}."""
        raw_estop_sources = list(raw_estop_sources or [])
        raw_faults = list(raw_faults or [])
        self.raw_faults = raw_faults

        # Record edges on the RAW sources, before any latching, so the trail reflects
        # what physically happened — including a BUTTON release, which the live
        # indicator now (correctly) acts on but would otherwise leave no trace of.
        now_raw = set(raw_estop_sources)
        for src in sorted(now_raw - self._prev_raw_sources):
            self._record("ASSERT", src)
        for src in sorted(self._prev_raw_sources - now_raw):
            self._record("RELEASE", src)
        self._prev_raw_sources = now_raw

        # E-stop latch, by source.
        #
        # LIVE (tracked, never latched) — each has its own acknowledgement already:
        #   BUTTON          twist-to-unlock; it cannot release itself, so the operator
        #                   unlatching it IS the deliberate ack. Latching it on top
        #                   would leave the display reading E-STOP while the StamPLC
        #                   has already auto-armed and the robot drives — a safety
        #                   indicator that disagrees with the machine is worse than none.
        #   STALE TELEMETRY fail-safe while the link is dead; auto-recovers, else every
        #                   USB blip would demand a manual re-arm (review R2).
        #   STAMPLC         the StamPLC's own latch, which the board holds itself until the
        #                   guarded re-arm (RELAY 0 1) clears it; latching it here too would
        #                   only let the display disagree with the board.
        #
        # LATCHING — SOFTWARE, and anything new that lacks a physical act behind it.
        # There the guarded clear is the only acknowledgement that exists.
        latching = [s for s in raw_estop_sources if s not in LIVE_ESTOP_SOURCES]
        live_now = [s for s in raw_estop_sources if s in LIVE_ESTOP_SOURCES]
        if latching:
            self.estop_latched = True
            self.estop_sources_held = latching
        eff_estop = list(self.estop_sources_held) if self.estop_latched else []
        for s in live_now:
            if s not in eff_estop:
                eff_estop.append(s)

        # Readiness latch (replaces the 4 s wall-clock INIT timer).
        if fresh and control_ok:
            self.ready = True
        booting = (not self.ready) and (uptime < READY_TIMEOUT_S)

        # Fault latch: accumulate; clear only on explicit, guarded operator action (clear_faults()).
        for f in raw_faults:
            if f not in self.fault_latched:
                self.fault_latched.append(f)
        self.fault_latched = [f for f in self.fault_latched if f not in SELF_CLEARING or f in raw_faults]

        eff_faults = list(self.fault_latched)
        if (not self.ready) and (uptime >= READY_TIMEOUT_S):
            if "TELEMETRY NOT READY" not in eff_faults:
                eff_faults.append("TELEMETRY NOT READY")   # condition-driven, not latched

        return {"booting": booting, "estop_sources": eff_estop, "faults": eff_faults}


# Module-level singleton (the FSM has one supervisor; status.py drives it).
supervisor = Supervisor()
