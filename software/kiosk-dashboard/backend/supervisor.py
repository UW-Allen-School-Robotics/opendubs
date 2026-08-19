"""Minimal stateful supervisor for the robot FSM (docs/state-machine-review.md R1).

`robot_state.derive()` is intentionally a pure function of the current telemetry
frame — so on its own it has NO memory and nothing can *latch* (review findings
1, 4; D2, D3). This module is the small stateful layer that holds memory between
frames and feeds `derive()` *latched* inputs:

- **E-stop latch:** once any e-stop source asserts, stay latched until an explicit,
  guarded operator re-arm (review R4 / D2). Releasing the button alone does NOT
  drop the latch.
- **Fault latch (DEGRADED):** faults latch until an explicit operator clear, and
  only clear if the underlying condition is absent AND telemetry is ready (review
  R4 / D3). Fatal faults (welded contactor, PDH hardware) are non-clearable from
  the UI — they require physical service.
- **INIT readiness:** boot completes on real ready-checks (StampPLC fresh + RC link
  live), not a wall-clock timer; if checks don't pass within a timeout, emit a
  fault rather than silently declaring ready (review R5 / D6).

Enforcement still lives in hardware + StampPLC — this only governs the *indicator*
+ the software-estop re-arm path, per docs/control-authority.md.
"""
from __future__ import annotations

READY_TIMEOUT_S = 30.0          # no ready-checks within this → TELEMETRY NOT READY fault
FATAL_FAULTS = {"CONTACTOR WELDED", "PDH hardware"}   # non-clearable from the UI


class Supervisor:
    def __init__(self):
        self.estop_latched = False
        self.estop_sources_held: list[str] = []
        self.fault_latched: list[str] = []
        self.ready = False                 # latches True once ready-checks first pass
        self._clear_fault_req = False

    # ---- operator actions -------------------------------------------------
    def request_clear_fault(self):
        """Arm a one-shot DEGRADED clear, consumed (and guarded) on the next step()."""
        self._clear_fault_req = True

    def clear_estop(self):
        """Drop the e-stop latch. Callers MUST check estop_rearm_guard() first."""
        self.estop_latched = False
        self.estop_sources_held = []

    def estop_rearm_guard(self, *, button_pressed, stale, welded, authority):
        """Guard for software e-stop re-arm (review R4/§5.4). SOFTWARE is being
        cleared by the action itself, so it is NOT a blocker; the *physical* and
        verification conditions are. Returns (ok, [blocker strings])."""
        blockers = []
        if button_pressed:
            blockers.append("E-STOP BUTTON still pressed")
        if stale:
            blockers.append("STALE TELEMETRY (safety state unknown)")
        if welded:
            blockers.append("CONTACTOR WELDED")
        if authority not in (None, "OFF"):
            blockers.append("CH5 not OFF")
        return (not blockers), blockers

    # ---- per-frame step ---------------------------------------------------
    def step(self, *, raw_estop_sources, raw_faults, fresh, control_ok, uptime):
        """Fold raw telemetry into latched inputs for derive(). Returns
        {booting, estop_sources, faults, note}."""
        raw_estop_sources = list(raw_estop_sources or [])
        raw_faults = list(raw_faults or [])
        note = None

        # E-stop latch. BUTTON/SOFTWARE are deliberate trips → LATCH (require a
        # guarded re-arm). STALE TELEMETRY is a *live* fail-safe condition: it forces
        # the E-stop display while the link is dead, but auto-recovers when telemetry
        # returns — latching it would force a manual re-arm after every USB blip /
        # reboot (review R2 watchdog semantics, not a physical-button latch).
        latching = [s for s in raw_estop_sources if s != "STALE TELEMETRY"]
        stale_now = "STALE TELEMETRY" in raw_estop_sources
        if latching:
            self.estop_latched = True
            self.estop_sources_held = latching
        eff_estop = list(self.estop_sources_held) if self.estop_latched else []
        if stale_now and "STALE TELEMETRY" not in eff_estop:
            eff_estop.append("STALE TELEMETRY")

        # Readiness latch (replaces the 4 s wall-clock INIT timer).
        if fresh and control_ok:
            self.ready = True
        booting = (not self.ready) and (uptime < READY_TIMEOUT_S)

        # Fault latch: accumulate; clear only on explicit, guarded operator action.
        for f in raw_faults:
            if f not in self.fault_latched:
                self.fault_latched.append(f)
        if self._clear_fault_req:
            self._clear_fault_req = False
            if not self.fault_latched:
                pass
            elif not self.ready:
                note = "fault clear blocked: telemetry not ready"
            else:
                kept = [f for f in self.fault_latched if (f in FATAL_FAULTS or f in raw_faults)]
                if kept:
                    note = "fault still latched: " + ", ".join(kept)
                self.fault_latched = kept

        eff_faults = list(self.fault_latched)
        if (not self.ready) and (uptime >= READY_TIMEOUT_S):
            if "TELEMETRY NOT READY" not in eff_faults:
                eff_faults.append("TELEMETRY NOT READY")   # condition-driven, not latched

        return {"booting": booting, "estop_sources": eff_estop,
                "faults": eff_faults, "note": note}


# Module-level singleton (the FSM has one supervisor; status.py drives it).
supervisor = Supervisor()
