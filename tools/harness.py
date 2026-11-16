#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = ["pyyaml"]
# ///
"""Wiring harnesses: one file per assembly says every cable, wire by wire; this checks it and draws it.

The source is hardware/harness/<assembly>.yaml: the cables a person makes or buys to join one
assembly's parts. Everything a reader sees is a VIEW of it: the wiring cards on a docs page
(generated between markers, the way a BOM table is), each cable's drawing, and a standalone page.

    <!-- WIRING-AUTO:<assembly> -->
    ...generated: a drawing per cable, its pin table, its notes and parts...
    <!-- /WIRING-AUTO:<assembly> -->

Each drawing is written to docs/assets/wiring/<assembly>-<cable>.svg, so `mkdocs build --strict`
fails on a page that names a drawing that is not there.

Usage (from the repository root, or with --root):
  harness.py                 validate, then list the assemblies and their cables
  harness.py --inject        regenerate every WIRING-AUTO block and drawing (writes)
  harness.py --check         validate + verify the pages and drawings are current; exit 1 if not
  harness.py --photos        the photos each assembly has a place for and does not have yet: the
                             file name to save each one as, and what to photograph
  harness.py --html DIR      one standalone page per assembly, draft ones included: the cable
                             cards, the jumper settings, and the connectors no cable uses yet

Needs PyYAML, which arrives with MkDocs. Anywhere else, `uv run harness.py` installs it.

Schema opendubs.harness/0: hardware/harness/<assembly>.yaml
  schema: opendubs.harness/0
  assembly: <id>          the marker key and the drawings' filename prefix; = the file's name
  title: <text>           optional: the page's heading
  product: <text>         optional: what is being built ("OpenDubs"). The page then says
                          "OpenDubs keeps it" and "OPENDUBS USES THIS", not "this build"
  status: robot | draft   robot: on the robot, injected into `page`. draft: checked and drawn by
                          --html only, never injected, and its parts may be missing from parts.csv
                          (a board that is not yet fabricated and validated stays out of parts.csv)
  page: docs/...md        the page that carries the WIRING-AUTO block (required for robot)
  connectors:             every end a wire lands on
    <conn-id>:
      board: <board>      pins come from hardware/harness/boards/<board>.pins.json ...
      ref: <ref>          ... connector <ref> there (its name, part and pins)
    <conn-id>:            ... or an end described here:
      name: <text>
      kind: <text>              optional: what the mating end is ("Grove plug", "female housing")
      part: <parts.csv id>      optional; the card shows its name and buy link from parts.csv
      bare: true                optional; loose wire ends (a supply, your load), drawn dashed
      numbered: false           optional; pin positions are not established, so none are drawn
      end: <text>               optional: what this end of the cable is attached to ("RC receiver"),
                                printed over the connector as "RC RECEIVER END". A board connector
                                takes its board's `label` unless this says otherwise
      pins: {<pin-id>: <label>} in the order they are drawn
  settings:               optional: jumpers and solder bridges this assembly needs set
    - {board: <board>, ref: <ref>, set: <text>, means: <text>}
                          `means` is why THIS assembly needs it. When the board file describes the
                          jumper's positions (below), `set` must be one of them, and the setting is
                          drawn: every position, the one the board ships in, the one to use
  cables:
    - id: <id>
      title: <text>
      what: <text>              one sentence: what the cable does
      length_mm: <n>            the cable's length. Optional in a draft; a cable on the robot without
                                one is a warning, and the page lists the ones still to measure
      length_note: <text>       optional: where the length comes from ("estimate: ...", "the lead's
                                stocked length"), or how to find it when there is no number yet
      build: <text>             optional: how it is made (bought, crimped, soldered)
      splice: <text>            optional: what joins the wires mid-cable, drawn as a block
      checked: <text>           optional: the date and how this cable was confirmed on hardware
      spec: {<label>: <text>}   optional: what someone planning around this connection needs to
                                know (a bus's standard, speed, termination), listed on the card
      spec_title: <text>        optional: the heading over `spec` (default "Details")
      wires:
        - from: <conn-id>.<pin-id>
          to: <conn-id>.<pin-id> | null     null: the wire is cut back and insulated
          signal: <text>
          colour: <name>        one of COLOURS below
          awg: <n>              optional
          pair: <name>          optional: the two wires sharing a name are twisted together
          spare: true           optional: the wire is in the cable and carries nothing (a bought
                                4-wire cable on a 3-signal port); drawn dashed, and landing on a
                                pin the board connects to nothing is then not a warning
      notes: [<text>, ...]
      parts: [{id: <parts.csv id>, qty: <text>}]   what to buy for the cable itself
      photos: [{file: <path in this repository>, caption: <text>}]   photos that already exist
                                elsewhere in the guide and show this connection; shown in the
                                card's gallery beside the cable's own photos (see Photos below)
  bundles:                PROVISIONAL: a first way to show cables that run together. The format
    - id: <id>            will change when routes come from CAD or a routing tool.
      title: <text>
      what: <text>
      start: <text>               where the trunk begins ("Hat, J5 to J8")
      example: true               the lengths are placeholders; the drawing says so
      branches:                   each cable leaves the trunk `at_mm` from the start and runs
        - {cable: <cable id>, at_mm: <n>, tail_mm: <n>}     `tail_mm` more to its far end

Photos: docs/assets/wiring/photos/<name>.jpg (or .jpeg, .png, .webp), taken by whoever builds
the cables. There is nothing to add to the harness file: a photo saved under the right name is
shown on its card the next time --inject runs, and --check then holds the page to it.
  <assembly>-<cable>              the finished cable, laid flat, both ends in view, a ruler beside it
  <assembly>-<cable>-<conn-id>    that end plugged in, close enough to see which connector and which way round
  <assembly>-set-<ref>            the jumper or solder bridge as it should look
--photos lists the ones still missing, in the form of the repository's photos.manifest
(docs/assets/build/README.md: convert from HEIC, about 1600 px on the long edge, under 300 KB).

Parts that are not in the BOM yet: hardware/harness/pending-parts.csv, the same columns as
hardware/parts.csv. A DRAFT harness may name a part from it, and the card shows its name and buy
link marked "not in the robot BOM yet". A harness on the robot may not: move the row to parts.csv
first (an id in both files is an error). This is where a board's cable parts wait while the board
itself is not in the BOM. tools/bom.py never reads this file, so nothing in it reaches the Build
or Purchase BOM; only this tool reads it, and only to put a name and a link on a draft card.

Board pin files: hardware/harness/boards/<board>.pins.json
  {"board": <id>, "rev": <text>, "source": "<repo>@<commit>:<path>" or "<path in this repo>",
   "refs_on_board": <bool>, "label": <text>,
   "connectors": {<ref>: {"name": <text>, "part": <parts.csv id>, "numbered": <bool>,
                          "pins": {<pin-id>: <label> | null}}},
   "jumpers": {<ref>: {"name": <text>, "pins": {...}}},
   "verify": [{"file": <path in this repo>, "pattern": <literal text>, "why": <text>}]}
  Generated by, or copied from, the board's own source (a PCB built with the pcb-from-source
  skill exports it from its netlist). A null label is a pin the board connects to nothing.
  Optional per connector: "pin_names" {<pin-id>: <text>} (what a builder calls the pin, shown in
  place of the label), "kind", "mpn", "footprint", "note" (a trap a person wiring to it must
  know; printed on every card that uses the connector), "type" (what the connector on the board
  is, in words: "JST XH connector, 3-pin"), and "box": [x0, y0, x1, y1], where it sits, in mm from
  the board's top-left corner ("back": true when it is on the underside). With a top-level
  "outline": [width, height] in mm, each card gets a picture of the board with the connectors its
  cable uses marked. "landmarks": {<ref>: {"name", "box"}} are drawn faintly, to orient the reader. `label` is the
  board's name in a sentence ("Hat"): a cable's end on it is captioned "HAT END".
  What a person sets or presses on the board, so it can be shown and not only told:
    a jumper may add "title", "type", "selects", "how", "ships" (the position it arrives in),
      "link" ("wire" or "cap"), "pad_names" {<pad>: <short label>}, "pad_at" {<pad>: [x, y]} and
      "positions" {<name>: {"join": [<pad>, <pad>] or [], "means": <text>}}
    "buttons": {<ref>: {"name", "does", "box"}}
    "left_off": [{"refs": [...], "what", "leave", "fit", "boxes": [...]}]  parts the board is
      assembled without on purpose; fitting one is a setting made with a soldering iron `verify` lines tie a pin to this
  repository's firmware: --check fails when the text is gone, so a moved pin cannot leave stale
  wiring. `refs_on_board: true` says the refs are printed on the board (J5 on a PCB's silkscreen),
  so a reader can find them: the drawing and tables then show "J5 · <name>". Without it a ref is
  this file's own key and is never shown; the connector goes by its name alone.

What --check enforces: the schema; every wire end names a declared connector and one of its
pins; no pin is used twice in one cable; a twisted pair is two wires; every part id is in
parts.csv (robot status); every colour is known; every setting names a jumper its board has;
every verify line still matches; every robot assembly's page has its block, and the block and
drawings are current. A board pin whose label changes makes the block stale, which is how a
renamed net is seen. A wire landing on a pin the board connects to nothing is a warning.

The drawing follows the 2CAN configurator's cable cards (arm-can-node repository,
tools/configurator/cables.js cableSvg() and app.css at commit 6d73450; that repository has no
remote, so the drawing is ported here, not imported).
"""
import argparse, base64, csv, html, json, math, os, re, sys

try:
    import yaml
except ImportError:
    sys.exit("harness.py needs PyYAML (it comes with MkDocs). Install it, or run: uv run harness.py")

SCHEMA = "opendubs.harness/0"

COLOURS = {"red": "#d23b2f", "black": "#2a2a2a", "white": "#f4f4f4", "brown": "#7a4a24",
           "green": "#2e8b45", "yellow": "#e2bd1f", "grey": "#8f8f8f", "pink": "#e58fb5",
           "blue": "#2f6fd1", "orange": "#ec8a2c", "violet": "#7d4cc2"}

