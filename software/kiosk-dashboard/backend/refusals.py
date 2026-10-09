"""Refusals the operator can look up.

Every refusal the dashboard returns carries a stable `code` beside its sentence:

    {"ok": false, "code": "LIFT_RELAY_RESERVED", "error": "relays 1 and 2 are the lift legs — ..."}

The code names an entry in the troubleshooting registry (docs/reference/registry/), and the
frontend's `helpLink(code)` opens that entry. `tools/faults.py --check` scans for `refuse("CODE"`
and fails CI if a code is not registered — so build every operator-facing refusal with `refuse()`,
and write the code as a string literal where the call is, or the scan cannot see it.

A malformed request ("use up|down|stop") is a programming error, not something an operator can act
on, and stays a plain 400 without a code.
"""
from __future__ import annotations

from fastapi.responses import JSONResponse


def refuse(code: str, text: str, status: int = 409, **extra) -> JSONResponse:
    """A refusal body: `ok: false`, the registry `code`, the human `error` sentence, and any
    extra fields the caller already returns (`hint`, `blockers`, ...)."""
    return JSONResponse({"ok": False, "code": code, "error": text, **extra}, status_code=status)
