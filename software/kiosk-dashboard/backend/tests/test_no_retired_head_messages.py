"""Nothing on the Thor sends the head a retired message (head-protocol.md §8, tablet plan DT-21).

The ESP-IDF head answers them `ERR unknown`, so a sender that still uses one does nothing at all,
and nothing says so. This scans the Thor's Python for the retired text lines and binary frame ids.
"""
import pathlib
import re

SOFTWARE = pathlib.Path(__file__).resolve().parents[3]

RETIRED_TEXT = re.compile(r"""["'](TURN|LIFT|PMODE|PLCIP) """)
RETIRED_FRAMES = re.compile(r"\b0x10[48]\b")

# Still sent, with the reason and the plan item that removes it. Keep this list short and dated.
ALLOWED = {
    # 0x104 until the Tab5 parses `TRACK thor`, its replacement (tablet plan 2.5, 2026-10-11).
    "kiosk-dashboard/backend/yolo_tracker.py",
    # The conformance suite sends every retired message to check the head refuses it.
    "head-protocol/head_protocol.py",
}


def _senders():
    for path in sorted(SOFTWARE.rglob("*.py")):
        rel = path.relative_to(SOFTWARE).as_posix()
        if "/tests/" in f"/{rel}" or rel in ALLOWED or "/.venv/" in f"/{rel}":
            continue
        yield rel, path.read_text(errors="replace")


def test_no_sender_uses_a_retired_text_line():
    found = [f"{rel}:{n}" for rel, text in _senders()
             for n, line in enumerate(text.splitlines(), 1) if RETIRED_TEXT.search(line)]
    assert not found, f"retired head lines still sent (head-protocol.md §8): {found}"


def test_no_sender_uses_a_retired_frame():
    found = [f"{rel}:{n}" for rel, text in _senders()
             for n, line in enumerate(text.splitlines(), 1) if RETIRED_FRAMES.search(line)]
    assert not found, f"retired head frames still sent (head-protocol.md §8): {found}"


def test_the_allowlist_is_not_stale():
    for rel in ALLOWED:
        assert (SOFTWARE / rel).exists(), f"{rel} is gone: drop it from ALLOWED"