TOP_KEYS = {"schema", "assembly", "status", "page", "connectors", "settings", "cables", "title", "product", "bundles"}
CONN_KEYS = {"board", "ref", "name", "kind", "part", "bare", "numbered", "pins", "note", "end"}
CABLE_KEYS = {"id", "title", "what", "length_mm", "length_note", "build", "splice", "checked", "wires", "notes", "parts", "spec", "spec_title", "photos"}
WIRE_KEYS = {"from", "to", "signal", "colour", "awg", "pair", "spare"}
SETTING_KEYS = {"board", "ref", "set", "means"}
ALL_PINS_UP_TO = 10          # a larger connector is drawn with only the pins the cable uses

MARKER = re.compile(r"(<!-- WIRING-AUTO:(?P<key>[a-z0-9-]+) -->\n)(?P<body>.*?)(<!-- /WIRING-AUTO:(?P=key) -->)", re.S)


class Repo:
    def __init__(self, root):
        self.root = root
        self.harness = os.path.join(root, "hardware", "harness")
        self.boards = os.path.join(self.harness, "boards")
        self.parts_csv = os.path.join(root, "hardware", "parts.csv")
        self.pending_csv = os.path.join(self.harness, "pending-parts.csv")
        self.svg = os.path.join(root, "docs", "assets", "wiring")
        self.photos = os.path.join(self.svg, "photos")


def load_parts(repo):
    if not os.path.exists(repo.parts_csv):
        return {}
    with open(repo.parts_csv, newline="", encoding="utf-8") as f:
        return {r["id"].strip(): r for r in csv.DictReader(f)}


def load_pending(repo, parts, errors):
    """Parts a draft harness may name before they are in the BOM (see the header)."""
    if not os.path.exists(repo.pending_csv):
        return {}
    with open(repo.pending_csv, newline="", encoding="utf-8") as f:
        rows = {r["id"].strip(): dict(r, _pending=True) for r in csv.DictReader(f)}
    for pid in sorted(set(rows) & set(parts)):
        errors.append(f"pending-parts.csv: {pid} is in hardware/parts.csv too; delete it from pending-parts.csv")
    return rows


def load_boards(repo, errors):
    boards = {}
    if not os.path.isdir(repo.boards):
        return boards
    for fn in sorted(os.listdir(repo.boards)):
        if not fn.endswith(".pins.json"):
            continue
        with open(os.path.join(repo.boards, fn), encoding="utf-8") as f:
            b = json.load(f)
        name = fn[: -len(".pins.json")]
        if b.get("board") != name:
            errors.append(f"boards/{fn}: board is {b.get('board')!r}, the file name says {name!r}")
        for v in b.get("verify", []):
            path = os.path.join(repo.root, v["file"])
            if not os.path.exists(path):
                errors.append(f"boards/{fn}: verify file {v['file']} is missing")
            elif v["pattern"] not in open(path, errors="replace").read():
                errors.append(f"boards/{fn}: {v['file']} no longer contains {v['pattern']!r} "
                              f"({v.get('why', 'a pin this harness relies on')}): update the pins file and the harness")
        boards[name] = b
    return boards


def resolve(h, boards, errors):
    """Turn each connector into {name, kind, part, bare, numbered, pins, nc, note} or record why not."""
    out = {}
    for cid, c in (h.get("connectors") or {}).items():
        where = f"{h.get('assembly')}: connector {cid}"
        for k in set(c) - CONN_KEYS:
            errors.append(f"{where}: unknown key {k!r}")
        src = tag = None
        end_ = c.get("end")
        if "board" in c:
            b = boards.get(c["board"])
            if not b:
                errors.append(f"{where}: no hardware/harness/boards/{c['board']}.pins.json")
                continue
            bc = b["connectors"].get(c.get("ref"))
            if not bc:
                errors.append(f"{where}: board {c['board']} has no connector {c.get('ref')!r}")
                continue
            if "pins" in c:
                errors.append(f"{where}: a board connector takes its pins from the board file; drop `pins`")
            src = (c["board"], c["ref"])
            c = {**bc, **{k: v for k, v in c.items() if k not in ("board", "ref")}}
            c.setdefault("name", src[1])
            end_ = end_ or b.get("label")
            if b.get("refs_on_board"):
                tag = src[1]
                if tag not in str(c["name"]):
                    c["name"] = f"{tag} · {c['name']}"
        if not c.get("pins"):
            errors.append(f"{where}: no pins")
            continue
        pins = {str(k): v for k, v in c["pins"].items()}
        plain = {str(k): v for k, v in (c.get("pin_names") or {}).items()} if src else {}
        out[cid] = {"name": str(c.get("name", cid)), "kind": c.get("kind"), "part": c.get("part"),
                    "bare": bool(c.get("bare")), "numbered": c.get("numbered", True), "src": src, "tag": tag, "end": end_,
                    "type": c.get("type") if src else None, "box": c.get("box") if src else None, "back": bool(src and c.get("back")),
                    "note": c.get("note"), "nc": {k for k, v in pins.items() if v is None},
                    "pins": {k: ("no connection" if v is None else str(plain.get(k, v))) for k, v in pins.items()}}
    return out


def end(ref, conns, where, errors):
    cid, _, pin = str(ref).partition(".")
    if cid not in conns:
        errors.append(f"{where}: {ref!r} names no declared connector")
        return None
    if pin not in conns[cid]["pins"]:
        errors.append(f"{where}: connector {cid} has no pin {pin!r} (has {', '.join(conns[cid]['pins'])})")
        return None
    return cid, pin


def validate(h, fn, boards, parts, errors, warnings, pending=None, root=None):
    where = os.path.basename(fn)
    for k in set(h) - TOP_KEYS:
        errors.append(f"{where}: unknown key {k!r}")
    if h.get("schema") != SCHEMA:
        errors.append(f"{where}: schema must be {SCHEMA}")
    if h.get("assembly") != where[: -len(".yaml")]:
        errors.append(f"{where}: assembly is {h.get('assembly')!r}, the file name says {where[:-5]!r}")
    if h.get("status") not in ("robot", "draft"):
        errors.append(f"{where}: status must be robot or draft")
    robot = h.get("status") == "robot"
    if robot and not h.get("page"):
        errors.append(f"{where}: a robot harness needs `page`")
    conns = resolve(h, boards, errors)

    def part_ok(pid, w):
        if pid and robot and pid not in parts:
            errors.append(f"{w}: part {pid!r} is not in hardware/parts.csv")
        elif pid and pid not in parts and pid not in (pending or {}):
            warnings.append(f"{w}: part {pid!r} is in neither hardware/parts.csv nor pending-parts.csv, so the card has no buy link")

    for cid, c in conns.items():
        part_ok(c["part"], f"{where}: connector {cid}")
    for s in h.get("settings") or []:
        w = f"{where}: setting {s.get('ref')}"
        for k in set(s) - SETTING_KEYS:
            errors.append(f"{w}: unknown key {k!r}")
        b = boards.get(s.get("board"))
        if not b or s.get("ref") not in b.get("jumpers", {}):
            errors.append(f"{w}: board {s.get('board')!r} has no jumper {s.get('ref')!r}")
        if not s.get("set") or not s.get("means"):
            errors.append(f"{w}: needs `set` and `means`")
        choices = ((b or {}).get("jumpers", {}).get(s.get("ref")) or {}).get("positions")
        if choices and s.get("set") not in choices:
            errors.append(f"{w}: {s.get('set')!r} is not a position of {s.get('ref')} (it has {', '.join(choices)})")
    seen_ids = set()
    for cab in h.get("cables") or []:
        w = f"{where}: cable {cab.get('id')}"
        for k in set(cab) - CABLE_KEYS:
            errors.append(f"{w}: unknown key {k!r}")
        for k in ("id", "title", "what", "wires"):
            if not cab.get(k):
                errors.append(f"{w}: missing {k}")
        if cab.get("id") in seen_ids:
            errors.append(f"{w}: duplicate cable id")
        seen_ids.add(cab.get("id"))
        used, pairs = set(), {}
        cab["_wires"] = []
        for i, wire in enumerate(cab.get("wires") or []):
            ww = f"{w} wire {i + 1}"
            for k in set(wire) - WIRE_KEYS:
                errors.append(f"{ww}: unknown key {k!r}")
            if wire.get("colour") not in COLOURS:
                errors.append(f"{ww}: colour {wire.get('colour')!r} is not one of {', '.join(COLOURS)}")
            if not wire.get("signal"):
                errors.append(f"{ww}: missing signal")
            a = end(wire.get("from"), conns, ww, errors)
            b = end(wire["to"], conns, ww, errors) if wire.get("to") is not None else None
            for e_ in (a, b):
                if e_ and e_ in used:
                    errors.append(f"{ww}: {e_[0]}.{e_[1]} is already used in this cable")
                if e_:
                    used.add(e_)
                    if e_[1] in conns[e_[0]]["nc"] and not wire.get("spare"):
                        warnings.append(f"{ww}: {e_[0]}.{e_[1]} is a pin {conns[e_[0]]['name']} connects to nothing")
            if wire.get("pair"):
                pairs.setdefault(wire["pair"], []).append(i)
            cab["_wires"].append((a, b, wire))
        for name, idx in pairs.items():
            if len(idx) != 2:
                errors.append(f"{w}: pair {name!r} has {len(idx)} wire(s); a twisted pair is two")
        if robot and not cab.get("length_mm"):
            warnings.append(f"{w}: no length_mm; a builder cannot cut or order it")
        for ph in cab.get("photos") or []:
            if not isinstance(ph, dict) or set(ph) != {"file", "caption"}:
                errors.append(f"{w}: a photo is {{file, caption}}; got {ph!r}")
            elif root and not os.path.exists(os.path.join(root, ph["file"])):
                errors.append(f"{w}: photo {ph['file']} is not in the repository")
        if not isinstance(cab.get("spec") or {}, dict):
            errors.append(f"{w}: spec is a mapping of label to text")
        for p in cab.get("parts") or []:
            if not isinstance(p, dict) or set(p) - {"id", "qty"} or not p.get("id"):
                errors.append(f"{w}: a part is {{id, qty}}; got {p!r} (quote a qty that has a comma in it)")
                continue
            part_ok(p.get("id"), w)
    ids = {c.get("id") for c in h.get("cables") or []}
    for b in h.get("bundles") or []:
        w = f"{where}: bundle {b.get('id')}"
        for k in ("id", "title", "what", "branches"):
            if not b.get(k):
                errors.append(f"{w}: missing {k}")
        for br in b.get("branches") or []:
            if br.get("cable") not in ids:
                errors.append(f"{w}: {br.get('cable')!r} is not a cable in this file")
            if not all(isinstance(br.get(k), (int, float)) and br[k] > 0 for k in ("at_mm", "tail_mm")):
                errors.append(f"{w}: branch {br.get('cable')!r} needs at_mm and tail_mm, in millimetres")
    h["_conns"] = conns


