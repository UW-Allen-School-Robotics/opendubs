#!/usr/bin/env python3
"""The speech layer's share of the voice's no-GPL rule, for the architect image's build.

    speech_no_gpl.py BEFORE.txt      exit 0 if clean, 1 with every finding listed

BEFORE.txt names the Python distributions installed before the speech layer (`pip list
--format=freeze | cut -d= -f1`). Refused: a GPL or unreviewed LGPL distribution among those the
layer added, a banned one (phonemizer, espeakng-loader) anywhere, and espeak-ng on disk or from
apt. It uses the voice service's own check (software/voice/image/check_no_gpl.py, copied beside
it at build time), so the two cannot disagree on what counts.

The base's own distributions are not judged: archit-perception:dev ships ultralytics (AGPL-3.0)
and ROS tooling under the GPL, which is the perception image's matter, and the whole-image check
would refuse it before the speech layer added anything.
"""
import importlib.metadata as md
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_no_gpl as c  # noqa: E402


def main(before_path: str) -> int:
    with open(before_path, encoding="utf-8") as f:
        before = {c.canonical(line.strip()) for line in f if line.strip()}
    added = [d for d in md.distributions() if c.canonical(d.metadata["Name"] or "") not in before]
    banned = [d for d in md.distributions() if c.canonical(d.metadata["Name"] or "") in c.BANNED]
    found = c.check_distributions(added + banned)
    found += c.check_files(c.python_paths() + ["/usr/lib", "/usr/local/lib", "/usr/share", "/opt"])
    found += c.check_dpkg()
    print(f"speech_no_gpl: {len(added)} distributions added by the speech layer")
    if found:
        print("speech_no_gpl: REFUSED. The voice ships no GPL code:")
        for x in sorted(set(found)):
            print("  - " + x)
        return 1
    print("speech_no_gpl: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
