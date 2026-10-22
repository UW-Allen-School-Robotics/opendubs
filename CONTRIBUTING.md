# Contributing to OpenDubs

Thanks for your interest in building on OpenDubs! This is an open-hardware research project — a
low-cost, replicable mobile robot — and we welcome fixes, improvements, new parts, and better docs.

## Ways to contribute

- **Build a copy and report back** — issues you hit during assembly, flashing, or bring-up are
  incredibly valuable while the docs are still filling in.
- **Documentation** — the [`docs/`](docs/) site is the priority. Corrections, clearer steps, and
  photos from your own build all help.
- **Firmware / software** — see the subsystem READMEs under `firmware/` and `software/`.
- **Hardware** — CAD improvements, alternate parts, cost reductions.

## Developer Certificate of Origin (DCO)

We use the Developer Certificate of Origin ([`DCO.txt`](DCO.txt), version 1.1) instead of a
contributor licence agreement. It is a short statement that you wrote the contribution, or have the
right to submit it, under the project's licences. **Every commit must be signed off.** Commit with

```bash
git commit -s
```

which adds a line like this to the commit message, using your `git config user.name` and
`user.email`:

```
Signed-off-by: Your Name <your.email@example.com>
```

The sign-off must match the commit's author email. By signing off you certify the DCO for that
commit, and agree it is licensed under the project's licences (below).

A coding assistant or agent working for a maintainer commits under that maintainer's git identity,
so its commits are signed off in the maintainer's name, matching the author field. The maintainer
certifies those commits. Like `AUTHORS`, this is a deliberate exception to the rule that the repo
names no one.

**CI checks it.** The required "Hygiene checks" fails a pull request with an unsigned commit. It
checks only commits authored after the requirement started; older history is never rewritten.
Merge and squash commits that GitHub creates are exempt. To fix an unsigned commit on your branch:

```bash
git commit --amend -s --no-edit                 # the last commit
git rebase --signoff origin/main                # every commit on the branch
```

then push with `--force-with-lease`.

## Authors

"The OpenDubs Authors" named in the licences are the people listed in [`AUTHORS`](AUTHORS). You are
added only with your own agreement, by a pull request that adds your line.

## Licenses

Contributions are accepted under the license that matches the material:

- **Code / firmware** — Apache-2.0 ([`LICENSE-CODE`](LICENSE-CODE))
- **Hardware designs** — CERN-OHL-W-2.0 ([`LICENSE-HARDWARE`](LICENSE-HARDWARE))
- **Docs / media** — CC-BY-4.0 ([`LICENSE-DOCS`](LICENSE-DOCS))

## Working locally

- **Docs site:** `pip install mkdocs-material && mkdocs serve` — preview at http://127.0.0.1:8000.
- **StamPLC firmware:** PlatformIO in `firmware/rc_serial_probe/` (`pio run`). Flash **over USB** —
  see [`docs/flashing-stamplc.md`](docs/flashing-stamplc.md).
- **Dashboard / ROS:** see `software/kiosk-dashboard/` and `software/ros/`.

## Pull requests

- Keep PRs focused; describe what and why.
- If you changed behavior, say how you verified it (and on what hardware, if applicable).
- Update the relevant docs in the same PR.
- Be kind — see [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md).