# ---- drawing: a port of the 2CAN configurator's cableSvg() ------------------------------------
# FILE: fixed colours, for a drawing saved to docs/assets (it has no stylesheet around it).
# PAGE: the same roles as CSS variables, for a drawing inline in a page that defines them.
FILE = dict(ink="#17211c", muted="#5a6860", line="#c7d0c9", pad="#d4a94c", surface="#ffffff", accent="#c2602b",
            display="'Space Grotesk', 'Helvetica Neue', Arial, sans-serif",
            body="'IBM Plex Sans', 'Helvetica Neue', Arial, sans-serif",
            mono="'IBM Plex Mono', ui-monospace, Menlo, Consolas, monospace", backdrop=True)
PAGE = dict(ink="var(--fg)", muted="var(--muted)", line="var(--line)", pad="var(--pad)", surface="var(--surface)", accent="var(--copper)",
            display="var(--display)", body="var(--body)", mono="var(--mono)", backdrop=False)
e = html.escape


def shown_pins(cab, conns):
    """{conn: [pin ids drawn]}: every pin of a small connector, only the used ones of a large one."""
    used = {}
    for a, b, _ in cab["_wires"]:
        for e_ in (a, b):
            if e_:
                used.setdefault(e_[0], set()).add(e_[1])
    return {cid: [p for p in conns[cid]["pins"] if len(conns[cid]["pins"]) <= ALL_PINS_UP_TO or p in pins]
            for cid, pins in used.items()}


def cable_ends(cab):
    lefts, rights = [], []
    for a, b, _ in cab["_wires"]:
        if a and a[0] not in lefts:
            lefts.append(a[0])
    for a, b, _ in cab["_wires"]:
        if b and b[0] not in rights and b[0] not in lefts:
            rights.append(b[0])
    return lefts, rights


def text_width(text, px, bold=False):
    """Roughly how wide `text` draws, erring wide: the drawing is read in whatever font the reader has."""
    em = sum(0.36 if ch in "iljtfrI.,:;'|!()[] ·" else 0.92 if ch in "mwMW@" else
             0.70 if ch.isupper() or ch.isdigit() else 0.58 for ch in text)
    return em * px * (1.06 if bold else 1)


def wrap(text, px, width, bold=False):
    """`text` as lines no wider than `width`, broken at spaces; a single word that is too wide is split."""
    lines, cur = [], ""
    for word in str(text).split():
        if cur and text_width(f"{cur} {word}", px, bold) > width:
            lines.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}".strip()
        while text_width(cur, px, bold) > width and len(cur) > 1:
            n = len(cur) - 1
            while n > 1 and text_width(cur[:n], px, bold) > width:
                n -= 1
            lines.append(cur[:n])
            cur = cur[n:]
    return lines + [cur] if cur else lines


def cable_svg(cab, conns, t=FILE):
    lefts, rights = cable_ends(cab)
    shown = shown_pins(cab, conns)
    row, bw, W, gap = 22, 232, 760, 14
    fit = lambda text, n: text if len(text) <= n else text[: n - 1].rstrip() + "…"
    lx, rx = 8, W - 8 - bw
    # A connector's name is never cut short: it wraps, and the box grows a line. Under it, what the
    # mating end is, or how many pins are left out of the drawing; nothing when every pin is drawn.
    title = {cid: wrap(conns[cid]["name"], 13, bw - 20, bold=True) for cid in shown}
    under = {cid: wrap(conns[cid]["kind"] or (f"{len(shown[cid])} of its {len(conns[cid]['pins'])} pins"
                                             if len(shown[cid]) < len(conns[cid]["pins"]) else ""), 11, bw - 20)
             for cid in shown}
    head = lambda cid: 15 * len(title[cid]) + 13 * len(under[cid]) + (6 if under[cid] else 9)
    box_h = lambda cid: head(cid) + len(shown[cid]) * row + 6
    cap = lambda cid: 18 if conns[cid]["end"] else 0       # room over the box for "HAT END"
    stack = lambda ids: sum(box_h(c) + cap(c) for c in ids) + gap * max(0, len(ids) - 1)
    H = max(stack(lefts), stack(rights)) + 28
    tops = {}
    for ids in (lefts, rights):
        y = (H - stack(ids)) / 2 + 6
        for c in ids:
            tops[c] = y + cap(c)
            y += cap(c) + box_h(c) + gap
    pin_y = lambda cid, pin: tops[cid] + head(cid) + shown[cid].index(pin) * row + row / 2 + 2

    def box(cid, x, side):
        c, top = conns[cid], tops[cid]
        dash = ' stroke-dasharray="5 4"' if c["bare"] else ""
        out = f'<rect x="{x}" y="{top:.1f}" width="{bw}" height="{box_h(cid)}" rx="8" fill="{t["surface"]}" stroke="{t["ink"]}" stroke-width="1.5"{dash}/>'
        if c["end"]:
            out += (f'<text x="{x + 2}" y="{top - 6:.1f}" font-family="{t["mono"]}" font-weight="600" font-size="11" letter-spacing="0.6" '
                    f'fill="{t["accent"]}">{e(end_label(c).upper())}</text>')
        for i, text in enumerate(title[cid]):
            out += f'<text x="{x + 10}" y="{top + 15 * (i + 1):.1f}" font-family="{t["display"]}" font-weight="700" font-size="13" fill="{t["ink"]}">{e(text)}</text>'
        for i, text in enumerate(under[cid]):
            out += f'<text x="{x + 10}" y="{top + 15 * len(title[cid]) + 13 * (i + 1):.1f}" font-family="{t["body"]}" font-size="11" fill="{t["muted"]}">{e(text)}</text>'
        for i, pid in enumerate(shown[cid]):
            py = top + head(cid) + i * row + row / 2 + 2
            nx = x + bw - 18 if side == "L" else x + 8
            nc = pid in c["nc"]
            out += (f'<line x1="{x + 6}" x2="{x + bw - 6}" y1="{py - row / 2 + 1:.1f}" y2="{py - row / 2 + 1:.1f}" stroke="{t["line"]}"/>'
                    f'<rect x="{nx}" y="{py - 7:.1f}" width="10" height="14" rx="2" fill="{"none" if c["bare"] or nc else t["pad"]}" stroke="{t["ink"]}" stroke-width="1"/>')
            if c["numbered"] and not c["bare"]:
                tx, anchor = (nx - 6, "end") if side == "L" else (nx + 16, "start")
                out += f'<text x="{tx}" y="{py + 4:.1f}" text-anchor="{anchor}" font-family="{t["mono"]}" font-size="10" fill="{t["muted"]}">{e(pid)}</text>'
            tx, anchor = (x + 10, "start") if side == "L" else (x + bw - 10, "end")
            out += f'<text x="{tx}" y="{py + 4:.1f}" text-anchor="{anchor}" font-family="{t["mono"]}" font-size="11" fill="{t["muted"] if nc else t["ink"]}">{e(fit(c["pins"][pid], 24))}</text>'
        return out

    x0, x1 = lx + bw, rx
    tx0, tx1 = x0 + 34, x0 + 150
    pairs = {}
    for a, b, w in cab["_wires"]:
        if w.get("pair") and a and b:
            pairs.setdefault(w["pair"], []).append((a, b, w))
    wires = ""
    for a, b, w in cab["_wires"]:
        if not a:
            continue
        ya, col = pin_y(*a), COLOURS[w["colour"]]
        if w.get("spare"):     # in the cable, carrying nothing: thin and dashed, so it does not read as a connection
            stroke = lambda d: (f'<path d="{d}" fill="none" stroke="{t["ink"]}" stroke-opacity="0.45" stroke-width="3.5" stroke-dasharray="7 6"/>'
                                f'<path d="{d}" fill="none" stroke="{col}" stroke-width="1.8" stroke-dasharray="7 6"/>')
        else:
            stroke = lambda d: (f'<path d="{d}" fill="none" stroke="{t["ink"]}" stroke-opacity="0.45" stroke-width="5.5" stroke-linecap="round"/>'
                                f'<path d="{d}" fill="none" stroke="{col}" stroke-width="3.5" stroke-linecap="round"/>')
        if not b:
            wires += stroke(f"M{x0},{ya:.1f} L{x0 + 40},{ya:.1f}")
            wires += f'<text x="{x0 + 48}" y="{ya + 4:.1f}" font-family="{t["body"]}" font-size="11" fill="{t["muted"]}">not connected: insulate</text>'
            continue
        yb = pin_y(*b)
        if w.get("pair") and len(pairs.get(w["pair"], [])) == 2:
            grp = pairs[w["pair"]]
            k = [g[2] for g in grp].index(w)
            ym = sum(pin_y(*g[0]) for g in grp) / len(grp)
            pts = [f"{x},{ym + 5 * math.sin((x - tx0) / 14 * math.pi) * (-1 if k else 1):.1f}" for x in range(int(tx0), int(tx1) + 1, 3)]
            d = f"M{x0},{ya:.1f} C{x0 + 20},{ya:.1f} {tx0 - 14},{ym:.1f} {tx0},{ym:.1f} L{' L'.join(pts)} C{tx1 + 70},{ym:.1f} {x1 - 70},{yb:.1f} {x1},{yb:.1f}"
        else:
            d = f"M{x0},{ya:.1f} C{x0 + 150},{ya:.1f} {x1 - 150},{yb:.1f} {x1},{yb:.1f}"
        wires += stroke(d)
        if cab.get("splice"):
            sx, sy = (x0 + x1) / 2 - 10, (ya + yb) / 2
            wires += f'<rect x="{sx}" y="{sy - 5:.1f}" width="20" height="10" rx="3" fill="{t["muted"]}" stroke="{t["ink"]}" stroke-width="1"/>'
    labels = "".join(
        f'<text x="{(tx0 + tx1) / 2}" y="{sum(pin_y(*g[0]) for g in grp) / len(grp) - 12:.1f}" text-anchor="middle" font-family="{t["body"]}" font-size="11" fill="{t["muted"]}">twisted pair</text>'
        for grp in pairs.values() if len(grp) == 2)
    if cab.get("splice"):
        labels += f'<text x="{(x0 + x1) / 2}" y="14" text-anchor="middle" font-family="{t["body"]}" font-size="11" fill="{t["muted"]}">{e(cab["splice"])}</text>'
    boxes = "".join(box(c, lx, "L") for c in lefts) + "".join(box(c, rx, "R") for c in rights)
    back = f'<rect width="{W}" height="{H:.0f}" fill="{t["surface"]}"/>' if t["backdrop"] else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H:.0f}" role="img" aria-label="{e(cab["title"])} wiring">'
            f'{back}{wires}{labels}{boxes}</svg>\n')


