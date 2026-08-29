#!/usr/bin/env python3
"""Convert + resize the DESIGNATED build photos into a module's web-ready assets
folder. Cross-platform twin of convert-build-photos.sh (which needs macOS `sips`) —
this one runs anywhere Python does, and reads the SAME manifest format.

ONLY files listed in the manifest are processed — that manifest is how you designate
which of the many shots on Drive actually make the guide, and what each becomes.

Workflow:
  1. Download the module's raw photos (e.g. "1 - Base Frame Build") to a staging dir.
  2. Edit the manifest: one "SOURCE.HEIC  target.jpg" line per KEPT photo (see below).
  3. Run this — converted/resized JPGs land in docs/assets/build/<module>/.

Usage:
  python3 tools/convert_build_photos.py <staging-dir> <manifest> <out-dir>
Example:
  python3 tools/convert_build_photos.py ~/Downloads/base-frame-raw \
      docs/assets/build/base-frame/photos.manifest \
      docs/assets/build/base-frame

Manifest format (whitespace-separated, '#' comments and blank lines ignored):
  IMG_1480.HEIC   completed-01.jpg
  IMG_1490.HEIC   01-frame-rectangle.jpg

HEIC support needs `pillow-heif` (pip install pillow pillow-heif). Plain JPG/PNG
sources only need Pillow. Longest edge defaults to 1600 px — override with MAXPX.

Each output is held under MAXKB (default 300, per docs/assets/build/README.md) by
stepping quality down from QUALITY until it fits. Set MAXKB=0 to disable and encode
at QUALITY flat.

Re-encoding drops most camera metadata, including GPS. Photos shot in a lab still
carry EXIF in the ORIGINALS on Drive — this only scrubs the copies in the repo.
"""

import io
import os
import sys

try:
    from PIL import Image, ImageOps
except ImportError:  # reported in main() so --help still works without Pillow
    Image = ImageOps = None

MAXPX = int(os.environ.get("MAXPX", "1600"))
QUALITY = int(os.environ.get("QUALITY", "88"))
MAXKB = int(os.environ.get("MAXKB", "300"))
# Floors the budget search will not push past, so a photo degrades visibly rather
# than silently. Both are overridable for the occasional shot that busts the budget
# even here — a full-frame carpet background is the usual culprit.
MIN_QUALITY = int(os.environ.get("MIN_QUALITY", "68"))
MIN_PX = int(os.environ.get("MIN_PX", "1200"))


def _try(img, quality, fmt):
    buf = io.BytesIO()
    if fmt == "WEBP":
        img.save(buf, "WEBP", quality=quality, method=6)
    else:
        img.save(buf, "JPEG", quality=quality, optimize=True)
    return buf


def encode(img, path):
    """Save under MAXKB, in the format implied by the target extension.

    Returns (bytes, quality, longest_edge). Steps quality down first, then shrinks —
    busy shots (carpet, cluttered benches) hit the quality floor while still
    oversize, and a slightly smaller photo reads better than a mushy one at full
    size. WebP usually fits at full size where JPEG cannot.
    """
    fmt = "WEBP" if path.lower().endswith(".webp") else "JPEG"
    budget = MAXKB * 1024
    while True:
        quality = QUALITY
        while True:
            buf = _try(img, quality, fmt)
            if MAXKB <= 0 or buf.tell() <= budget or quality <= MIN_QUALITY:
                break
            quality -= 4
        if MAXKB <= 0 or buf.tell() <= budget or max(img.size) <= MIN_PX:
            with open(path, "wb") as fh:
                fh.write(buf.getvalue())
            return buf.tell(), quality, max(img.size)
        img = img.resize(
            (round(img.width * 0.9), round(img.height * 0.9)), Image.LANCZOS
        )


def main(argv):
    if len(argv) != 4:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    staging, manifest, out = argv[1], argv[2], argv[3]

    if Image is None:
        print("Pillow not installed — run: pip install pillow pillow-heif", file=sys.stderr)
        return 1
    try:
        import pillow_heif

        pillow_heif.register_heif_opener()
    except ImportError:
        pillow_heif = None  # only fatal if the manifest actually lists a HEIC

    if not os.path.isdir(staging):
        print(f"staging dir not found: {staging}", file=sys.stderr)
        return 1
    if not os.path.isfile(manifest):
        print(f"manifest not found: {manifest}", file=sys.stderr)
        return 1
    os.makedirs(out, exist_ok=True)

    n = miss = fail = 0
    with open(manifest, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # Split at the LAST whitespace run: source paths routinely contain
            # spaces ("Motor Wire Configuration /1.jpg" — Drive folders are named
            # in prose) but target names never do. Splitting on the first run
            # instead would silently truncate such a source to its first word.
            parts = line.rsplit(None, 1)
            if len(parts) < 2:
                print(f"  BAD LINE {line}", file=sys.stderr)
                fail += 1
                continue
            src, dst = parts[0].strip(), parts[1].strip()

            in_path = os.path.join(staging, src)
            if not os.path.isfile(in_path):
                print(f"  MISSING  {src}", file=sys.stderr)
                miss += 1
                continue
            if src.lower().endswith((".heic", ".heif")) and pillow_heif is None:
                print(f"  NO HEIC  {src} — run: pip install pillow-heif", file=sys.stderr)
                fail += 1
                continue

            try:
                # exif_transpose applies the camera's rotation flag, then the save
                # below drops EXIF — without it, phone shots land sideways.
                img = ImageOps.exif_transpose(Image.open(in_path)).convert("RGB")
                img.thumbnail((MAXPX, MAXPX), Image.LANCZOS)
                size, quality, edge = encode(img, os.path.join(out, dst))
            except Exception as exc:  # noqa: BLE001 — report and keep going
                print(f"  FAILED   {src}: {exc}", file=sys.stderr)
                fail += 1
                continue

            over = "  OVER BUDGET" if MAXKB > 0 and size > MAXKB * 1024 else ""
            print(
                f"  ok       {src}  ->  {dst}  "
                f"({size // 1024} KB, q{quality}, {edge}px){over}"
            )
            n += 1

    tail = "".join(
        [f"  ({miss} missing)" if miss else "", f"  ({fail} failed)" if fail else ""]
    )
    print(f"converted {n} file(s) into {out}{tail}")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
