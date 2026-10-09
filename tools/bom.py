#!/usr/bin/env python3
"""OpenDubs BOM tool — single source of truth is hardware/parts.csv.

The Purchase BOM and per-module Build BOMs are *views* of that one CSV, so they stay
aligned by construction. Doc tables are generated into the pages between markers:

    <!-- BOM-AUTO:<key> -->
    ...generated table...
    <!-- /BOM-AUTO:<key> -->

Keys: build:<module> | purchase:components | purchase:structure | purchase:fasteners |
      purchase:tools | purchase:consumables

`module` is a build page id, `shared` (tools and hardware used everywhere), or `system` (a part
with no build page of its own, such as the computer or the arms). purchase:components lists every
component, system parts first, with the same part bought for two modules counted once.

Usage:
  tools/bom.py                     validate, then print Purchase BOM + module list
  tools/bom.py --module base-frame print that module's Build BOM
  tools/bom.py --inject            regenerate every BOM-AUTO block in docs/ (writes)
  tools/bom.py --check             validate CSV + verify docs are up to date; exit 1 if not

CSV columns: id,name,type,group,sku,module,qty,need,source,notes
"""
import csv, sys, os, re, argparse
from collections import OrderedDict

ROOT = os.path.join(os.path.dirname(__file__), "..")
CSV_PATH = os.path.join(ROOT, "hardware", "parts.csv")
DOCS_DIR = os.path.join(ROOT, "docs")
VALID_TYPES = {"structure", "component", "fastener", "tool", "consumable", "printed", "fabricated"}
DISCRETE = {"structure", "component", "printed", "fabricated"}  # counted parts → qty must be an integer
# A part a build page documents because a robot has it fitted, but which a new build should not
# buy: shown on its build page, left off the shopping list.
NOT_FOR_NEW_BUILDS = "not for new builds"
MARKER = re.compile(
    r"<!-- BOM-AUTO:(?P<key>[\w:.\-]+) -->(?P<body>.*?)<!-- /BOM-AUTO:(?P=key) -->",
    re.DOTALL,
)


def load(path=CSV_PATH):
    with open(path, newline="", encoding="utf-8") as f:
        return [{k: (v or "").strip() for k, v in r.items()} for r in csv.DictReader(f)]


def _int(s):
    try:
        return int(s)
    except (TypeError, ValueError):
        return None


def _link(url, text):
    url = (url or "").strip()
    if not url:
        return "—"
    return f"[{text}]({url})" if url.startswith("http") else url


# ── validation ──────────────────────────────────────────────────────────────
def validate(rows):
    errs, warns, seen = [], [], set()
    for i, r in enumerate(rows, start=2):
        rid = r.get("id") or "?"
        if not r.get("id"):     errs.append(f"L{i}: missing id")
        if not r.get("name"):   errs.append(f"L{i}: {rid} missing name")
        if not r.get("module"): errs.append(f"L{i}: {rid} missing module")
        if r.get("type") not in VALID_TYPES:
            errs.append(f"L{i}: {rid} invalid type '{r.get('type')}'")
        key = (r.get("id"), r.get("module"))
        if key in seen:         errs.append(f"L{i}: duplicate (id, module) {key}")
        seen.add(key)
        if r.get("type") in DISCRETE and _int(r.get("qty")) is None:
            errs.append(f"L{i}: {rid} {r.get('type')} qty not an integer: '{r.get('qty')}'")
        if r.get("type") == "structure" and not r.get("sku"):
            warns.append(f"L{i}: {rid} structure has no SKU")
        if not r.get("source"):
            warns.append(f"L{i}: {rid} has no source/link")
    return errs, warns


# ── table renderers (return markdown, no surrounding headings) ───────────────
def _structure_purchase(rows):
    agg = OrderedDict()
    for r in rows:
        if r["type"] != "structure":
            continue
        a = agg.setdefault(r["id"], {"name": r["name"], "sku": r["sku"],
                                     "source": r["source"], "qty": 0})
        a["qty"] += _int(r["qty"]) or 0
    out = ["| Part | SKU | Qty | Source |", "|---|---|---:|---|"]
    for a in sorted(agg.values(), key=lambda x: x["sku"]):
        out.append(f"| {a['name']} | `{a['sku']}` | {a['qty']} | {_link(a['source'], 'ServoCity')} |")
    return "\n".join(out)


def _page_title(module):
    """The build page's title without its step number ("4 · Electronics Bay" -> "Electronics Bay")."""
    path = os.path.join(DOCS_DIR, "build", f"{module}.md")
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.startswith("# "):
                    return re.sub(r"^\d+\s*·\s*", "", line[2:].strip())
    except OSError:
        pass
    return None