def cable_boards(cab, conns, boards):
    """{board: [conn ids]}: the boards this cable plugs into that say where their connectors are."""
    out = {}
    for cid in [c for side in cable_ends(cab) for c in side]:
        src = conns[cid]["src"]
        if src and conns[cid]["box"] and boards[src[0]].get("outline"):
            out.setdefault(src[0], []).append(cid)
    return out


def end_label(c):
    """"Hat end", "RC receiver end": which end of the cable this connector is."""
    return c["end"] if str(c["end"]).lower().endswith(" end") else f"{c['end']} end"


def locator_svg(board, marks, t=FILE):
    """The board from above: its outline, every connector as a faint box, and `marks` filled and named.
    marks: [(label, box)]; an empty label marks the box without naming it."""
    bw, bh = board["outline"]
    k, pad = 2.6, 18                                       # px per mm
    W, H = bw * k + 2 * pad, bh * k + 2 * pad + 20
    clip = lambda b: [max(-5, b[0]), max(-5, b[1]), min(bw + 5, b[2]), min(bh + 5, b[3])]
    rect = lambda b, extra: (f'<rect x="{pad + b[0] * k:.1f}" y="{pad + b[1] * k:.1f}" width="{(b[2] - b[0]) * k:.1f}" '
                             f'height="{(b[3] - b[1]) * k:.1f}" {extra}/>')
    out = [f'<rect x="{pad}" y="{pad}" width="{bw * k:.1f}" height="{bh * k:.1f}" rx="4" fill="{t["surface"]}" stroke="{t["ink"]}" stroke-width="1.5"/>']
    for mark in (board.get("landmarks") or {}).values():
        b = clip(mark["box"])
        out.append(rect(b, f'rx="2" fill="none" stroke="{t["line"]}" stroke-dasharray="3 3"'))
        out.append(f'<text x="{pad + (b[0] + b[2]) / 2 * k:.1f}" y="{pad + (b[1] + b[3]) / 2 * k + 3:.1f}" text-anchor="middle" '
                   f'font-family="{t["body"]}" font-size="12" fill="{t["muted"]}">{e(mark["name"])}</text>')
    named = {label for label, _ in marks}
    for ref, c in board["connectors"].items():
        if c.get("box") and ref not in named:
            out.append(rect(clip(c["box"]), f'rx="1.5" fill="none" stroke="{t["muted"]}" stroke-opacity="0.7"'
                                            + (' stroke-dasharray="2 2"' if c.get("back") else "")))
    for label, box in marks:
        b = clip(box)
        small = (b[2] - b[0]) * k < 9 or (b[3] - b[1]) * k < 9          # a 0603 part: grow it so it can be seen
        if small:
            cx_, cy_ = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
            b = [cx_ - 2.2, cy_ - 2.2, cx_ + 2.2, cy_ + 2.2]
        out.append(rect(b, f'rx="1.5" fill="{t["accent"]}" stroke="{t["ink"]}" stroke-width="1.2"'))
        if not label:
            continue
        cx, below = pad + (b[0] + b[2]) / 2 * k, (b[1] + b[3]) / 2 < bh / 2
        ty = pad + b[3] * k + 17 if below else pad + b[1] * k - 6
        cx = min(max(cx, pad + 18), pad + bw * k - 18)
        out.append(f'<text x="{cx:.1f}" y="{ty:.1f}" text-anchor="middle" font-family="{t["display"]}" font-weight="700" '
                   f'font-size="17" fill="{t["ink"]}" stroke="{t["surface"]}" stroke-width="4" paint-order="stroke">{e(label)}</text>')
    side = "underside connectors dashed" if any(c.get("back") for c in board["connectors"].values()) else "seen from above"
    out.append(f'<text x="{pad}" y="{H - 7:.1f}" font-family="{t["body"]}" font-size="14" fill="{t["muted"]}">'
               f'{e(str(board["board"]))}, revision {e(str(board.get("rev", "?")))}: {side}</text>')
    back = f'<rect width="{W:.0f}" height="{H:.0f}" fill="{t["surface"]}"/>' if t["backdrop"] else ""
    what = ", ".join(label for label, _ in marks if label)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W:.0f} {H:.0f}" role="img" '
            f'aria-label="Where {e(what)} is on the {e(str(board["board"]))} board">{back}{"".join(out)}</svg>\n')


def conn_marks(conns, cids):
    return [(conns[c]["src"][1], conns[c]["box"]) for c in cids]


def setting_svg(j, chosen=None, t=FILE, product=None):
    """A jumper or solder bridge, one panel per position: the pads as they sit on the board, what each
    position joins, which one the board arrives in, and which one to use here."""
    names = list(j["positions"])
    W, gap = 760, 12
    pw = (W - 16 - gap * (len(names) - 1)) / len(names)
    at = j["pad_at"]
    xs, ys = [p_[0] for p_ in at.values()], [p_[1] for p_ in at.values()]
    row = (max(xs) - min(xs)) >= (max(ys) - min(ys))                    # drawn the way it lies on the board
    order = sorted(at, key=lambda p_: at[p_][0 if row else 1])
    step = 78 if row else 36
    art_h = 62 if row else step * (len(order) - 1) + 40
    text = {n: wrap(j["positions"][n]["means"], 11.5, pw - 24) for n in names}
    used = f"{product} uses this" if product else "used here"
    tags = {n: [w_ for w_, on in (("as shipped", n == j.get("ships")), (used, n == chosen)) if on] for n in names}
    top = 30 + 14 * max(len(v) for v in tags.values())
    H = top + art_h + 10 + 14 * max(len(v) for v in text.values()) + 14
    out = []
    for i, n in enumerate(names):
        x = 8 + i * (pw + gap)
        pick = n == chosen
        out.append(f'<rect x="{x:.1f}" y="4" width="{pw:.1f}" height="{H - 8:.0f}" rx="8" fill="{t["surface"]}" '
                   f'stroke="{t["accent"] if pick else t["line"]}" stroke-width="{2.5 if pick else 1.2}"/>')
        out.append(f'<text x="{x + 12:.1f}" y="24" font-family="{t["display"]}" font-weight="700" font-size="14" fill="{t["ink"]}">{e(n)}</text>')
        for k_, tag in enumerate(tags[n]):
            use = tag == used
            out.append(f'<text x="{x + 12:.1f}" y="{40 + 14 * k_}" font-family="{t["mono"]}" font-size="10.5" font-weight="600" letter-spacing="0.5" '
                       f'fill="{t["accent"] if use else t["muted"]}">{e(tag.upper())}</text>')
        if row:
            x0 = x + pw / 2 - step * (len(order) - 1) / 2
            centre = {p_: (x0 + k_ * step, top + 18) for k_, p_ in enumerate(order)}
        else:
            centre = {p_: (x + 30, top + 18 + k_ * step) for k_, p_ in enumerate(order)}
        join = j["positions"][n]["join"]
        if len(join) == 2 and j.get("link") == "cap":                   # a jumper cap over two pins
            (ax, ay), (bx, by) = centre[join[0]], centre[join[1]]
            out.append(f'<rect x="{min(ax, bx) - 17:.1f}" y="{min(ay, by) - 17:.1f}" width="{abs(ax - bx) + 34:.1f}" height="{abs(ay - by) + 34:.1f}" '
                       f'rx="6" fill="{t["accent"]}" fill-opacity="0.9" stroke="{t["ink"]}" stroke-width="1.2"/>')
        for p_ in order:
            cx, cy = centre[p_]
            out.append(f'<rect x="{cx - 11:.1f}" y="{cy - 11:.1f}" width="22" height="22" rx="5" fill="{t["pad"]}" stroke="{t["ink"]}" stroke-width="1"/>'
                       f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="4.5" fill="{t["surface"]}" stroke="{t["ink"]}" stroke-width="1"/>')
            label = e(j["pad_names"][p_])
            if row:
                out.append(f'<text x="{cx:.1f}" y="{cy + 28:.1f}" text-anchor="middle" font-family="{t["mono"]}" font-size="10.5" fill="{t["ink"]}">{label}</text>')
            else:
                out.append(f'<text x="{cx + 24:.1f}" y="{cy + 4:.1f}" font-family="{t["mono"]}" font-size="10.5" fill="{t["ink"]}">{label}</text>')
        if len(join) == 2 and j.get("link") != "cap":                   # a soldered wire link between two pads
            (ax, ay), (bx, by) = centre[join[0]], centre[join[1]]
            d = f"M{ax:.1f},{ay:.1f} L{bx:.1f},{by:.1f}"
            out.append(f'<path d="{d}" fill="none" stroke="{t["ink"]}" stroke-width="9" stroke-linecap="round"/>'
                       f'<path d="{d}" fill="none" stroke="{t["accent"]}" stroke-width="5.5" stroke-linecap="round"/>')
        if not join:
            ny = top + art_h - 2 if not row else top + 58
            out.append(f'<text x="{x + pw / 2 if row else x + 12:.1f}" y="{ny:.1f}" text-anchor="{"middle" if row else "start"}" font-family="{t["body"]}" '
                       f'font-size="10.5" fill="{t["muted"]}">nothing joined</text>')
        for k_, line_ in enumerate(text[n]):
            out.append(f'<text x="{x + 12:.1f}" y="{top + art_h + 22 + 14 * k_:.1f}" font-family="{t["body"]}" font-size="11.5" fill="{t["ink"]}">{e(line_)}</text>')
    back = f'<rect width="{W}" height="{H:.0f}" fill="{t["surface"]}"/>' if t["backdrop"] else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H:.0f}" role="img" '
            f'aria-label="{e(j.get("title", "setting"))}: {e(", ".join(names))}">{back}{"".join(out)}</svg>\n')


