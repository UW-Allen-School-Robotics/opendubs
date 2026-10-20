#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = ["pyyaml"]
# ///
"""OpenDubs troubleshooting registry — generator and validator.

The registry is docs/reference/registry/*.yaml: one file of entries per lane, plus
symptoms.yaml (the symptom groups the page is organised by) and glossary.yaml. The
troubleshooting page and the glossary are *views* of it, generated between markers:

    <!-- FAULTS-AUTO:<key> -->
    ...generated...
    <!-- /FAULTS-AUTO:<key> -->

Keys: troubleshooting (docs/reference/troubleshooting.md) | glossary (docs/reference/glossary.md)

It also writes software/voice/fault_help.json, a plain view of every fault and warning
({CODE: {title, means, do}}), so the robot's voice can read an active fault's help aloud from
the same words. Generated: never edit it by hand. --check fails if it is stale.

Usage:
  tools/faults.py             validate, then list the codes by symptom
  tools/faults.py --inject    regenerate every FAULTS-AUTO block (writes)
  tools/faults.py --check     validate + verify the pages are current + verify every code the
                              source uses is registered; exit 1 if not

Needs PyYAML, which arrives with MkDocs. Anywhere else, `uv run tools/faults.py` installs it.

A code is "used" when the source names it where the scan can see it:
  refuse("CODE", ...)       the dashboard backend's refusal helper
  helpLink("CODE"), setHelp(el, "CODE"), openHelp("CODE")
                            the dashboard frontend's "?" — any quoted code on the call's line,
                            so `setHelp(el, bad ? "CODE" : null)` counts
  @fault CODE               an annotation, for any language (shell, C++, a comment)
"""
import argparse, json, os, re, sys

try:
    import yaml
except ImportError:
    sys.exit("faults.py needs PyYAML (it comes with MkDocs). Install it, or run: uv run tools/faults.py")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, "docs")
REG_DIR = os.path.join(DOCS, "reference", "registry")
VOICE_HELP = os.path.join(ROOT, "software", "voice", "fault_help.json")
VOICE_KINDS = ("fault", "warning")
PAGES = {
    "troubleshooting": os.path.join(DOCS, "reference", "troubleshooting.md"),
    "glossary": os.path.join(DOCS, "reference", "glossary.md"),
}
SCAN_ROOTS = ["software", "firmware"]
SCAN_SKIP_DIRS = {".git", ".pio", ".venv", "venv", "node_modules", "vendor", "build",
                  "managed_components", "__pycache__", "gradle", ".gradle"}
SCAN_EXTS = {".py", ".js", ".sh", ".cpp", ".c", ".h", ".hpp", ".kt", ""}

MARKER = re.compile(
    r"(<!-- FAULTS-AUTO:(?P<key>[a-z-]+) -->\n)(?P<body>.*?)(<!-- /FAULTS-AUTO:(?P=key) -->)", re.S)
CALL = re.compile(r"\b(?:refuse|helpLink|setHelp|openHelp)\(")
QUOTED = re.compile(r"""["']([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)["']""")
ANNOTATION = re.compile(r"@fault\s+([A-Z][A-Z0-9_]*)")

PREFIXES = ("ESTOP", "CONTACTOR", "RC", "DRIVE", "LINK", "POWER", "LIFT", "HEAD", "ARM", "SHOW",
            "VOICE")
CODE_RE = re.compile(r"^[A-Z][A-Z0-9]*(_[A-Z0-9]+)+$")
ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
KINDS = {"refusal": "Refused action", "fault": "Fault", "warning": "Warning",
         "info": "Information", "expected": "Expected behaviour"}
SEEN_ON = {"dashboard": "the dashboard", "head": "the head", "transmitter": "the transmitter",
           "lights": "the status lights", "log": "the logs", "robot": "the robot itself"}
ENTRY_KEYS = {"code", "title", "symptom", "rank", "kind", "seen_on", "means", "do", "see", "derived"}
SYMPTOM_KEYS = {"id", "title", "intro"}
TERM_KEYS = {"term", "anchor", "aka", "means", "see"}
SPECIAL_FILES = {"symptoms.yaml", "glossary.yaml"}


def anchor(code):
    """The page anchor for a code — the contract the dashboard links to."""
    return code.lower().replace("_", "-")


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _as_list(v):
    return v if isinstance(v, list) else [v]