def _component_purchase(rows):
    """Every component across the build, one line per distinct part (name + SKU + source), quantities
    summed over the modules that use it. `system` parts come first, in CSV order."""
    agg = OrderedDict()
    ordered = ([r for r in rows if r["module"] == "system"]
               + [r for r in rows if r["module"] != "system"])
    for r in ordered:
        if r["type"] != "component" or r["need"] == NOT_FOR_NEW_BUILDS:
            continue
        a = agg.setdefault((r["name"], r["sku"], r["source"]),
                           {"r": r, "qty": 0, "modules": []})
        a["qty"] += _int(r["qty"]) or 0
        if r["module"] not in a["modules"]:
            a["modules"].append(r["module"])
    out = ["| Part | SKU | Qty | Used in | Notes |", "|---|---|---:|---|---|"]
    for a in agg.values():
        r = a["r"]
        name = f"[{r['name']}]({r['source']})" if r["source"].startswith("http") else r["name"]
        sku = f"`{r['sku']}`" if r["sku"] else "—"
        used = []
        for m in a["modules"]:
            t = _page_title(m) if m not in ("system", "shared") else None
            used.append(f"[{t}](build/{m}.md)" if t else "—")
        out.append(f"| {name} | {sku} | {a['qty']} | {', '.join(used)} | {r['notes']} |")
    return "\n".join(out)


def _type_table(rows, typ, module=None):
    items = [r for r in rows if r["type"] == typ and (module is None or r["module"] == module)]
    out = ["| Item | Need | Qty | Source | Notes |", "|---|---|---|---|---|"]
    for r in items:
        out.append(f"| {r['name']} | {r['need'] or '—'} | {r['qty'] or '—'} | "
                   f"{_link(r['source'], 'buy')} | {r['notes']} |")
    return "\n".join(out)


def _build_tables(rows, module):
    items = [r for r in rows if r["module"] == module]
    if not items:
        return f"_(no parts for module '{module}')_"
    groups = OrderedDict()
    for r in items:
        groups.setdefault(r["group"] or "Other", []).append(r)
    blocks = []
    for gname, grows in groups.items():
        lines = [f"**{gname}**", "", "| Part | SKU | Qty | Notes |", "|---|---|---:|---|"]
        for r in grows:
            sku = f"`{r['sku']}`" if r["sku"] else "—"
            name = f"[{r['name']}]({r['source']})" if r["source"].startswith("http") else r["name"]
            lines.append(f"| {name} | {sku} | {r['qty']} | {r['notes']} |")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


# ── marker injection ─────────────────────────────────────────────────────────
def _key_type(key):
    # purchase:fasteners -> fastener, purchase:tools -> tool, etc.
    return {"fasteners": "fastener", "tools": "tool", "consumables": "consumable",
            "components": "component"}.get(key.split(":", 1)[1], key.split(":", 1)[1])


def render_dispatch(key, rows):
    if key.startswith("build:"):
        return _build_tables(rows, key.split(":", 1)[1])
    if key == "purchase:structure":
        return _structure_purchase(rows)
    if key == "purchase:components":
        return _component_purchase(rows)
    if key.startswith("purchase:"):        # every module — the complete shopping list
        return _type_table(rows, _key_type(key))
    if key.startswith("shared:"):          # only module=shared — the base kit used everywhere
        return _type_table(rows, _key_type(key), module="shared")
    return f"_(unknown BOM key '{key}')_"


def process_docs(rows, write):
    """Walk docs/, regenerate BOM-AUTO blocks. Returns list of (path, changed)."""
    results, stale = [], []
    for dirpath, _dirs, files in os.walk(DOCS_DIR):
        for fn in files:
            if not fn.endswith(".md"):
                continue
            path = os.path.join(dirpath, fn)
            src = open(path, encoding="utf-8").read()
            if "BOM-AUTO:" not in src:
                continue

            def repl(m):
                new = render_dispatch(m.group("key"), rows).strip("\n")
                return f"<!-- BOM-AUTO:{m.group('key')} -->\n{new}\n<!-- /BOM-AUTO:{m.group('key')} -->"

            new_src = MARKER.sub(repl, src)
            changed = new_src != src
            if changed:
                stale.append(os.path.relpath(path, ROOT))
                if write:
                    open(path, "w", encoding="utf-8").write(new_src)
            results.append((os.path.relpath(path, ROOT), changed))
    return results, stale


# ── CLI ──────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="OpenDubs BOM generator/validator")
    ap.add_argument("--module", help="print this module's Build BOM")
    ap.add_argument("--inject", action="store_true", help="regenerate BOM-AUTO blocks in docs/ (writes)")
    ap.add_argument("--check", action="store_true", help="validate + verify docs current; exit 1 if not")
    args = ap.parse_args()

    rows = load()
    errs, warns = validate(rows)
    for w in warns:
        print(f"warning: {w}", file=sys.stderr)
    if errs:
        for e in errs:
            print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    if args.inject:
        _res, stale = process_docs(rows, write=True)
        print(f"injected — {len(stale)} file(s) updated: {', '.join(stale) or '(none)'}", file=sys.stderr)
        return
    if args.check:
        _res, stale = process_docs(rows, write=False)
        if stale:
            print(f"ERROR: docs out of date — run `tools/bom.py --inject`: {', '.join(stale)}", file=sys.stderr)
            sys.exit(1)
        print(f"OK — {len(rows)} parts, docs current, {len(warns)} warning(s)", file=sys.stderr)
        return
    if args.module:
        print(_build_tables(rows, args.module)); return

    # default: print purchase views
    print("## Structure (aggregated)\n"); print(_structure_purchase(rows))
    for typ in ("fastener", "tool", "consumable"):
        print(f"\n## {typ.capitalize()}s\n"); print(_type_table(rows, typ))


if __name__ == "__main__":
    main()
