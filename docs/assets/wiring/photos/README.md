# Cable photos

Photos of the robot's cables, taken by whoever builds the next unit. Nothing is added to the
wiring file: the photo's name says which cable card it belongs on.

Every cable has three places for a photo: the finished cable laid flat, and each end plugged in.
Every board setting that is drawn has one. To see which are still missing, and the name to save
each one under:

```sh
uv run --with pyyaml python tools/harness.py --photos
```

It prints one line per missing photo in the form the photo converter reads
(`SOURCE.HEIC   target.jpg   # what to photograph`), so the list can be pasted into a
`photos.manifest` and converted with `tools/convert-build-photos.sh`, with this folder,
`docs/assets/wiring/photos`, as the converter's output folder (see
[`docs/assets/build/README.md`](../../build/README.md)).

Then put the photos on their cards, and commit the page together with the photos:

```sh
uv run --with pyyaml python tools/harness.py --inject
```

A photo does not appear by itself. Until `--inject` has run, `harness.py --check` reports the
page as STALE, and CI fails on that.

Take them before the cables are tied down or covered: the ends are the part that cannot be
photographed afterwards. A plain background and a ruler in the frame for the laid-flat shot.

Names, where `<conn-id>` is the connector's id in the wiring file
(`hardware/harness/<assembly>.yaml`, under `connectors:`):

| name | what it shows | example |
|---|---|---|
| `<assembly>-<cable>.jpg` | the finished cable, laid flat | `rc-receiver-pigtail.jpg` |
| `<assembly>-<cable>-<conn-id>.jpg` | that end plugged in | `rc-receiver-pigtail-blue.jpg` |
| `<assembly>-set-<ref>.jpg` | a jumper or solder bridge as it should look | |

`.jpg`, `.jpeg`, `.png` and `.webp` are found.
