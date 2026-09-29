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

We use the [DCO](https://developercertificate.org/) instead of a CLA. It's a lightweight statement
that you wrote the contribution (or have the right to submit it) under the project's licenses. Sign
off every commit by adding a line to the commit message:

```
Signed-off-by: Your Name <your.email@example.com>
```

`git commit -s` adds it automatically. By signing off you agree your contribution is licensed under
the project's licenses (below).

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