def drawn_settings(h, boards):
    """[(setting, jumper)] for the settings whose board describes the jumper's positions."""
    out = []
    for s_ in h.get("settings") or []:
        j = boards[s_["board"]].get("jumpers", {}).get(s_["ref"]) or {}
        if j.get("positions"):
            out.append((s_, j))
    return out



def verdict(s_, j, product=None):
    """(what the board ships as and what the product does about it, why). No instruction to "check":
    a default that is right needs no action."""
    who, ships = product or "This build", j.get("ships", "not stated")
    if not s_:
        return f"Ships as: {ships}. {who} has not picked a position yet.", ""
    if s_["set"] == ships:
        return f"Ships as: {ships}. {who} keeps it that way.", s_["means"]
    return f"Ships as: {ships}. {who} changes it to: {s_['set']}.", s_["means"]



def plugs(cab, conns):
    """One line per end of the cable: which end it is, the connector, and what kind it is. Empty when
    no end says a kind, so a harness that names none reads as it did before."""
    ends = [conns[c] for side in cable_ends(cab) for c in side]
    if not any(c["type"] or c["kind"] for c in ends):
        return []
    return [(f"{end_label(c)}: {c['name']}" if c["end"] else c["name"], c["type"] or (c["kind"] if not c["src"] else "") or "")
            for c in ends]


def drawings(h, boards):
    """{file name: svg} for everything this harness draws: a drawing per cable, and a board picture per
    board a cable plugs into, when the board says where its connectors are."""
    out = {}
    for cab in h["cables"]:
        out[f"{h['assembly']}-{cab['id']}.svg"] = cable_svg(cab, h["_conns"])
        for b, cids in cable_boards(cab, h["_conns"], boards).items():
            out[f"{h['assembly']}-{cab['id']}-on-{b}.svg"] = locator_svg(boards[b], conn_marks(h["_conns"], cids))
    for s_, j in drawn_settings(h, boards):
        out[f"{h['assembly']}-set-{s_['ref']}.svg"] = setting_svg(j, s_["set"], product=h.get("product"))
        if j.get("box") and boards[s_["board"]].get("outline"):
            out[f"{h['assembly']}-set-{s_['ref']}-on-{s_['board']}.svg"] = locator_svg(boards[s_["board"]], [(s_["ref"], j["box"])])
    return out


def bundle_svg(b, h, t=FILE):
    """Cables that run together: a trunk from one end, each cable peeling off at its own distance.
    Every cable is one line; a ruler under the trunk gives the distance to each breakout, and each
    tail carries its own length. PROVISIONAL (see the header)."""
    cabs, conns = {c["id"]: c for c in h["cables"]}, h["_conns"]
    br = sorted(b["branches"], key=lambda x: x["at_mm"])
    W, H, x0, x1, ty, lane = 760, 340, 150, 560, 152, 6
    k = (x1 - x0) / max(x["at_mm"] for x in br)
    ups, downs = br[1::2], br[0::2]                           # alternate sides, so neighbours do not collide
    out = []
    if b.get("example"):
        out.append(f'<text x="8" y="14" font-family="{t["mono"]}" font-weight="600" font-size="11" letter-spacing="0.6" fill="{t["accent"]}">'
                   f'EXAMPLE: THE LENGTHS ARE PLACEHOLDERS</text>')
    span = lane * len(br) + 14
    out.append(f'<rect x="{x0}" y="{ty - span / 2:.1f}" width="{x1 - x0}" height="{span}" rx="{span / 2:.1f}" fill="none" stroke="{t["line"]}" stroke-dasharray="5 4"/>')
    for side, group in ((-1, ups), (1, downs)):
        for i, x in enumerate(group):
            cab = cabs[x["cable"]]
            y = ty + side * ((len(group) - i) * lane - lane / 2)       # the first to leave rides on the outside
            xb = x0 + x["at_mm"] * k
            reach = min(max(x["tail_mm"] * k, 44), 84)
            xe, ye = xb + reach * 0.5, y + side * reach * 0.87
            d = f"M{x0},{y:.1f} L{xb - 8:.1f},{y:.1f} Q{xb:.1f},{y:.1f} {xb + 5:.1f},{y + side * 8:.1f} L{xe:.1f},{ye:.1f}"
            out.append(f'<path d="{d}" fill="none" stroke="{t["ink"]}" stroke-opacity="0.45" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>'
                       f'<path d="{d}" fill="none" stroke="{t["muted"]}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>')
            out.append(f'<line x1="{xb - 12:.1f}" x2="{xb - 12:.1f}" y1="{ty - span / 2 - 3:.1f}" y2="{ty + span / 2 + 3:.1f}" stroke="{t["ink"]}" stroke-width="1.5"/>')
            mx, my = (xb + 5 + xe) / 2, (y + side * 8 + ye) / 2
            out.append(f'<text x="{mx + 10:.1f}" y="{my + 4:.1f}" font-family="{t["mono"]}" font-size="11" fill="{t["ink"]}" stroke="{t["surface"]}" stroke-width="3" paint-order="stroke">{x["tail_mm"]:g} mm</text>')
            far = [c for c in cable_ends(cab)[1]]
            end_ = end_label(conns[far[0]]) if far and conns[far[0]]["end"] else "far end"
            wires = [w for _, _, w in cab["_wires"] if not w.get("spare")]
            bx, by = xe - 20, (ye + 4 if side > 0 else ye - 40)
            out.append(f'<rect x="{bx:.1f}" y="{by:.1f}" width="134" height="36" rx="7" fill="{t["surface"]}" stroke="{t["ink"]}" stroke-width="1.3"/>'
                       f'<text x="{bx + 9:.1f}" y="{by + 15:.1f}" font-family="{t["display"]}" font-weight="700" font-size="12" fill="{t["ink"]}">{e(cab["title"])}</text>'
                       f'<text x="{bx + 9:.1f}" y="{by + 29:.1f}" font-family="{t["mono"]}" font-size="10" fill="{t["muted"]}">{e(end_.upper())}</text>')
            for n, w in enumerate(wires):                               # the conductors inside this cable
                out.append(f'<circle cx="{bx + 125 - n * 11:.1f}" cy="{by + 25:.1f}" r="4" fill="{COLOURS[w["colour"]]}" stroke="{t["ink"]}" stroke-width="0.8"/>')
    out.append(f'<rect x="8" y="{ty - 24}" width="{x0 - 8}" height="48" rx="8" fill="{t["surface"]}" stroke="{t["ink"]}" stroke-width="1.5"/>')
    for i, line_ in enumerate(wrap(b.get("start", "Start"), 12, x0 - 26, bold=True)[:2]):
        out.append(f'<text x="18" y="{ty - 4 + 15 * i}" font-family="{t["display"]}" font-weight="700" font-size="12" fill="{t["ink"]}">{e(line_)}</text>')
    ry = H - 26                                                         # the ruler: distance along the trunk
    out.append(f'<line x1="{x0}" x2="{x1}" y1="{ry}" y2="{ry}" stroke="{t["ink"]}" stroke-width="1.2"/>')
    for mm in [0] + sorted({x["at_mm"] for x in br}):
        x = x0 + mm * k
        out.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{ry - 5}" y2="{ry + 5}" stroke="{t["ink"]}" stroke-width="1.2"/>'
                   f'<text x="{x:.1f}" y="{ry + 18}" text-anchor="middle" font-family="{t["mono"]}" font-size="11" fill="{t["ink"]}">{mm:g} mm</text>')
    out.append(f'<text x="{x1 + 14}" y="{ry + 4}" font-family="{t["body"]}" font-size="11" fill="{t["muted"]}">along the trunk</text>')
    back = f'<rect width="{W}" height="{H}" fill="{t["surface"]}"/>' if t["backdrop"] else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" role="img" aria-label="{e(b["title"])}">'
            f'{back}{"".join(out)}</svg>\n')


PHOTO_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


def photo_slots(h, boards):
    """Every photo this harness has a place for: [(owner, file stem, caption, what to photograph)].
    owner is a cable id, or "set-<ref>" for a setting."""
    a, conns, out = h["assembly"], h["_conns"], []
    for s_, _ in drawn_settings(h, boards):
        out.append((f"set-{s_['ref']}", f"{a}-set-{s_['ref']}", f"{s_['ref']} set to {s_['set']}",
                    f"{s_['ref']} on the board, set to {s_['set']}, close enough to see every pad"))
    for cab in h["cables"]:
        out.append((cab["id"], f"{a}-{cab['id']}", f"{cab['title']}: the finished cable",
                    f"{cab['title']}: the finished cable laid flat on a plain surface, both ends in view, a ruler beside it"))
        for cid in [c for side in cable_ends(cab) for c in side]:
            c = conns[cid]
            where = end_label(c) if c["end"] else c["name"]
            out.append((cab["id"], f"{a}-{cab['id']}-{cid}", f"{cab['title']}: plugged in at the {where}",
                        f"{cab['title']}: the {where} plugged in" + (f" ({c['name']})" if c["end"] else "")
                        + ", close enough to see which connector it is and which way round"))
    return out


def find_photo(repo, stem):
    for ext in PHOTO_TYPES:
        path = os.path.join(repo.photos, stem + ext)
        if os.path.exists(path):
            return path
    return None



