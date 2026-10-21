#!/usr/bin/env python3
"""The FLOOR HAND: a crawling hand on the floor in front of the robot (a show's `crawler:`, its CRAWL
cues), driven live by the runner (notes/design/choreo-floor-hand.md).

    runner.py palette ... --hand /dev/serial/by-id/<the RS-485 adapter>    # this robot has a floor hand
    runner.py palette ... --hand off                                        # it has none (the default)

THE PART is `floor_hand` (cue.EFFECTORS, from the CRAWL slot), so a show that crawls NEEDS it and may list
it in `can_skip:`. At preflight `availability(tl)` says whether it can play this show, in operator words:

  * the show's hand type has a driver (effectors: `output: surge_at`), and this robot has it FITTED at
    `base` (effectors.load_fitted: the robot's own tools file);
  * every crawl move in the show has run on the REAL hand (effectors.hand_untested): a pivot or a gait
    found only in simulation is not played on a person's floor;
  * this runner has a floor-hand port (--hand), and the hand answers on it.

Anything else is `unavailable`, with why; the runner's effector check then refuses the show, or — when it
lists floor_hand in can_skip — offers it without the hand, on the operator's approval.

THE RUN (HandRun) plays the show's CRAWL track as the real hand plays it (effectors.hardware_sequence) on
the show's clock, in its own thread like the projector's video, so the runner's loop never waits on a
serial port: begin() opens the port while the arms travel (nothing moves), go(clock) streams joint targets
at 20 Hz from the show's t0, end(status) sends the OPEN pose and turns feedback back on — on every end,
complete, abort or e-stop (the hand has no watchdog: an unsent stop would hold its last target). A hand
that stops answering mid-show is LOST: logged, left alone, and the show goes on. It is never a safety
function: its power is on the e-stop-switched branch, so an e-stop stops it whatever this does.
"""
from __future__ import annotations

import importlib.util
import os
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PART = "floor_hand"
RATE_HZ = 20.0


def _surge_at():
    spec = importlib.util.spec_from_file_location("surge_at", os.path.join(HERE, "drivers", "surge_at.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FloorHand:
    """This runner's floor-hand output: the port the hand's adapter is on. `driver` makes the hand's
    driver (tests pass a fake); its interface is drivers/surge_at.SurgeAT's."""

    def __init__(self, port: str, log=None, driver=None, fitted=None):
        self.port, self.log = port, log or (lambda *a, **k: None)
        self._mod = None
        self._driver = driver
        self._fitted = fitted                       # {mount: type}; None: the robot's tools file

    def mod(self):
        if self._mod is None:
            self._mod = _surge_at()
        return self._mod

    def driver(self):
        return self._driver() if self._driver else self.mod().SurgeAT(log=lambda *a: None)

    def fitted(self) -> dict:
        if self._fitted is not None:
            return self._fitted
        import effectors
        return effectors.load_fitted()

    def probe(self) -> str | None:
        """None when the hand answers on the port (its joint angles), else why not. Queries only: the hand
        does not move."""
        if not os.path.exists(self.port):
            return f"its adapter is not plugged in ({self.port} is not there)"
        d = self.driver()
        try:
            st = d.open(self.port, startup=False)
        except Exception as e:  # noqa: BLE001
            return f"its port would not open: {type(e).__name__}: {e}"
        try:
            if not any(v is not None for v in (st.get("angles") or {}).values()):
                return "the hand did not answer (is it powered? the e-stop clear?)"
            return None
        finally:
            d.close(home=False)

    def availability(self, tl: dict) -> dict:
        return availability(tl, self)

    def make_run(self, tl: dict, log=None, on_lost=None) -> "HandRun":
        import effectors
        seq = effectors.hardware_sequence(tl, "live run")
        return HandRun(self, self.mod().ShowPlayer(seq), log or self.log, on_lost)


def availability(tl: dict, hand: FloorHand | None) -> dict:
    """{"state": "ok" | "unavailable", "why"} for this show's crawling hand on this robot (see above)."""
    cr = tl.get("crawler")
    if not cr:
        return {"state": "ok", "why": None}
    import effectors
    tool = effectors.registry()[cr["tool"]]
    no = lambda why: {"state": "unavailable", "why": why}  # noqa: E731
    if tool.output is None:
        return no(f"the {tool.name} has no driver yet: it runs only in the twin")
    if hand is None:
        return no("this robot has no floor-hand output (the runner's --hand)")
    try:
        have = hand.fitted().get("base")
    except Exception as e:  # noqa: BLE001
        return no(f"this robot's tools file is unreadable: {e}")
    if have != tool.id:
        return no(f"this robot has {have or 'no hand'} on the floor (its tools file, `base:`), not the {tool.id}")
    untested = effectors.hand_untested(tl)
    if untested:
        return no("the crawl uses moves never run on the real hand: " + "; ".join(untested[:3])
                  + (f" (and {len(untested) - 3} more)" if len(untested) > 3 else ""))
    why = hand.probe()
    return no(why) if why else {"state": "ok", "why": None}


class HandRun:
    """One live run's floor hand (see the module's doc)."""

    def __init__(self, hand: FloorHand, player, log, on_lost=None):
        self.hand, self.player, self.log, self.on_lost = hand, player, log, on_lost
        self.d = None
        self.failed = self.lost = None
        self.done, self.sent = False, 0
        self._clock, self._stop, self._thread, self._end_status = None, threading.Event(), None, None

    def begin(self):
        self._thread = threading.Thread(target=self._loop, daemon=True, name="floor-hand")
        self._thread.start()

    def go(self, show_clock):
        self._clock = show_clock

    def ready(self) -> bool:
        return self.d is not None or bool(self.failed)

    def _lose(self, what, e):
        self.failed = f"{what}: {type(e).__name__}: {e}"
        self.lost = "the floor hand stopped answering"
        self.log("hand_error", error=self.failed)
        if self.on_lost:
            self.on_lost(self.lost)

    def _loop(self):
        try:
            d = self.hand.driver()
            d.open(self.hand.port)                       # its working state: feedback on, glove mode off
            d.start_stream()
            self.d = d
        except Exception as e:  # noqa: BLE001  the hand never stops the show
            self._lose("open", e)
            return
        self.log("hand", note="the floor hand is ready; it moves from the show's start")
        try:
            while not self._stop.is_set():
                if self._clock is not None:
                    tg = self.player.targets(self._clock())
                    if tg:
                        self.sent += d.apply(self._clock(), tg)
                self._stop.wait(1.0 / RATE_HZ)
        except Exception as e:  # noqa: BLE001
            self._lose("stream", e)
        finally:
            self._finish()

    def _finish(self):
        """Every end: the open pose, feedback back on, the port closed."""
        if self.d is None:
            return
        try:
            self.d.close()                               # SurgeAT.close: AT+SPP=0, then AT+SEF=1
            self.log("hand", note=f"the floor hand is open and stopped ({self.sent} commands sent)",
                     end=self._end_status)
        except Exception as e:  # noqa: BLE001
            self.log("hand_error", error=f"stop: {type(e).__name__}: {e}")
        self.d = None

    def end(self, status: str):
        if self.done:
            return
        self.done, self._end_status = True, status
        self._stop.set()                                 # its thread opens the hand and closes the port

    def state(self) -> dict:
        return {"output": "floor_hand", "sent": self.sent, "error": self.failed, "lost": self.lost}
