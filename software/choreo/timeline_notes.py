#!/usr/bin/env python3
"""Timeline notes: what the person composing a show asks the next editor — often an agent — to do.

    python3 timeline_notes.py list   SHOW [--all]     # the open notes, each with the cues it covers
    python3 timeline_notes.py prompt SHOW             # the whole request, ready to paste into any agent
    python3 timeline_notes.py snapshot SHOW --by NAME # keep the show as it is now, before editing it
    python3 timeline_notes.py reply  SHOW ID done|declined "one line" [--by NAME]
    python3 timeline_notes.py history SHOW            # the snapshots kept for this show

A note lives in the show file (format: `timeline_notes:`) — anchored to a time range (`at`, `to`:
"bar.beat" strings or seconds) and optionally to slots, with free text, a status (open, done,
declined) and the editor's one-line reply. Notes never move the robot: the runner never reads them,
and the validator checks only their shape (cue.py).

THE LOOP (composer plan #5). The person writes notes in the composer. An agent — asked "apply the
notes in <show>", or handed the text `prompt` prints — snapshots the show, edits the OTHER parts of
the show to satisfy each open note, validates it, and marks each note done with a one-line reply, or
declined with the reason. The composer notices the file changed on disk, shows the difference, and
can restore any snapshot: every agent edit is reviewable and undoable.

HISTORY lives beside the shows, never in the repository: <shows dir>/.history/<show>/<time>-<by>.yaml.
"""
from __future__ import annotations

import argparse
import difflib
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(HERE, "composer")]

HISTORY = ".history"
KEEP = 50                       # snapshots kept per show; older ones are removed


# ---- history: snapshots beside the shows ------------------------------------------------------

def history_dir(show_path: str) -> str:
    d, name = os.path.split(os.path.abspath(show_path))
    return os.path.join(d, HISTORY, name)


def snapshot(show_path: str, by: str, text: str | None = None) -> str | None:
    """Keep the show's text (by default, as it is on disk now) as a snapshot. Returns its name, or
    None when it is identical to the newest snapshot (nothing new to keep)."""
    if text is None:
        if not os.path.isfile(show_path):
            return None
        with open(show_path, encoding="utf-8") as fh:
            text = fh.read()
    d = history_dir(show_path)
    os.makedirs(d, exist_ok=True)
    snaps = list_snapshots(show_path)
    if snaps:
        with open(os.path.join(d, snaps[0]["name"]), encoding="utf-8") as fh:
            if fh.read() == text:
                return None
    by = re.sub(r"[^a-z0-9_-]+", "-", (by or "someone").lower()).strip("-") or "someone"
    name = f"{time.strftime('%Y%m%d-%H%M%S')}-{int(time.time() * 1000) % 1000:03d}-{by}.yaml"
    with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
        fh.write(text)
    for old in list_snapshots(show_path)[KEEP:]:
        os.remove(os.path.join(d, old["name"]))
    return name


def list_snapshots(show_path: str) -> list:
    """Newest first: {name, when, by}."""
    d = history_dir(show_path)
    if not os.path.isdir(d):
        return []
    out = []
    for f in sorted(os.listdir(d), reverse=True):
        m = re.fullmatch(r"(\d{8})-(\d{6})-\d{3}-(.+)\.yaml", f)
        if m:
            ds, ts = m.group(1), m.group(2)
            out.append({"name": f, "when": f"{ds[:4]}-{ds[4:6]}-{ds[6:]} {ts[:2]}:{ts[2:4]}:{ts[4:]}",
                        "by": m.group(3)})
    return out


def read_snapshot(show_path: str, name: str) -> str:
    if not re.fullmatch(r"[\w.-]+\.yaml", name or "") or name.startswith("."):
        raise ValueError("not a snapshot name")
    with open(os.path.join(history_dir(show_path), name), encoding="utf-8") as fh:
        return fh.read()


def diff(a: str, b: str, a_name="before", b_name="after") -> str:
    return "".join(difflib.unified_diff(a.splitlines(True), b.splitlines(True), a_name, b_name, n=2))


# ---- the notes, with the cues each covers -------------------------------------------------------

def _analysis(show_path: str, text: str | None = None):
    import cue
    import yaml
    text = text if text is not None else open(show_path, encoding="utf-8").read()
    show = cue.show_from_text(text, os.path.abspath(show_path))
    limits_path = (cue.DEFAULT_LIMITS if os.path.isfile(cue.DEFAULT_LIMITS)
                   else os.path.join(HERE, "tests", "joint_limits_fixture.yaml"))
    tl = show.export(cue.load_limits(limits_path), limits_path, with_notes=True)
    return yaml.safe_load(text), tl


def notes_view(show_path: str, include_all=False, text: str | None = None) -> list:
    """Each note (open only, unless include_all) with the authored cues inside its range, on its
    slots (or every slot): [{note…, cues: [{index, t, slot, cue}]}]."""
    doc, tl = _analysis(show_path, text)
    out = []
    for n in tl.get("timeline_notes", []):
        if not include_all and n["status"] != "open":
            continue
        lo, hi = (n["t0"] or 0.0) - 1e-6, (n["t1"] if n["t1"] is not None else n["t0"] or 0.0) + 1e-6
        near = []
        for slot, evs in tl["tracks"].items():
            if n["slots"] and slot not in n["slots"]:
                continue
            for e in evs:
                end = e.get("end", e["t"])
                if e["t"] <= hi and end >= lo:
                    c = {k: v for k, v in (e.get("cue") or {}).items() if k not in ("solved", "solved_for")}
                    near.append({"index": (e.get("src") or {}).get("index"), "t": round(e["t"], 3),
                                 "slot": slot, "cue": c})
        out.append(dict(n, cues=sorted(near, key=lambda c: (c["t"], c["slot"]))))
    return out