def photos_for(h, boards, repo, owner, cab=None):
    """([(path, caption)] to show, [(file stem, what to photograph)] still to take) for one cable or setting.
    The cable's own photos come first, then any it borrows from elsewhere in the guide (`photos`)."""
    have, want = [], []
    for own, stem, caption, shoot in photo_slots(h, boards):
        if own == owner:
            path = find_photo(repo, stem)
            if path:
                have.append((path, caption))
            else:
                want.append((stem, shoot))
    have += [(os.path.join(repo.root, ph["file"]), ph["caption"]) for ph in (cab or {}).get("photos") or []]
    return have, want


def photo_src(path, out_dir):
    """A standalone page carries a small photo inside itself, so the one file can be sent to someone."""
    if os.path.getsize(path) <= 600_000:
        kind = PHOTO_TYPES[os.path.splitext(path)[1].lower()]
        return f"data:{kind};base64," + base64.b64encode(open(path, "rb").read()).decode("ascii")
    return os.path.relpath(path, out_dir).replace(os.sep, "/")



def facts(cab):
    awg = sorted({str(w["awg"]) for _, _, w in cab["_wires"] if w.get("awg")}, key=lambda s: (len(s), s))
    note = cab.get("length_note")
    length = (f"{cab['length_mm']} mm" + (f" ({note})" if note else "")) if cab.get("length_mm") else (f"not measured yet: {note}" if note else None)
    return [(k, v) for k, v in (("Length", length), ("Build", cab.get("build")),
                                ("Wire", (" and ".join(awg) + " AWG") if awg else None),
                                ("Checked", cab.get("checked"))) if v]


def cable_notes(cab, conns):
    """The cable's own notes, then each connector's trap, once."""
    ends = [c for side in cable_ends(cab) for c in side]
    return list(cab.get("notes") or []) + [f"{conns[c]['name']}: {conns[c]['note']}" for c in ends if conns[c]["note"]]


# ---- the docs-page block (Markdown) -------------------------------------------------------------
def md_escape(s):
    return str(s).replace("|", "\\|")


def part_link(pid, parts):
    p = parts.get(pid)
    if not p:
        return md_escape(pid)
    return f"[{md_escape(p['name'])}]({p['source']})" if p.get("source") else md_escape(p["name"])


def md_gallery(have, page):
    """The card's photos, folded away until asked for. Raw HTML, so it needs no Markdown extension."""
    if not have:
        return []
    out = [f"<details><summary>Photos ({len(have)})</summary>", ""]
    for path, caption in have:
        src = os.path.relpath(path, os.path.dirname(page)).replace(os.sep, "/")
        out += [f'<figure><img src="{src}" alt="{e(caption)}" loading="lazy"><figcaption>{e(caption)}</figcaption></figure>', ""]
    return out + ["</details>", ""]


def block(h, parts, repo, boards):
    conns, page = h["_conns"], os.path.join(repo.root, h["page"])
    rel = os.path.relpath(repo.svg, os.path.dirname(page))
    out = []
    drawn = {id(s_): j for s_, j in drawn_settings(h, boards)}
    for s in h.get("settings") or []:
        if id(s) not in drawn:
            out.append(f"- **Set {s['ref']} to {s['set']}:** {s['means']}")
    if out:
        out.append("")
    for s in h.get("settings") or []:
        j = drawn.get(id(s))
        if not j:
            continue
        out += [f"### Board setting: {j.get('title', s['ref'])} ({s['ref']})", "",
                f"{j.get('selects', '')} {j.get('type', '')}".strip(), "",
                f"![{s['ref']}: every position]({rel}/{h['assembly']}-set-{s['ref']}.svg)", ""]
        if j.get("box") and boards[s["board"]].get("outline"):
            out += [f"![Where {s['ref']} is on the {s['board']} board]({rel}/{h['assembly']}-set-{s['ref']}-on-{s['board']}.svg)", ""]
        head, why = verdict(s, j, h.get("product"))
        out += [f"**{head}** {why}".rstrip(), ""]
        out += md_gallery(photos_for(h, boards, repo, f"set-{s['ref']}")[0], page)
        if j.get("ideas"):
            out += ["**Why you might change it**", ""] + [f"- **{md_escape(k)}:** {v}" for k, v in j["ideas"].items()] + [""]
        if j.get("how"):
            out += [f"**How:** {j['how']}", ""]
    for cab in h["cables"]:
        a_ids = {a[0] for a, _, _ in cab["_wires"] if a}
        b_ids = {b[0] for _, b, _ in cab["_wires"] if b}
        pin = lambda e_: f"{e_[1]} {conns[e_[0]]['pins'][e_[1]]}" if conns[e_[0]]["numbered"] and e_[1] != conns[e_[0]]["pins"][e_[1]] else conns[e_[0]]["pins"][e_[1]]
        side = lambda ids, e_: (pin(e_) if len(ids) == 1 else f"{conns[e_[0]]['name']} · {pin(e_)}") if e_ else "—"
        head = lambda ids, word: conns[next(iter(ids))]["name"] if len(ids) == 1 else word
        out += [f"### {cab['title']}", "", cab["what"], "",
                f"![{cab['title']} wiring]({rel}/{h['assembly']}-{cab['id']}.svg)", ""]
        for b, cids in cable_boards(cab, conns, boards).items():
            refs = ", ".join(conns[c]["src"][1] for c in cids)
            out += [f"![Where {refs} is on the {b} board]({rel}/{h['assembly']}-{cab['id']}-on-{b}.svg)", ""]
        ends_ = plugs(cab, conns)
        if ends_:
            out += [f"- **{md_escape(n)}:** {md_escape(d)}" if d else f"- **{md_escape(n)}**" for n, d in ends_] + [""]
        out += md_gallery(photos_for(h, boards, repo, cab["id"], cab)[0], page)
        awg = any(w.get("awg") for _, _, w in cab["_wires"])
        cols = ["Signal", head(a_ids, "From"), "Wire"] + (["AWG"] if awg else []) + [head(b_ids, "To") if b_ids else "To"]
        out += ["| " + " | ".join(md_escape(c) for c in cols) + " |", "|" + "---|" * len(cols)]
        for a, b, w in cab["_wires"]:
            row = [w["signal"], side(a_ids, a), w["colour"]] + ([w.get("awg", "")] if awg else []) + [side(b_ids, b) if b else "— (insulate)"]
            out.append("| " + " | ".join(md_escape(c) for c in row) + " |")
        out.append("")
        line = " · ".join(f"**{k}:** {v}" for k, v in facts(cab) if k != "Wire")
        if line:
            out += [line if line.endswith((".", "!", "?")) else line + ".", ""]
        if cab.get("spec"):
            out += [f"**{cab.get('spec_title', 'Details')}**", ""] + [f"- **{md_escape(k)}:** {v}" for k, v in cab["spec"].items()] + [""]
        notes = cable_notes(cab, conns)
        out += [f"- {n}" for n in notes] + ([""] if notes else [])
        ends = [c for side_ in cable_ends(cab) for c in side_ if conns[c]["part"]]
        if ends:
            out += ["**Connects:** " + " · ".join(part_link(conns[c]["part"], parts) for c in ends), ""]
        if cab.get("parts"):
            out += ["**Parts for the cable:** " + " · ".join(f"{md_escape(p.get('qty', ''))} × {part_link(p['id'], parts)}".lstrip(" ×") for p in cab["parts"]), ""]
    return "\n".join(out).rstrip() + "\n"


# ---- the standalone page (HTML) -------------------------------------------------------------------
# Colour and type tokens, and the cable-card rules, from the 2CAN configurator (see the header).
CSS = """
:root { --bg:#f4f6f2; --surface:#ffffff; --fg:#17211c; --muted:#5a6860; --line:#c7d0c9; --pad:#d4a94c; --copper:#c2602b;
  --c-conn:#f7c58e; --c-ok:#acdc92; --c-draft:#f4e47c; --chip-ink:#17211c;
  --display:"Space Grotesk","Helvetica Neue",Arial,sans-serif; --body:"IBM Plex Sans","Helvetica Neue",Arial,sans-serif;
  --mono:"IBM Plex Mono",ui-monospace,Menlo,Consolas,monospace; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --bg:#0f1713; --surface:#16211b; --fg:#e2eae4; --muted:#96a59c;
  --line:#2c3b33; --pad:#c99d42; --copper:#e07a44; --c-conn:#e0ad78; --c-ok:#94c47c; --c-draft:#dccb68; color-scheme:dark; } }
:root[data-theme="dark"] { --bg:#0f1713; --surface:#16211b; --fg:#e2eae4; --muted:#96a59c; --line:#2c3b33; --pad:#c99d42;
  --copper:#e07a44; --c-conn:#e0ad78; --c-ok:#94c47c; --c-draft:#dccb68; color-scheme:dark; }
* { box-sizing: border-box; }
body { margin:0; padding:28px 16px 64px; background:var(--bg); color:var(--fg); font-family:var(--body); font-size:15px; line-height:1.45; }
main { max-width:920px; margin:0 auto; display:grid; gap:28px; }
main > * { min-width:0; }
h1,h2,h3 { font-family:var(--display); text-wrap:balance; margin:0; }
h1 { font-size:32px; letter-spacing:-0.01em; } h2 { font-size:20px; } h3 { font-size:15px; }
p { margin:0; } a { color:var(--copper); }
.muted { color:var(--muted); }
.titlerow { display:flex; flex-wrap:wrap; gap:10px; align-items:center; }
.pill { font-family:var(--mono); font-size:11px; font-weight:600; letter-spacing:0.04em; padding:2px 8px; border-radius:999px; color:var(--chip-ink); white-space:nowrap; }
.pill.robot { background:var(--c-ok); } .pill.draft { background:var(--c-draft); }
.chip { font-family:var(--mono); font-size:11.5px; padding:2px 8px; border-radius:6px; border:1px solid var(--line); background:var(--c-conn); color:var(--chip-ink); white-space:nowrap; }
.panel { border:1px solid var(--line); border-radius:12px; background:var(--surface); padding:12px 14px; display:grid; gap:8px; }
.facts { display:grid; grid-template-columns:max-content minmax(0,1fr); gap:2px 12px; margin:0; font-size:13px; }
.facts dt { color:var(--muted); } .facts dd { margin:0; overflow-wrap:anywhere; }
.cablecards { display:grid; gap:12px; }
.cablecard { border:1px solid var(--line); border-radius:12px; background:var(--bg); padding:12px 14px; min-width:0; }
.cablecard header { display:grid; grid-template-columns:minmax(0,1fr) auto; gap:10px; align-items:start; }
.cablecard header p { margin:2px 0 0; font-size:13px; color:var(--muted); }
.cdraw { margin:10px 0; overflow-x:auto; }
.cdraw svg { display:block; width:100%; min-width:520px; height:auto; }
.cnotes { margin:8px 0 0; padding-left:18px; font-size:13px; }
.where { display:grid; grid-template-columns:minmax(0,340px) minmax(0,1fr); gap:14px; align-items:center; margin:0 0 10px; padding:10px 0; border-top:1px solid var(--line); border-bottom:1px solid var(--line); }
.where figure { margin:0; display:grid; gap:6px; } .where svg { display:block; width:100%; height:auto; }
.where ul, .where ol { margin:0; padding-left:18px; font-size:13px; display:grid; gap:4px; }
.where .lead, .spec h4 { margin:0 0 4px; font-family:var(--display); font-size:13px; }
.spec { margin:10px 0 0; padding:10px 0 0; border-top:1px solid var(--line); }
details.gallery { margin:10px 0 0; padding:8px 0 0; border-top:1px solid var(--line); }
details.gallery summary, details.more summary { cursor:pointer; font-family:var(--display); font-size:13px; font-weight:700; }
details.gallery .todo { font-size:12px; color:var(--muted); margin:8px 0 0; padding-left:18px; }
details.more { margin-top:6px; } details.more > *:not(summary) { margin-top:8px; }
.why { margin:6px 0 0; padding-left:18px; font-size:13px; display:grid; gap:3px; }
.photos { display:grid; grid-template-columns:repeat(auto-fit, minmax(200px, 1fr)); gap:10px; margin:10px 0; }
.photos figure { margin:0; } .photos img { display:block; width:100%; height:auto; border-radius:8px; border:1px solid var(--line); }
.photos figcaption, .nophoto { font-size:12px; color:var(--muted); margin-top:4px; }
table.shots { border-collapse:collapse; font-size:13px; width:100%; } table.shots td, table.shots th { text-align:left; vertical-align:top; padding:4px 10px 4px 0; border-top:1px solid var(--line); }
table.shots td:first-child { font-family:var(--mono); font-size:12px; white-space:nowrap; }
table.lens td:first-child { font-family:inherit; font-size:13px; }
@media (max-width:640px) { .where { grid-template-columns:minmax(0,1fr); } }
.cparts { list-style:none; margin:8px 0 0; padding:8px 0 0; border-top:1px solid var(--line); font-size:13px; display:grid; gap:2px; }
.cparts .q { font-family:var(--mono); color:var(--muted); }
ul.plain { margin:0; padding-left:18px; font-size:13.5px; }
.unused { display:flex; flex-wrap:wrap; gap:6px; }
.unused .chip { background:transparent; border-style:dashed; color:var(--muted); }
"""



