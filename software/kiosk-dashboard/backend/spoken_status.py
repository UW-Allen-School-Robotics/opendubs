"""What the voice may SAY about the robot: what the dashboard SHOWS, from the same functions, in its own
words, and nothing it can only DO (the user's decision, 2026-10-20). Read-only, loopback only: the voice
service runs on the Thor and asks here; the tablet never does.

    GET /api/status/spoken
    {"ts": <epoch s>,
     "state":   {"name": "SAFE_IDLE", "words": "Ready"},
     "faults":  [{"words": "power cut-off not working", "code": "CONTACTOR_WELDED" | null}],
     "battery": {"percent": 82 | null, "charging": true | false | null},
     "arms":    {"left": "active" | "inactive", "right": ...} | null,
     "show":    {"running": true | false | null, "name": "wave" | null},
     "network": {"uplink": "good" | "weak" | "busy" | "captive" | "down" | "no router" | null},
     "uptime_s": 12345,
     "controller": null}

Rules (agreed with the Voice lane):
  1. A value the dashboard cannot vouch for NOW is null, never the last one known. Each source keeps its
     own staleness bound: the StamPLC link's for the state and battery, the router poller's 15 s for the
     uplink, the show runner answering at all for the show, a recent arm probe for the arms.
  2. No addresses, MACs, SSIDs, device paths or ids, serials, or account or person names, in any field:
     every string here is one of a fixed set of words, except a show's routine id.
  3. Loopback only.
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()

# robot_state's fault names -> the troubleshooting registry's codes (docs/reference/registry/).
FAULT_CODES = {
    "CONTACTOR WELDED": "CONTACTOR_WELDED",
    "PDH brownout": "POWER_PDH_BROWNOUT",
    "PDH hardware": "POWER_PDH_FAULT",
    "OVERTEMP": "POWER_OVERTEMP",
    "lift module not responding": "LIFT_NOT_RESPONDING",
}
UPLINK_WORDS = {"GOOD": "good", "WEAK": "weak", "BUSY": "busy", "CAPTIVE": "captive", "DOWN": "down",
                "NO_ROUTER": "no router"}


def _state(status_mod, rsd: dict) -> tuple[dict, list]:
    import state_out
    import state_words
    shown = state_out.present(rsd)
    soc = None
    try:
        pack = status_mod._pack() or {}
        soc = pack.get("soc") if pack.get("present") else None
    except Exception:
        pass
    # "always": the face hides MANUAL and AUTO by default, but a voice asked "what are you doing?"
    # while being driven must not say it does not know.
    words = state_words.caption(shown, rsd, soc, mode="always") if shown else None
    faults = []
    for f in (rsd.get("inputs") or {}).get("faults") or []:
        if isinstance(f, str):
            w = state_words.FAULTS.get(f, state_words.GENERIC)
            if not any(x["words"] == w for x in faults):
                faults.append({"words": w, "code": FAULT_CODES.get(f)})
    return {"name": shown, "words": words}, faults


def _battery(status_mod) -> dict:
    try:
        pack = status_mod._pack() or {}
    except Exception:
        pack = {}
    if not pack.get("present"):                 # no BMS, or the StamPLC link is stale
        return {"percent": None, "charging": None}
    soc = pack.get("soc")
    pct = int(round(soc)) if isinstance(soc, (int, float)) and 0 <= soc <= 100 else None
    ch = pack.get("charging")
    return {"percent": pct, "charging": bool(ch) if ch is not None else None}


def _arms() -> dict | None:
    # Only an answer the dashboard already has (a show's preflight probe, under 10 s old): reading the
    # arms means talking on their CAN bus, which nothing does just to answer a question.
    try:
        import choreo_proxy
        a = choreo_proxy._probed("arms")
    except Exception:
        a = None
    return {"left": a, "right": a} if a in ("active", "inactive") else None


def _show() -> dict:
    try:
        import choreo_proxy
        code, body = choreo_proxy._call("GET", "palette", None, 1.0)
    except Exception:
        code, body = 0, None
    if code != 200 or not isinstance(body, dict):
        return {"running": None, "name": None}
    rid = body.get("running")
    name = rid if isinstance(rid, str) and 0 < len(rid) <= 60 else None
    return {"running": bool(rid), "name": name}


def _network(status_mod) -> dict:
    try:
        st = (status_mod._router_report() or {}).get("uplink") or {}
    except Exception:
        st = {}
    return {"uplink": UPLINK_WORDS.get(st.get("state"))}        # UNKNOWN or stale -> null


def spoken() -> dict:
    import psutil
    import status as status_mod
    try:
        rsd = status_mod._robot_state() or {}
    except Exception:
        rsd = {}
    state, faults = _state(status_mod, rsd) if rsd else ({"name": None, "words": None}, [])
    return {"ts": round(time.time(), 1), "state": state, "faults": faults,
            "battery": _battery(status_mod), "arms": _arms(), "show": _show(),
            "network": _network(status_mod),
            "uptime_s": int(time.time() - psutil.boot_time()), "controller": None}


@router.get("/api/status/spoken")
async def get_spoken(request: Request):
    if not request.client or request.client.host not in ("127.0.0.1", "::1"):
        return JSONResponse({"error": "loopback only"}, status_code=403)
    import asyncio
    return JSONResponse(await asyncio.get_running_loop().run_in_executor(None, spoken))