def _read_yaml(path, top):
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict) or set(data) - {top}:
        raise ValueError(f"{os.path.relpath(path, ROOT)}: expected a single top-level key '{top}:'")
    items = data.get(top) or []
    if not isinstance(items, list):
        raise ValueError(f"{os.path.relpath(path, ROOT)}: '{top}' must be a list")
    return items


def load():
    """Returns (symptoms, entries, terms). Each entry carries `_file` (its owner's file)."""
    symptoms = _read_yaml(os.path.join(REG_DIR, "symptoms.yaml"), "symptoms")
    terms = _read_yaml(os.path.join(REG_DIR, "glossary.yaml"), "terms")
    entries = []
    for name in sorted(os.listdir(REG_DIR)):
        if not name.endswith(".yaml") or name in SPECIAL_FILES:
            continue
        for e in _read_yaml(os.path.join(REG_DIR, name), "entries"):
            if isinstance(e, dict):
                e["_file"] = name
            entries.append(e)
    return symptoms, entries, terms


def _see_ok(target, errs, where):
    path, _, _frag = target.partition("#")
    if not path.endswith(".md") or not os.path.isfile(os.path.join(DOCS, path)):
        errs.append(f"{where}: see '{target}' is not a page under docs/")


def validate(symptoms, entries, terms):
    errs = []
    sym_ids = []
    for i, s in enumerate(symptoms):
        where = f"symptoms.yaml #{i + 1}"
        if not isinstance(s, dict):
            errs.append(f"{where}: not a mapping"); continue
        for k in set(s) - SYMPTOM_KEYS:
            errs.append(f"{where}: unknown field '{k}'")
        if not ID_RE.match(str(s.get("id", ""))):
            errs.append(f"{where}: id must be lower-case-with-hyphens")
        if not s.get("title"):
            errs.append(f"{where}: missing title")
        if s.get("id") in sym_ids:
            errs.append(f"{where}: duplicate id '{s['id']}'")
        sym_ids.append(s.get("id"))

    seen = {}
    for e in entries:
        if not isinstance(e, dict):
            errs.append("an entry is not a mapping"); continue
        code = str(e.get("code", ""))
        where = f"{e['_file']} {code or '(no code)'}"
        for k in set(e) - ENTRY_KEYS - {"_file"}:
            errs.append(f"{where}: unknown field '{k}'")
        if not CODE_RE.match(code):
            errs.append(f"{where}: code must be UPPER_SNAKE with at least two parts")
        elif code.split("_")[0] not in PREFIXES:
            errs.append(f"{where}: prefix '{code.split('_')[0]}' is not one of {', '.join(PREFIXES)}")
        if code in seen:
            errs.append(f"{where}: duplicate — also defined in {seen[code]}")
        seen[code] = e["_file"]
        for k in ("title", "means"):
            if not e.get(k):
                errs.append(f"{where}: missing {k}")
        if not isinstance(e.get("do"), list) or not e["do"]:
            errs.append(f"{where}: 'do' must be a non-empty list of steps")
        elif not all(isinstance(d, str) for d in e["do"]):
            # An unquoted step with ": " in it parses as a mapping and renders as a Python dict.
            errs.append(f"{where}: every 'do' step must be text — quote a step that contains ': '")
        for s in _as_list(e.get("symptom")):
            if s not in sym_ids:
                errs.append(f"{where}: unknown symptom '{s}' (see symptoms.yaml)")
        if not isinstance(e.get("rank", 50), int):
            errs.append(f"{where}: rank must be an integer")
        if e.get("kind", "refusal") not in KINDS:
            errs.append(f"{where}: kind must be one of {', '.join(KINDS)}")
        for s in e.get("seen_on", []):
            if s not in SEEN_ON:
                errs.append(f"{where}: seen_on '{s}' is not one of {', '.join(SEEN_ON)}")
        for t in e.get("see", []):
            _see_ok(t, errs, where)
        if not isinstance(e.get("derived", False), bool):
            errs.append(f"{where}: derived must be true or false")

    anchors = set()
    for t in terms:
        if not isinstance(t, dict):
            errs.append("glossary.yaml: a term is not a mapping"); continue
        where = f"glossary.yaml {t.get('term', '(no term)')}"
        for k in set(t) - TERM_KEYS:
            errs.append(f"{where}: unknown field '{k}'")
        if not t.get("term") or not t.get("means"):
            errs.append(f"{where}: needs term and means")
            continue
        a = t.get("anchor") or slug(str(t["term"]))
        if a in anchors:
            errs.append(f"{where}: duplicate anchor '{a}'")
        anchors.add(a)
        for s in t.get("see", []):
            _see_ok(s, errs, where)
    return errs