def part_html(pid, parts):
    p = parts.get(pid)
    if not p:
        return f'{e(str(pid))} <span class="muted">(no source yet)</span>'
    name = f'<a href="{e(p["source"])}" target="_blank" rel="noopener">{e(p["name"])}</a>' if p.get("source") else e(p["name"])
    more = [x for x in ((p.get("sku") or "").strip(), f"${p['price_usd'].strip()}" if (p.get("price_usd") or "").strip() else "",
                        "" if p.get("source") else "any supplier", (p.get("notes") or "").strip() if p.get("_pending") else "") if x]
    return name + (f' <span class="muted">· {e(" · ".join(more))}</span>' if more else "")



def photos_html(h, boards, repo, out_dir, owner, cab=None):
    """The card's gallery, folded shut: the photos there are, then the ones still to take."""
    if not repo:
        return ""
    have, want = photos_for(h, boards, repo, owner, cab)
    if not have and not want:
        return ""
    label = f"Photos ({len(have)})" + (f" · {len(want)} to take" if want else "")
    out = f'<details class="gallery"><summary>{label}</summary>'
    if have:
        out += '<div class="photos">'
        for p_, c_ in have:
            src = e(photo_src(p_, out_dir))
            out += f'<figure><a href="{src}" target="_blank"><img src="{src}" alt="{e(c_)}" loading="lazy"></a><figcaption>{e(c_)}</figcaption></figure>'
        out += "</div>"
    if want:
        out += '<ul class="todo">' + "".join(f"<li>{e(shoot)}. Save as <code>{e(stem)}.jpg</code></li>" for stem, shoot in want) + "</ul>"
    return out + "</details>"


