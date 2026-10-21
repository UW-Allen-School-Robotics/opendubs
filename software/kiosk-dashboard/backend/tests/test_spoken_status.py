import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import spoken_status
import status


def test_stale_sources_are_null_and_nothing_identifying_leaks(monkeypatch):
    """The voice may say what the dashboard shows and nothing it cannot vouch for: a stale or missing
    source is null, never its last value; and no field carries an address or an id."""
    rsd = {"state": "DEGRADED", "inputs": {"faults": ["CONTACTOR WELDED", "something new"], "charging": False}}
    monkeypatch.setattr(status, "_robot_state", lambda: rsd)
    monkeypatch.setattr(status, "_pack", lambda: {"present": False, "soc": 80, "charging": True})  # stale BMS
    monkeypatch.setattr(status, "_router_report", lambda: {"uplink": {"state": "UNKNOWN", "say": "No network data"},
                                                          "robot_wifi": [{"ip": "10.42.2.13", "name": "tab"}]})
    monkeypatch.setattr(spoken_status, "_show", lambda: {"running": None, "name": None})
    out = spoken_status.spoken()
    assert out["state"]["name"] == "DEGRADED" and out["state"]["words"].startswith("Problem: ")
    assert out["faults"] == [{"words": "power cut-off not working", "code": "CONTACTOR_WELDED"},
                             {"words": "needs a check", "code": None}]
    assert out["battery"] == {"percent": None, "charging": None}
    assert out["network"] == {"uplink": None} and out["arms"] is None
    assert not re.search(r"\d+\.\d+\.\d+\.\d+|[0-9a-f]{2}(:[0-9a-f]{2}){5}|/dev/", json.dumps(out), re.I)
