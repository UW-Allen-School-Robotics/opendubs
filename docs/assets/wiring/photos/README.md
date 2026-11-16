# Cable photos

Photos of the robot's cables, taken by whoever builds the next unit. A photo saved here under the
right name appears on its cable card with no other edit.

Every cable has three places for a photo: the finished cable laid flat, and each end plugged in.
Every board setting that is drawn has one. To see which are still missing, and the name to save
each one under:

```sh
uv run --with pyyaml python tools/harness.py --photos
```

It prints one line per missing photo in the form the photo converter reads
(`SOURCE.HEIC   target.jpg   # what to photograph`), so the list can be pasted into a
`photos.manifest` and converted with `tools/convert-build-photos.sh` (see
[`docs/assets/build/README.md`](../../build/README.md)).

Take them before the cables are tied down or covered: the ends are the part that cannot be
photographed afterwards. A plain background and a ruler in the frame for the laid-flat shot.

Names: `<assembly>-<cable>.jpg`, `<assembly>-<cable>-<end>.jpg`, `<assembly>-set-<ref>.jpg`.
`.jpg`, `.jpeg`, `.png` and `.webp` are found.