def page_html(h, boards, parts, repo=None, out_dir=None):
    conns, product = h["_conns"], h.get("product")
    label = lambda b: str(boards[b].get("label") or b)
    used_boards = sorted({c["src"][0] for c in conns.values() if c["src"]} | {s["board"] for s in h.get("settings") or []})
    heading = h.get("title") or f"Wiring: {h['assembly']}"
    out = ['<!doctype html>', '<meta charset="utf-8">', f'<title>{e(heading)}</title>',
           '<meta name="viewport" content="width=device-width, initial-scale=1">',
           '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600&family=Space+Grotesk:wght@500;700&display=swap">',
           f"<style>{CSS}</style>", "<main>",
           f'<header><div class="titlerow"><h1>{e(heading)}</h1><span class="pill {h["status"]}">{"On the robot" if h["status"] == "robot" else "Draft"}</span></div>',
           '<p class="muted">' + ("Matches the boards listed below."
                                   if h["status"] == "robot" else
                                   "Draft. Pins on the boards come from the boards' design files. The other end of each cable comes from documents and has not been tried on hardware.") + "</p></header>"]
    unmeasured = [cab["title"] for cab in h["cables"] if not cab.get("length_mm")]
    if unmeasured:
        out.append('<section class="panel"><h2>Not measured yet</h2><p class="muted">These cables need a length before anyone cuts or orders. '
                   'Lay a string along the route on the robot, measure it, and add 50 mm.</p><ul class="plain">'
                   + "".join(f"<li>{e(t_)}</li>" for t_ in unmeasured) + "</ul></section>")
    chosen = {(s["board"], s["ref"]): s for s in h.get("settings") or []}
    plain = [s for s in h.get("settings") or [] if not (boards[s["board"]].get("jumpers", {}).get(s["ref"]) or {}).get("positions")]
    if plain:
        out.append('<section class="panel"><h2>Board settings</h2><ul class="plain">')
        out += [f'<li><b>{e(s["ref"])}: {e(str(s["set"]))}.</b> {e(s["means"])}</li>' for s in plain]
        out.append("</ul></section>")
    # Every selector on the boards in use, drawn: the ones this wiring sets, and the ones it leaves alone.
    cards = [(b, r, j) for b in used_boards for r, j in (boards[b].get("jumpers") or {}).items() if j.get("positions")]
    cards.sort(key=lambda c: (c[0], c[1]) not in chosen)               # the ones this wiring sets come first
    if cards:
        out.append('<section><h2>Board settings</h2><p class="muted">Options you pick with a jumper or a dab of solder. Each picture shows every choice'
                   + (f"; the outlined one is how {e(product)} is built." if product else ".") + '</p><div class="cablecards" style="margin-top:12px">')
        for b, r, j in cards:
            s = chosen.get((b, r))
            out.append(f'<article class="cablecard" id="set-{e(r)}"><header><div><h3>{e(j.get("title", r))}</h3>'
                       f'<p>{e(j.get("selects", ""))} {e(j.get("type", ""))}</p></div><span class="chip">{e(r)}</span></header>')
            out.append(f'<div class="cdraw">{setting_svg(j, s["set"] if s else None, PAGE, product)}</div>')
            pic = f'<figure>{locator_svg(boards[b], [(r, j["box"])], PAGE)}</figure>' if j.get("box") and boards[b].get("outline") else ""
            head, why = verdict(s, j, product)
            text = f'<p style="font-size:13px"><b>{e(head)}</b> {e(why)}</p>'
            if j.get("ideas"):
                text += ('<p class="lead" style="margin-top:8px">Why you might change it</p><ul class="why">'
                         + "".join(f"<li><b>{e(k)}:</b> {e(v)}</li>" for k, v in j["ideas"].items()) + "</ul>")
            if j.get("how"):
                text += f'<p style="font-size:13px;margin-top:8px"><b>How:</b> {e(j["how"])}</p>'
            out.append(f'<div class="where">{pic}<div>{text}</div></div>{photos_html(h, boards, repo, out_dir, "set-" + r) if s else ""}</article>')
        out.append("</div></section>")
    out.append('<section><h2>Cables</h2><div class="cablecards" style="margin-top:12px">')
    for cab in h["cables"]:
        lefts, _ = cable_ends(cab)
        chip = " · ".join(conns[c]["tag"] or conns[c]["name"] for c in lefts)
        out.append(f'<article class="cablecard" id="{e(cab["id"])}"><header><div><h3>{e(cab["title"])}</h3><p>{e(cab["what"])}</p></div><span class="chip">{e(chip)}</span></header>')
        out.append(f'<div class="cdraw">{cable_svg(cab, conns, PAGE)}</div>')
        where, ends_ = cable_boards(cab, conns, boards), plugs(cab, conns)
        if where or ends_:
            pics = "".join(locator_svg(boards[b], conn_marks(conns, cids), PAGE) for b, cids in where.items())
            lines = "".join(f"<li><b>{e(n)}</b>{'. ' + e(d[0].upper() + d[1:]) + '.' if d else ''}</li>" for n, d in ends_)
            out.append(f'<div class="where">{"<figure>" + pics + "</figure>" if pics else ""}<div><ul>{lines}</ul></div></div>')
        fs = facts(cab)
        if fs:
            out.append('<dl class="facts">' + "".join(f"<dt>{e(k)}</dt><dd>{e(str(v))}</dd>" for k, v in fs) + "</dl>")
        notes = cable_notes(cab, conns)
        if notes:
            out.append('<ul class="cnotes">' + "".join(f"<li>{e(n)}</li>" for n in notes) + "</ul>")
        if cab.get("spec"):                      # reference detail: there for whoever needs it, folded away for everyone else
            out.append(f'<details class="more spec"><summary>{e(cab.get("spec_title", "Details"))}</summary><dl class="facts">'
                       + "".join(f"<dt>{e(str(k))}</dt><dd>{e(str(v))}</dd>" for k, v in cab["spec"].items()) + "</dl></details>")
        if cab.get("parts"):
            pend = any((parts.get(p_["id"]) or {}).get("_pending") for p_ in cab["parts"])
            out.append(f'<details class="more"><summary>What to buy ({len(cab["parts"])})</summary><ul class="cparts" style="border-top:0;padding-top:0;margin-top:4px">'
                       + "".join(f'<li><span class="q">{e(str(p_.get("qty", "")))} ×</span> {part_html(p_["id"], parts)}</li>' for p_ in cab["parts"]) + "</ul>"
                       + ('<p class="nophoto">These are not in the robot\'s parts list yet.</p>' if pend else "") + "</details>")
        out.append(photos_html(h, boards, repo, out_dir, cab["id"], cab))
        out.append("</article>")
    out.append("</div></section>")
    if h.get("bundles"):
        cabs = {c["id"]: c for c in h["cables"]}
        out.append('<section><h2>Bundles</h2><p class="muted">Cables that run the same way, tied into one trunk. The ruler shows how far along the trunk '
                   'each cable leaves; each tail shows how much further it runs. A first sketch: real routes will come from the robot\'s CAD.</p>'
                   '<div class="cablecards" style="margin-top:12px">')
        for bd in h["bundles"]:
            out.append(f'<article class="cablecard" id="bundle-{e(bd["id"])}"><header><div><h3>{e(bd["title"])}</h3><p>{e(bd["what"])}</p></div>'
                       f'<span class="chip">{len(bd["branches"])} cables</span></header><div class="cdraw">{bundle_svg(bd, h, PAGE)}</div>')
            rows = "".join(f'<tr><td>{e(cabs[x["cable"]]["title"])}</td><td>{x["at_mm"]:g} mm</td><td>{x["tail_mm"]:g} mm</td><td><b>{x["at_mm"] + x["tail_mm"]:g} mm</b></td></tr>'
                           for x in sorted(bd["branches"], key=lambda x: x["at_mm"]))
            out.append('<table class="shots lens"><tr><th>Cable</th><th>In the trunk</th><th>Tail</th><th>Cut length</th></tr>' + rows + "</table>"
                       + ('<p class="nophoto">Example numbers. Nobody has measured these.</p>' if bd.get("example") else "") + "</article>")
        out.append("</div></section>")
    wired = {c["src"] for c in conns.values() if c["src"] and any(e_ and conns[e_[0]] is c for cab in h["cables"] for a, b, _ in cab["_wires"] for e_ in (a, b))}
    for b in used_boards:
        free = [r for r in boards[b]["connectors"] if (b, r) not in wired]
        if free:
            out.append(f'<section class="panel"><h2>{e(label(b))} connectors with no cable on this page</h2><div class="unused">'
                       + "".join(f'<span class="chip">{e(r + " " if boards[b].get("refs_on_board") else "")}{e(str(boards[b]["connectors"][r].get("name", r)))}</span>' for r in free) + "</div></section>")
    for b in used_boards:
        buttons, left = boards[b].get("buttons") or {}, boards[b].get("left_off") or []
        located = bool(boards[b].get("outline"))
        if buttons:
            # Numbered on the picture and in the list: three names side by side do not fit over three buttons.
            pic = f'<figure>{locator_svg(boards[b], [(str(i + 1), v["box"]) for i, v in enumerate(buttons.values())], PAGE)}</figure>' if located else ""
            lines = "".join(f'<li><b>{e(v["name"])}.</b> {e(v["does"])}</li>' for r, v in buttons.items())
            out.append(f'<section class="panel"><h2>Buttons on the {e(label(b).lower())}</h2><div class="where" style="border:0;padding:0;margin:0">{pic}<div><ol>{lines}</ol></div></div></section>')
        if left:
            marks = []
            for n, o in enumerate(left):                 # a group's number, once per place the group has parts
                seen = []
                for bx in o.get("boxes") or []:
                    c_ = ((bx[0] + bx[2]) / 2, (bx[1] + bx[3]) / 2)
                    near = any(abs(c_[0] - q[0]) + abs(c_[1] - q[1]) < 12 for q in seen)
                    marks.append(("" if near else str(n + 1), bx))
                    seen.append(c_)
            pic = f'<figure>{locator_svg(boards[b], marks, PAGE)}</figure>' if located and marks else ""
            lines = "".join(f'<li><b>{e(o["what"])}</b> {e(o["leave"])} {e(o["fit"])} <span class="muted">({e(", ".join(o["refs"]))})</span></li>' for o in left)
            out.append(f'<section class="panel"><h2>Left off the {e(label(b).lower())} on purpose</h2><p class="muted">These parts are not fitted. '
                       f'Each can be added later with a soldering iron.</p><div class="where" style="border:0;padding:0;margin:0">{pic}<div><ol>{lines}</ol></div></div></section>')
    if repo:
        slots = photo_slots(h, boards)
        want = [(stem, shoot) for _, stem, _, shoot in slots if not find_photo(repo, stem)]
        if want:
            out.append(f'<section class="panel"><h2>Photos to take</h2><p class="muted">{len(slots) - len(want)} of {len(slots)} taken. '
                       f'Take them as you build, before the cables are tucked away. Save each as a .jpg under the name shown, in docs/assets/wiring/photos/.</p>'
                       f'<details class="more"><summary>The list ({len(want)})</summary><table class="shots"><tr><th>Save as</th><th>Photograph</th></tr>'
                       + "".join(f"<tr><td>{e(stem)}.jpg</td><td>{e(shoot)}</td></tr>" for stem, shoot in want) + "</table></details></section>")
    if used_boards:
        out.append('<section class="panel"><details class="more"><summary>Where the pin data comes from</summary><dl class="facts">'
                   + "".join(f'<dt>{e(label(b))}</dt><dd>revision {e(str(boards[b].get("rev", "?")))} · <span class="muted">{e(str(boards[b].get("source", "")))}</span></dd>'
                             for b in used_boards) + "</dl></details></section>")
    out.append("</main>")
    return "\n".join(out) + "\n"


def find_root(arg):
    if arg:
        return os.path.abspath(arg)
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return here if os.path.isdir(os.path.join(here, "hardware", "harness")) else os.getcwd()


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", help="the repository root (default: this script's repository, else the current directory)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--inject", action="store_true")
    g.add_argument("--check", action="store_true")
    g.add_argument("--html", metavar="DIR")
    g.add_argument("--photos", action="store_true")
    args = ap.parse_args()
    repo = Repo(find_root(args.root))

    errors, warnings, stale = [], [], []
    parts = load_parts(repo)
    pending = load_pending(repo, parts, errors)
    shown = {**pending, **parts}                 # what a card may show; only `parts` counts for a harness on the robot
    boards = load_boards(repo, errors)
    harnesses = []
    for fn in sorted(os.listdir(repo.harness)) if os.path.isdir(repo.harness) else []:
        if fn.endswith(".yaml"):
            path = os.path.join(repo.harness, fn)
            h = yaml.safe_load(open(path, encoding="utf-8")) or {}
            validate(h, path, boards, parts, errors, warnings, pending, repo.root)
            harnesses.append(h)
    for w in warnings:
        print("WARNING " + w)
    if errors:
        print("\n".join("ERROR " + x for x in errors))
        sys.exit(1)

    if args.photos:
        # The shot list, in the form of a photos.manifest: put your photo's file name in the first column.
        for h in harnesses:
            slots = photo_slots(h, boards)
            want = [(stem, shoot) for _, stem, _, shoot in slots if not find_photo(repo, stem)]
            print(f"# {h['assembly']} ({h['status']}): {len(slots) - len(want)} of {len(slots)} photos are in docs/assets/wiring/photos/")
            for stem, shoot in want:
                print(f"YOUR_PHOTO.HEIC   {stem}.jpg   # {shoot}")
        return

    if args.html:
        os.makedirs(args.html, exist_ok=True)
        for h in harnesses:
            out = os.path.join(args.html, f"{h['assembly']}.html")
            open(out, "w", encoding="utf-8").write(page_html(h, boards, shown, repo, os.path.abspath(args.html)))
            print("wrote", out)
        return

    pages = {}
    for h in harnesses:
        if h["status"] != "robot":
            continue
        for name, svg in drawings(h, boards).items():
            svg_path = os.path.join(repo.svg, name)
            cur = open(svg_path, encoding="utf-8").read() if os.path.exists(svg_path) else None
            if cur != svg:
                if args.inject:
                    os.makedirs(repo.svg, exist_ok=True)
                    open(svg_path, "w", encoding="utf-8").write(svg)
                else:
                    stale.append(os.path.relpath(svg_path, repo.root))
        page = os.path.join(repo.root, h["page"])
        text = pages.get(page) or open(page, encoding="utf-8").read()
        m = next((m for m in MARKER.finditer(text) if m.group("key") == h["assembly"]), None)
        if not m:
            print(f"ERROR {h['page']}: no <!-- WIRING-AUTO:{h['assembly']} --> block")
            sys.exit(1)
        body = block(h, shown, repo, boards)
        if m.group("body") != body:
            text = text[: m.start("body")] + body + text[m.end("body"):]
            stale.append(h["page"])
        pages[page] = text
    if args.inject:
        for page, text in pages.items():
            if open(page, encoding="utf-8").read() != text:
                open(page, "w", encoding="utf-8").write(text)
                print("wrote", os.path.relpath(page, repo.root))
        print("OK")
        return
    if args.check:
        if stale:
            print("STALE (run harness.py --inject): " + ", ".join(dict.fromkeys(stale)))
            sys.exit(1)
        print(f"OK: {len(harnesses)} assembl{'y' if len(harnesses) == 1 else 'ies'}, "
              f"{sum(len(h['cables']) for h in harnesses)} cable(s), {len(boards)} board pin file(s)")
        return
    for h in harnesses:
        print(f"{h['assembly']} ({h['status']}): " + ", ".join(c["id"] for c in h["cables"]))


if __name__ == "__main__":
    main()