# ---------- rendering --------------------------------------------------------------

def _page_title(rel):
    try:
        with open(os.path.join(DOCS, rel), encoding="utf-8") as f:
            for line in f:
                if line.startswith("# "):
                    return line[2:].strip()
    except OSError:
        pass
    return rel


def _see_line(targets):
    links = []
    for t in targets:
        path, _, frag = t.partition("#")
        rel = os.path.relpath(os.path.join(DOCS, path), os.path.join(DOCS, "reference"))
        links.append(f"[{_page_title(path)}]({rel}{'#' + frag if frag else ''})")
    return "**See also:** " + " · ".join(links)


def _numbered(steps):
    out = []
    for i, step in enumerate(steps, 1):
        lines = str(step).strip().splitlines()
        out.append(f"{i}. {lines[0]}")
        out.extend(("   " + l) if l.strip() else "" for l in lines[1:])
    return out


def _entry(e):
    code = e["code"]
    meta = [f"`{code}`", KINDS[e.get("kind", "refusal")]]
    if e.get("seen_on"):
        meta.append("seen on " + ", ".join(SEEN_ON[s] for s in e["seen_on"]))
    out = [f"### {e['title']} {{#{anchor(code)}}}", "", " · ".join(meta), "",
           str(e["means"]).strip(), "", "**What to do**", ""]
    out += _numbered(e["do"])
    if e.get("see"):
        out += ["", _see_line(e["see"])]
    return out


def render_troubleshooting(symptoms, entries):
    by_sym = {s["id"]: [] for s in symptoms}
    for e in entries:
        for s in _as_list(e["symptom"]):
            by_sym[s].append(e)
    home = {}                       # code -> the first symptom (in page order) it is written under
    for s in symptoms:
        for e in by_sym[s["id"]]:
            home.setdefault(e["code"], s["id"])
    out = []
    for s in symptoms:
        group = sorted(by_sym[s["id"]], key=lambda e: (e.get("rank", 50), e["code"]))
        if not group:
            continue            # a symptom no lane has filled yet is not published empty
        out += [f"## {s['title']} {{#{s['id']}}}", ""]
        if s.get("intro"):
            out += [str(s["intro"]).strip(), ""]
        out += ["Check in this order:", ""]
        out += [f"{i}. [{e['title']}](#{anchor(e['code'])})" for i, e in enumerate(group, 1)]
        out.append("")
        for e in group:
            if home[e["code"]] == s["id"]:
                out += _entry(e) + [""]
    return "\n".join(out).rstrip() + "\n"


def render_glossary(terms):
    out = []
    for t in sorted(terms, key=lambda t: str(t["term"]).lower()):
        a = t.get("anchor") or slug(str(t["term"]))
        out += [f"### {t['term']} {{#{a}}}", ""]
        if t.get("aka"):
            out += ["*Also:* " + ", ".join(f"**{x}**" for x in t["aka"]), ""]
        out += [str(t["means"]).strip(), ""]
        if t.get("see"):
            out += [_see_line(t["see"]), ""]
    return "\n".join(out).rstrip() + "\n"