def _when(n) -> str:
    at = f'bar {n["at"]}' if isinstance(n["at"], str) else f'{n["at"]} s'
    to = "" if n.get("to") is None else (f' to bar {n["to"]}' if isinstance(n["to"], str) else f' to {n["to"]} s')
    secs = "" if n["t0"] is None else f' ({n["t0"]:.2f}–{n["t1"]:.2f} s)' if n["t1"] != n["t0"] else f' ({n["t0"]:.2f} s)'
    return at + to + secs


def prompt(show_path: str, text: str | None = None) -> str:
    """The whole request for an agent: where the show is, the open notes with what they cover, and the
    rules. The composer's "copy notes as a prompt" button copies exactly this."""
    view = notes_view(show_path, text=text)
    p = os.path.abspath(show_path)
    lines = [f"Apply the open timeline notes in the OpenDubs show {p}.", ""]
    if not view:
        lines.append("(There are no open notes in this show.)")
    for n in view:
        on = f" on {', '.join(n['slots'])}" if n["slots"] else ""
        lines.append(f"- note {n['id']}, {_when(n)}{on}: {n['text']}")
        for c in n["cues"][:12]:
            lines.append(f"    cue {c['index']} at {c['t']} s, {c['slot']}: {c['cue']}")
        if len(n["cues"]) > 12:
            lines.append(f"    … and {len(n['cues']) - 12} more cues in range")
    lines += ["", "How (software/choreo in the OpenDubs repository):",
              f"1. Snapshot first: python3 software/choreo/timeline_notes.py snapshot {p} --by agent",
              "2. Edit the show's cues (and slots/clips if needed) to satisfy each note. Do not edit other",
              "   notes' text. Keep the file's layout: change only the lines you must.",
              f"3. Validate: python3 software/choreo/cue.py validate {p} — it must stay valid; for reach/point_at",
              f"   targets run python3 software/choreo/ik.py compile {p} first.",
              f"4. For each note: python3 software/choreo/timeline_notes.py reply {p} <id> done \"what you did, one line\"",
              "   or ... reply <id> declined \"why\" when it cannot be done safely or is unclear.",
              "5. Do not commit the show: show files live outside the repository.",
              "The person reviews the result in the composer, which shows the change and can restore the snapshot."]
    return "\n".join(lines)


def reply(show_path: str, note_id: str, status: str, text: str, by: str = "agent") -> None:
    """Mark one note done or declined with a one-line reply — a minimal edit of the show file."""
    import yaml
    import yamledit
    if status not in ("done", "declined", "open"):
        raise ValueError("status is done, declined or open")
    with open(show_path, encoding="utf-8") as fh:
        src = fh.read()
    notes = yaml.safe_load(src).get("timeline_notes") or []
    n = next((x for x in notes if isinstance(x, dict) and x.get("id") == note_id), None)
    if n is None:
        raise ValueError(f"no timeline note {note_id!r} in {show_path}")
    new = dict(n, status=status, reply=" ".join(str(text).split()), by=by)
    out = yamledit.apply(src, [{"op": "note_set", "id": note_id, "note": new}])
    tmp = show_path + ".notes-tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(out)
    os.replace(tmp, show_path)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("list", "prompt", "snapshot", "history"):
        s = sub.add_parser(name)
        s.add_argument("show")
        if name == "list":
            s.add_argument("--all", action="store_true")
        if name == "snapshot":
            s.add_argument("--by", default="agent")
    r = sub.add_parser("reply")
    r.add_argument("show")
    r.add_argument("id")
    r.add_argument("status", choices=("done", "declined", "open"))
    r.add_argument("text")
    r.add_argument("--by", default="agent")
    a = ap.parse_args()
    try:
        if a.cmd == "list":
            for n in notes_view(a.show, a.all):
                print(f"{n['id']:6s} {n['status']:8s} {_when(n)}{' on ' + ', '.join(n['slots']) if n['slots'] else ''}")
                print(f"       {n['text']}")
                if n.get("reply"):
                    print(f"       ↳ {n['reply']}")
                for c in n["cues"]:
                    print(f"       cue {c['index']:>3} {c['t']:7.2f} s {c['slot']:6s} {c['cue']}")
        elif a.cmd == "prompt":
            print(prompt(a.show))
        elif a.cmd == "snapshot":
            name = snapshot(a.show, a.by)
            print(f"kept {name}" if name else "unchanged since the last snapshot — nothing new kept")
        elif a.cmd == "history":
            for s in list_snapshots(a.show):
                print(f"{s['when']}  {s['by']:12s} {s['name']}")
        else:
            reply(a.show, a.id, a.status, a.text, a.by)
            print(f"{a.id}: {a.status}")
    except (ValueError, OSError) as e:
        print(f"refused: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
