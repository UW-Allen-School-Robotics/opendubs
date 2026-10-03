#!/usr/bin/env python3
"""Keep the dashboard's ?v= cache-busting hashes honest.

index.html references its assets as `app.js?v=d9b97880`. The query string exists so a tablet that
has the dashboard open picks up a new build instead of serving a cached one -- but the hashes were
maintained BY HAND, which means the failure mode is silent and misleading: edit the CSS, forget the
hash, and the browser keeps the old file while the source on disk looks correct. That cost real
time on 2026-10-03, with a new page laid out correctly in the file and rendering wrong on screen.

This recomputes each hash from the file's own contents, so a changed file cannot keep a stale tag.

Usage:
  tools/cachebust.py            rewrite any stale ?v= hashes in index.html
  tools/cachebust.py --check    exit 1 if any are stale (for CI)
"""
from __future__ import annotations

import argparse
import hashlib
import pathlib
import re
import sys

FRONTEND = pathlib.Path(__file__).resolve().parent.parent / "software/kiosk-dashboard/frontend"
HTML = FRONTEND / "index.html"
# <script src="app.js?v=...">, <link href="style.css?v=...">
REF = re.compile(r'((?:src|href)=")([A-Za-z0-9_./-]+)\?v=([0-9a-zA-Z]+)(")')
# ES module imports: `from "./armview.js?v=..."`. These bypass the HTML entirely -- a module
# graph is fetched by the browser, not by a tag -- so without this a changed armview.js keeps
# being served from cache while index.html looks freshly busted.
IMP = re.compile(r'(from\s+["\'])(\.{1,2}/[A-Za-z0-9_./-]+)\?v=([0-9a-zA-Z]+)(["\'])')


def digest(path: pathlib.Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()[:8]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    stale, missing, seen = [], [], 0
    changed = []            # accumulated across passes; `stale` is per-pass

    def rewrite(path, pattern, base):
        nonlocal seen
        text = path.read_text()

        def sub(m):
            nonlocal seen
            pre, rel, old, close = m.groups()
            target = (base / rel).resolve()
            if not target.exists():
                missing.append(rel)
                return m.group(0)
            seen += 1
            new = digest(target)
            if new != old:
                stale.append((f"{path.name} -> {rel}", old, new))
            return f"{pre}{rel}?v={new}{close}"

        out = pattern.sub(sub, text)
        return text, out

    # A module's own import tags are part of its bytes, so rewriting one changes its hash and
    # therefore the tag that points AT it. Iterate to a fixpoint rather than assuming one pass.
    pending = {}
    for _ in range(6):
        stale.clear(); missing.clear(); seen = 0
        pending.clear()
        for js in sorted(FRONTEND.glob("*.js")):
            before, after = rewrite(js, IMP, js.parent)
            if before != after:
                pending[js] = after
        before, after = rewrite(HTML, REF, FRONTEND)
        if before != after:
            pending[HTML] = after
        changed.extend(stale)
        if not pending or args.check:
            break
        for path, body in pending.items():
            path.write_text(body)
    updated = pending.get(HTML, HTML.read_text())

    if missing:
        sys.exit("referenced but missing: " + ", ".join(sorted(set(missing))))

    if args.check:
        if stale:
            for rel, old, new in stale:
                print(f"  STALE {rel}: tagged {old}, content is {new}", file=sys.stderr)
            sys.exit(f"{len(stale)} stale cache-busting hash(es) -- run tools/cachebust.py")
        print(f"OK -- {seen} asset reference(s) match their contents")
        return

    for rel, old, new in changed:
        print(f"  {rel}: {old} -> {new}")
    print(f"{len(changed)} updated, {seen} reference(s) checked")


if __name__ == "__main__":
    main()