def render_voice_help(entries):
    """The help text of every fault and warning, keyed by code, for the voice to read aloud."""
    out = {}
    for e in entries:
        if e.get("kind", "refusal") in VOICE_KINDS:
            out[e["code"]] = {"title": str(e["title"]).strip(),
                              "means": " ".join(str(e["means"]).split()),
                              "do": [" ".join(str(d).split()) for d in e["do"]]}
    return json.dumps(out, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def process_pages(symptoms, entries, terms, write):
    """Regenerate (write=True) or compare (write=False). Returns a list of problems."""
    bodies = {"troubleshooting": render_troubleshooting(symptoms, entries),
              "glossary": render_glossary(terms)}
    problems = []
    for key, path in PAGES.items():
        rel = os.path.relpath(path, ROOT)
        if not os.path.isfile(path):
            problems.append(f"{rel}: missing"); continue
        with open(path, encoding="utf-8") as f:
            src = f.read()
        found = [m for m in MARKER.finditer(src) if m.group("key") == key]
        if len(found) != 1:
            problems.append(f"{rel}: needs exactly one FAULTS-AUTO:{key} block"); continue
        new = MARKER.sub(lambda m: m.group(1) + bodies[key] + m.group(4)
                         if m.group("key") == key else m.group(0), src)
        if new != src:
            if write:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(new)
                print(f"updated {rel}")
            else:
                problems.append(f"{rel}: stale — run tools/faults.py --inject")
    rel = os.path.relpath(VOICE_HELP, ROOT)
    want = render_voice_help(entries)
    have = open(VOICE_HELP, encoding="utf-8").read() if os.path.isfile(VOICE_HELP) else None
    if have != want:
        if write:
            with open(VOICE_HELP, "w", encoding="utf-8") as f:
                f.write(want)
            print(f"updated {rel}")
        else:
            problems.append(f"{rel}: {'missing' if have is None else 'stale'} — run tools/faults.py --inject")
    return problems


# ---------- source scan ------------------------------------------------------------

def scan_sources():
    """Returns {code: [file:line, ...]} for every code the source names."""
    used = {}
    for top in SCAN_ROOTS:
        for dirpath, dirnames, filenames in os.walk(os.path.join(ROOT, top)):
            dirnames[:] = [d for d in dirnames if d not in SCAN_SKIP_DIRS]
            for name in filenames:
                if os.path.splitext(name)[1] not in SCAN_EXTS:
                    continue
                path = os.path.join(dirpath, name)
                try:
                    if os.path.getsize(path) > 2_000_000:
                        continue
                    with open(path, encoding="utf-8") as f:
                        lines = f.readlines()
                except (OSError, UnicodeDecodeError):
                    continue
                for n, line in enumerate(lines, 1):
                    codes = ANNOTATION.findall(line)
                    if CALL.search(line):
                        codes += [c for c in QUOTED.findall(line) if c.split("_")[0] in PREFIXES]
                    for code in codes:
                        used.setdefault(code, []).append(f"{os.path.relpath(path, ROOT)}:{n}")
    return used


def main():
    ap = argparse.ArgumentParser(description="OpenDubs troubleshooting registry generator/validator")
    ap.add_argument("--inject", action="store_true", help="regenerate FAULTS-AUTO blocks (writes)")
    ap.add_argument("--check", action="store_true",
                    help="validate + verify pages current + verify source codes registered; exit 1 if not")
    args = ap.parse_args()

    try:
        symptoms, entries, terms = load()
    except (OSError, ValueError, yaml.YAMLError) as e:
        sys.exit(f"ERROR: {e}")
    errs = validate(symptoms, entries, terms)
    if errs:
        print("\n".join("ERROR: " + e for e in errs), file=sys.stderr)
        sys.exit(1)

    if args.inject:
        problems = process_pages(symptoms, entries, terms, write=True)
        if problems:
            sys.exit("\n".join("ERROR: " + p for p in problems))
        return

    if args.check:
        problems = process_pages(symptoms, entries, terms, write=False)
        registered = {e["code"] for e in entries}
        used = scan_sources()
        for code in sorted(set(used) - registered):
            problems.append(f"{code} is used in source but not registered: {', '.join(used[code][:3])}")
        if problems:
            print("\n".join("ERROR: " + p for p in problems), file=sys.stderr)
            sys.exit(1)
        idle = sorted(c for c in registered - set(used)
                      if not next(e for e in entries if e["code"] == c).get("derived"))
        note = f", {len(idle)} not yet named by any source" if idle else ""
        print(f"OK — {len(entries)} entries, {len(terms)} terms, pages current{note}")
        return

    for s in symptoms:
        codes = sorted((e for e in entries if s["id"] in _as_list(e["symptom"])),
                       key=lambda e: (e.get("rank", 50), e["code"]))
        print(f"{s['id']}: " + (", ".join(e["code"] for e in codes) or "(none)"))


if __name__ == "__main__":
    main()
