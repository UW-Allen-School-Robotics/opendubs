# Hygiene findings register and workplan

Owned by the **Hygiene lane** (CLAUDE.md, "Lanes"). The Hygiene lane reads the whole repo and edits
only its own paths. Each finding below names the lane that owns the fix; the owner decides and
makes the change. Anything that would rewrite git history is the maintainers' decision.

**Rules for this file.** No secret value and no person's name is ever written here: findings cite
`file:line` and say *redacted*. A leaked secret is listed as needing rotation, whatever happens to
history.

| | |
|---|---|
| First full sweep | 2026-10-09, against `main` at `3822efe` (gitleaks scanned 640 commits across all branches) |
| Tools | gitleaks 8.30.1 (`git` over `--all`, and `dir`), trufflehog 3.99.2 (`git`), exiftool over every image blob ever committed, `reuse lint` on a `git archive` export, hand greps, and a read-through of deploy scripts, the dashboard backend and both firmware network paths |
| Status keys | **open** · **sent** (with the owner) · **fixed** (cite the PR) · **accepted** (maintainers chose to keep it; say why) |

---

## Decisions

### Made (2026-10-09, relayed by the orchestrator)

| # | Decision | Effect |
|---|---|---|
| 1 | **Rotate the robot Wi-Fi password (S1).** | The user's own action, on the at-robot list. The literal is removed in PR #126; CI blocks a new one. |
| 2 | **S2 and S5 are accepted risk for now:** accepted network-command risk; details held privately. A security design may start; nothing deploys until the robot's behaviour settles. | Recorded as **accepted**, not as open defects. |
| 3 | **No history rewrite.** | Rotate, fix the current tree, block in CI. This covers S1, D1 and N4. |
| 4 | **L1: credit and keep.** | Docs & BOM adds the attribution and checks each vendor's terms. |
| — | **S4: approved.** | The Tablet & head interface lane adds the Origin check. Requests with no `Origin` header must keep working. |
| 5 | **Copyright holder: keep the TODO**, awaiting legal advice. | L4's holder question is parked. Sweeps and CI do not raise it again. |
| 6 | **`access/`: move the roster to the git-ignored `ops/access/`**, with role handles in the repo. | Hygiene does it (the path is unowned): branch `hygiene/access-roster`. **Merges only after the maintainer copies the current roster and keys into `ops/access/`.** |
| 7 | **Dependabot alerts: on. Required checks on `main`: approved.** | Config approved as proposed: require `Hygiene checks` (GitHub Actions, app 15368), not strict, admins included, 0 approvals, other settings unchanged. **Applied by the orchestrator only after the `hygiene/ci` PR merges**, then read back to confirm. |
| 9 | **No name deny-list in CI** (2026-10-14). The `HYGIENE_NAME_DENYLIST` secret will not be set. | The PR name check and the bundle gate's name check report "skipped: no deny-list configured" and pass. Personal names are caught by review and by the no-personal-names rule, not by CI. |

### Still open

8. **The unowned items** below (`backend/stamplc.py`,
   `firmware/rc_serial_probe/`, and `notes/per-robot-calibration.md`, which goes stale when S3's fix
   merges).
---

## Findings, ranked

### Urgent / high

**S1 · Live secret: robot Wi-Fi password hard-coded.** · literal **removed from `main` (PR #126,
merged)**. **NOT ROTATED as of 2026-10-15:** the password in the maintainers' current
`ops/wifi.env` still matches the leaked one. It remains in history, and at the tip of 193 refs
(82 branches, 111 PR heads, which GitHub keeps), so rotation is the only fix · owner: the
**user** (router key, then re-provision the Wi-Fi devices and update `ops/wifi.env`); the literal
was the Face & HRI lane's.
- `firmware/tab5_face/src/main.cpp:316` passes the robot network's SSID and pre-shared key as
  string literals to `WiFi.begin()` (value redacted).
- It was added in `4d64282` (2026-09-03) and is the only copy in the tree or in history.
- gitleaks and trufflehog both missed it: a quoted string with no keyword next to it matches no
  rule. The CI check needs a project rule for `WiFi.begin("…", "…")` and similar calls.
- The tree is the retired Arduino head.
- **Fix:**
  - Rotate the PSK on the router.
  - Re-provision the head through the existing `OPENDUBS_WIFI_SSID/PASS` one-time build path
    (`firmware/tab5_idf/build.sh:361`).
  - Delete the retired `firmware/tab5_face` tree (Q10), or at least replace the literals with the
    `#ifndef OPENDUBS_WIFI_PASS` build-flag pattern the tree already uses elsewhere.
- **Related, low:** a provisioning build of the IDF head writes the PSK into
  `<demo tree>/…/net_credentials.h` and into the built `.bin`. Both are outside the repo, but the
  image is copied to the Thor. Treat a provisioning image as a secret and delete it after use.
  Having `build.sh` overwrite `net_credentials.h` once the build is done would close this. The head
  firmware is frozen to approved fixes during the tablet evaluation, so this waits on the maintainers.

**S2 · Accepted network-command risk (StamPLC); details held privately.** · **accepted 2026-10-09** (decision 2) · owner: **unowned (StamPLC)**, through the orchestrator.

**S3 · `deploy-all.sh` overwrites per-robot calibration and deletes deployed files.** · **fix committed** (`a9a205e` on `fix/deploy-all-per-robot`, not yet pushed): rsync now excludes `deploy-audit.py`'s PER_ROBOT list, and the dashboard deploys through `deploy.sh`. Tested on a scratch directory, not yet on a robot ·
owners: **Thor stack lane** (`deploy-all.sh`, `tools/deploy-audit.py`) and **unowned**
(`firmware/rc_serial_probe` constants).
- `deploy-all.sh:20-23` runs `rsync --delete` over `firmware/rc_serial_probe` with no exclude.
- The robot's drive signs and ESC span are `static constexpr` values in
  `firmware/rc_serial_probe/src/main.cpp:148,164`. The repo values are a worked example, not any
  robot's values.
- So one `deploy-all.sh` followed by an OTA flashes the wrong wheel inversions. This is already
  known and has cost a day (`notes/per-robot-calibration.md`), but it is not fixed.
- `tools/deploy-audit.py:40` marks the file as PER_ROBOT, but nothing in the deploy honours that.
- The same rsync also does two more things:
  - It deletes the robot's `kiosk-dashboard/docs-site/`.
  - It copies the `frontend/assets/arm` symlink, which dangles in the Thor's flat layout.
    `software/kiosk-dashboard/deploy/deploy.sh:22-48` excludes and rebuilds both.
- **Fix:**
  - Short term: make `deploy-all.sh` exclude `src/main.cpp`, `docs-site/` and `assets/arm`, or call
    `deploy.sh`.
  - Long term: move the calibration into NVS (the `rccfg` pattern in `rc_config.cpp`), and have one
    deploy manifest that `deploy-all.sh`, `deploy.sh` and `deploy-audit.py` all read.

**L1 · Third-party product images on the public CC-BY docs site.** · **decided 2026-10-09: credit and keep** (Docs & BOM adds attribution and checks each vendor's terms) · owner:
**Docs & BOM lane.**
- `docs/assets/diagrams/rc-rcvr-wiring.png` embeds the receiver maker's own labelled product
  render. `rc-control-mapping.png` is built on the transmitter's product render.
- Both are all-rights-reserved, so the project cannot license them under CC-BY-4.0, yet Pages
  publishes them.
- `docs/assets/diagrams/otos-wiring-harness.png` uses SparkFun product photos with no credit.
  SparkFun publishes product photos under CC-BY-2.0, but check that per image.
- **Fix:** redraw the receiver and transmitter as plain outlines or the team's own photos. Credit
  SparkFun in the caption, or redraw.

**S5 · Accepted network-command risk (head); details held privately.** · **accepted 2026-10-09** (decision 2) · owner: **Face & HRI lane.**

### Medium

**D1 · Phone GPS coordinates in published photos.** · **fixed in PR #125 (open)**, verified at `baeccdf`: no image under `docs/assets` carries GPS, make or model · owner: **Docs & BOM lane.**
- `docs/assets/build/rivnuts/01-thread-rivnut.jpg`, `02-press-tight.jpg`, `02-press-tight-alt-a.jpg`
  and `02-press-tight-alt-b.jpg` carry GPS EXIF (the lab's location; coordinates redacted).
- All nine JPGs in `rivnuts/` and `thor-install/` carry the camera make and model.
- They are live on the public site.
- The cause: `tools/convert-build-photos.sh` (macOS `sips`) keeps EXIF, while the Python twin
  `tools/convert_build_photos.py:32` strips it. `docs/assets/build/README.md:9` says to strip EXIF.
- Two older images in history also carried a GPS block: `02-miter-gear*.jpg`, with a null
  position.
- **Fix:**
  - Strip the metadata but keep `Orientation` and the ICC profile. A bare `exiftool -all=` turns
    `02-press-tight-alt-b.jpg` sideways, because only its Orientation tag (6) makes it display upright.
  - Make the `.sh` path strip metadata too, or retire it in favour of the `.py`.
  - Hygiene CI will fail any image that carries GPS.

**S4 · Cross-site requests to the dashboard.** · **fixed in PR #162 (merged, `0f1b1d3`)** for POSTs
and for the `/ws` and `/rosbridge` WebSockets. A request naming a foreign page is refused; one naming
no page passes, so curl, scripts and native clients keep working. Approved 2026-10-09; Tablet &
head interface lane. Two edges remain, as S4b and S4c.

**S4b · A ROS network-access gap.** · **fixed 2026-10-12**: rosbridge now listens on `127.0.0.1` only (PR #167, `188ecc2`), and network clients use the dashboard's Origin-guarded `/rosbridge`. `SECURITY.md` was updated with it (PR #195) · owners: Thor stack & localization, Tablet, Arms.

**S4c · `/ws/show-audio` has no origin check.** · **approved, in progress** · owner: **Choreography**
(`backend/show_audio.py`). The Tablet lane has asked that lane to add
`origin_guard.refuse_websocket()` before `accept()`.

**S6 · Spoofed StamPLC telemetry redirects safety commands.** · open · owners: the dashboard's
`backend/stamplc.py` is **unowned (to the orchestrator)**; `software/ros/drive_bridge.py` is the
**Thor stack lane**'s.
- `stamplc.py:236-245,269-285` adopts any non-Thor host whose datagram looks like telemetry as the
  board ("last sender wins").
- After that, the operator's software E-STOP goes to that host, and the forged state feeds the
  re-arm guard.
- `drive_bridge.py:147` adopts *any* sender, with no guard at all, and sends `DRIVE` to it.
- **Fix:** pin the StamPLC address (configured, or by mDNS name), and share one link module between
  the two (Q2).

**S7 · Any app on the tablet can install a trusted CA or unlock the kiosk.** · **fixed in PR #172 (open; verified at `a3a70eb`)**: a separate `ProvisionActivity` guarded by `android.permission.DUMP` takes the extras, and `KioskActivity` ignores them. The APK builds; on-tablet checks wait for the bench tablet · owner:
**Tablet lane.**
- `KioskActivity` is exported (`software/tablet-kiosk/app/src/main/AndroidManifest.xml:18`).
- `KioskActivity.kt:115-131` acts on intent extras from any caller:
  - `ca_b64` installs a CA through the device-owner API.
  - `ca_clear` removes CAs.
  - `url` repoints the kiosk and keeps the change.
  - `unlock` leaves lock-task mode.
- Needs code on the tablet or adb.
- **Fix:** move the provisioning extras to a separate component guarded by
  `android:permission="android.permission.DUMP"`. adb shell holds that permission; other apps
  cannot get it.

**L2 · Face-detection test image.** · **closed 2026-10-14: public-domain artwork; provenance
noted.** No removal and no rewrite.
- `software/face-web/test/human_face.jpg` and `firmware/tab5_idf/probe/human_face.jpg` are one
  39 KB image, committed twice. It is a crop of a reproduction of Leonardo da Vinci's *Mona Lisa*
  (painted c. 1503–1519). The painting is in the public domain. A faithful photographic
  reproduction of a 2-D public-domain work is generally treated as public domain in the US. No
  living subject's consent is involved.
- Provenance: it came in as esp-dl's `human_face_detect` example image (commit `7ed0194`;
  `firmware/tab5_idf/app_face/percept/facedet.cpp:55`).
- It is used at runtime by the face page (`software/face-web/engine/track.js:21`) and embedded in
  the head firmware by `firmware/tab5_idf/build.sh:450`. Both are fine.
- The first sweep wrongly described it as a photograph of a real person, because the image itself
  was not opened. Corrected here. Lesson for the sweeps: look at an image before classifying it.

**L3 · Meshes with no stated source.** · open · owner: **Thor stack lane** (robot description).
- `description/opendubs/meshes/D415.stl` is almost certainly the camera maker's CAD, and its terms
  are not recorded.
- `1120_channel.stl` looks like the vendor's product CAD and is unused (`dimensions.yaml:179`).
- **Fix:** delete `1120_channel.stl`. Replace the D415 with a box of the datasheet envelope, or
  record its terms.

**L4 · Licence texts are stubs and the copyright holder is unresolved.** · **holder awaiting legal advice** (decision 5: not raised again); the stub texts and the site notice are still open · owners:
**Hygiene** (`LICENSE-*`, `CITATION.cff`), **Docs & BOM** (`mkdocs.yml`), and the
**maintainers** (who holds copyright).
- `LICENSE-DOCS:17-19` and `LICENSE-HARDWARE:18-22` say "append the full text".
  `LICENSE-HARDWARE:21` and `CITATION.cff:10` leave the holder as a TODO.
- The public site shows no licence notice.
- **Fix:** once the holder is decided, add the full texts under `LICENSES/`, add
  `copyright:` to `mkdocs.yml`, and adopt REUSE (see the CI section).

**N1 · Personal names in `access/`.** · **approved to move** (decision 6); fix on `hygiene/access-roster`, which waits for the maintainer's copy into `ops/access/` · owner: **unowned** (`access/`, `tools/access.py`), to
the orchestrator.
- The key files are named after people.
- Three `.pub` key comments carry personal email addresses (`access/*.pub:1`).
- The manifest sections are named after people.
- **Fix:** decide whether the roster belongs in the repo at all (Decision 6). If it stays, use
  role handles and strip the key comments.

**N2 · A personal Unix username is wired into services and sudoers.** · open · owner: **Thor stack
lane.**
- `User=` and `HOME=` in every `software/ros/opendubs-*.service` (e.g.
  `opendubs-drive-bridge.service:15-16`).
- `software/ros/opendubs-ros-sudoers:1-4` and `software/ros/scan_watchdog.py:19`.
- `firmware/rc_serial_probe/provision-firmware.sh:13` (unowned).
- `optional/pixhawk/opendubs-mavros.service:8-9` (unowned).
- It is a naming problem and a security problem at once. The network-facing daemons (rosbridge,
  foxglove, drive and stamplc bridges, camera) run as a personal account that has passwordless
  sudo, so code execution in any of them becomes root.
- **Fix:** a dedicated `opendubs-ros` service user with no sudo, created by `provision-ros.sh`.
  The sudoers rule then names that user.

**N3 · Contributors named in notes and code comments.** · partly fixed: Docs & BOM in PR #125, Arms in PR #124 (both open; verified at `baeccdf` and `bb9bb0a`); the rest open · owners as listed.
- `notes/docs-review-checklist.md:3,8,86` (Docs & BOM).
- `notes/design/arm-authority.md:156,180,203,316`, `notes/design/arm-telemetry-and-viz.md:99`,
  `software/openarm/patches/README.md:3,9,59` and `software/openarm/patches/teleop-optional-viewer.py:2`
  (Arms).
- `sim/README.md:40,84` (Choreography, which has owned `sim/` since PR #253 and is fixing these).
- `notes/punch-list.md:40,46,65,127,245` (shared).
- `firmware/rc_serial_probe/src/mast.h:146` (unowned).
- `firmware/tab5_face/src/ui_shared.cpp:26` (Face & HRI).
- `notes/design/arm-authority.md:156` also names a third party (a lab lead).
- **Fix:** say "the project team", "the maintainers" or "the upstream author". An upstream repo
  URL that contains a GitHub handle is fine as a link.
- **Keep** `description/openarm/LICENSE-openarm_assets:3`: the MIT licence requires that copyright
  line.

**N4 · Names in commit messages (history).** · open · owner: the **maintainers** (history).
- About 60 commit messages attribute decisions or corrections to people by first name.
- The repo is private, and recommendation 3 is not to rewrite.
- From now on the CI name check also reads the PR's commit messages. `Signed-off-by:` trailers and
  author metadata are fine.

**G1 · GitHub-side controls are off.** · partly fixed: Dependabot alerts on; required check approved, to be applied once the CI PR merges (decision 7) · owner: the **maintainers**.
- Secret scanning and push protection are off. On a private organisation repo they need GitHub
  Advanced Security, which is paid, so the CI scanner stands in for them.
- Dependabot alerts are off. They are free.
- `main` requires a PR, but with zero approvals, no required status checks, and admins exempt.
- `SECURITY.md` points reporters to GitHub private vulnerability reporting, which a private repo
  does not offer. It also has no contact address (the same is true of `CODE_OF_CONDUCT.md`).

### Low

| ID | Finding | Where | Owner | Fix |
|---|---|---|---|---|
| S8 | Heartbeat service runs as root with no need | `opendubs-stamplc-hb.service` (no `User=`) | shared (`kiosk-dashboard/deploy/`) | `User=kiosk` or `DynamicUser=yes` |
| S9 | Root OTA script uses a fixed `/tmp` log and serves the whole build directory on the LAN | `firmware/rc_serial_probe/ota-from-thor.sh:82,98` | unowned | `mktemp -d`, serve only the `.bin`, as `tab5_idf/ota-from-thor.sh` already does |
| S10 | The robot CA has no name constraints and is valid for 10 years | `software/kiosk-dashboard/deploy/make-robot-ca.sh:28-31` | shared | `nameConstraints=critical,permitted;DNS:<robot>.local,IP:<subnet>` |
| S11 | Root provisioning fetches files nothing verifies: udev rules from a `develop` branch; driver source pinned only to a movable kernel tag (`v${KVER}`) | `firmware/rc_serial_probe/provision-firmware.sh:30`, `software/openarm/provision-openarm.sh:19,35` | unowned · Arms (planned, arms plan 0.15) | pin a commit; commit SHA-256 sums and refuse a mismatch |
| S12 | CI actions pinned by tag, not by commit SHA | `.github/workflows/*.yml` | each workflow's owner | pin SHAs when convenient; Dependabot can keep them current |
| L5 | three.js shipped without its MIT notice | `software/kiosk-dashboard/frontend/vendor/` | shared | add `LICENSE-three.txt` (r160) |
| L6 | Upstream files without a credit line: LVGL template, M5Stack demo config, M5GFX-derived bring-up | `firmware/tab5_idf/lv_conf.h`, `sdkconfig.vendor`, `patches/gt911_selftest.c.inc:59-122` | Face & HRI | one credit line each, or REUSE.toml |
| L7 | MediaPipe licence copy can fail silently | `software/face-web/fetch-vendor.sh:19` (`\|\| true`) | unowned | make the copy fatal before the APK bundles it |
| L8 | Hat branch carries vendor 3-D models (TI, LCSC/EasyEDA STEP and WRL) with unknown terms | `origin/hardware/stamplc-hat`: `hardware/hat/hat.3dshapes/` | Hat PCB (parked) | check terms before that branch merges |
| D2 | Per-robot addresses as code defaults (agreed) | `software/kiosk-dashboard/backend/state_out.py:364`, `software/tablet-kiosk/app/src/main/res/values/strings.xml:6`, a USB serial-number glob in `backend/state_out.py:51` | Tablet | read them from `/etc/opendubs/robot.env` |
| I1 | Ignore-rule gaps: firmware images (`*.bin`, `*.elf`), `*.apk`/`*.aab`, recordings and rosbags (`*.mp4`, `*.mov`, `*.wav`, `*.mcap`, `*.db3`), HEIC, `.pytest_cache/`, editor junk (`.vscode/`, `.idea/`, `*.swp`), keys (`*.pem`, `*.key`, `id_*`), `.env.*` | root `.gitignore` | **Hygiene** | add in the CI PR; nothing tracked today matches, so nothing has to move |

### Code quality, ranked by cost (report only)

These are proposals for the owners. Hygiene will not refactor other lanes' code.

| # | Cost | Finding | Owner | Proposal |
|---|---|---|---|---|
| Q1 | safety | Drive calibration in shared source, overwritten by deploy | see S3 | NVS |
| Q2 | safety | `software/ros/drive_bridge.py` re-implements the StamPLC link without the dashboard's peer guard, with its own parser, no tests, and no CI for `software/ros` | Thor stack | one shared StamPLC link module; a path-filtered `ros` CI job |
| Q3 | safety | StamPLC firmware is one 3,343-line `main.cpp` (drive gate, mixer, ~290-line command parser, BMS, UI, OTA) with no CI build and no tests | unowned | pull the drive gate and mixer out as pure functions with host tests (the `firmware/comms/test` pattern), plus a CI `pio run` |
| Q4 | safety (**fixed in PR #171, open, `b3c4155`**) | Two UDP/5006 lift bridges (`backend/state_out.py:296-330`, `software/ros/tab5_bridge_node.py:91-196`) decode a frame the shipping head no longer sends (`firmware/tab5_idf/app_face/net.cpp:61`). Dead code on the pinch-zone path | Tablet | delete both listeners |
| Q5 | defect (**fixed in PR #171, open, `b3c4155`**, with a test that fails on a retired message; the 0x104 tracker stays, marked, until a head implements TRACK) | The AI TRACKING button sends `0x108` (`state_out.py:145`), which `software/head-protocol/head_protocol.py:77` lists as retired. No sender imports `head_protocol`; port 4242 is hard-coded at ~10 sites | Tablet, with Choreography | senders import `head_protocol`; a CI test fails on any retired frame |
| Q6 | risk | No tests for `software/openarm/openarm-up` (541 lines of bash where "the guards live") or for the HTTP routes `/api/estop`, `/api/mast`, `/api/relay`; `dashboard.yml` does not run when `software/choreo` changes the proxy contract | Arms · shared | TestClient route tests; shellcheck plus `openarm-up --dry-run` in CI |
| Q7 | time per change | `backend/app.py` (25 routes, edited by 5+ lanes in 30 days) and `frontend/app.js` (1,419 lines) are god-modules. `stamplc.py` starts a thread at import, forcing 17 function-local imports. `app.py:4-7` says "Nothing here commands actuators", which is false | shared | one router per lane (as `choreo_proxy` already does); explicit `reader.start()`; fix the docstring |
| Q8 | defect | Two deploy paths disagree (see S3); `tools/deploy-audit.py:5` says there is no deployment mechanism, and still maps the retired `tab5_face` | Thor stack | one deploy manifest |
| Q9 | onboarding | StamPLC `[TAG] k=v` telemetry parsed by hand in four places (`backend/stamplc.py:21-54`, `firmware/rc_serial_probe/tools/telemetry_gui.py:117`, `telemetry_viewer.py:136`, `drive_bridge.py:60`), with 4 tests total | unowned · Thor stack | a tag schema in YAML, a generated parser, and a contract check like `head-protocol.yml` |
| Q10 | onboarding | **Docs part fixed in PR #129 (open; verified at `0a2ae83`).** Retired trees presented as current: `firmware/tab5_face` (3,839 lines; listed in `README.md:59`, `docs/index.md:53`, `docs/interfaces/payload-comms.md:17,122`); `firmware/comms/opendubs_can.h` (no firmware uses it); VNC and Pixhawk leftovers in the dashboard; `firmware/tab5_idf_probe` | Face & HRI · Docs · shared | tag the last Arduino commit (cited as the rollback in `FLASHING-THE-ROBOT.md:86`), then delete; fix the docs. Keep `servo_mode_programmer`: the pan-tilt build uses it |
| Q11 | low | `firmware/tab5_idf/app_face/app_face.cpp` is 2,063 lines (geometry, themes, StamPLC link, camera) | Face & HRI | split along its existing section comments |

### Checked and clean

- **Secret scan:** gitleaks and trufflehog report nothing in history or the tree. The only secret
  found (S1) was found by hand.
- **Bulky files:** no firmware images, APKs, logs or recordings are tracked, except two deliberate
  wedge-investigation logs in `notes/history/`.
- **Repo size:** the pack is 67 MB. The largest history is ~30 revisions of a 2–3 MB KiCad board on
  the hat branches, which is acceptable.
- **Tracked vs ignored:** nothing tracked is matched by an ignore rule.
- **Show content:** show audio and sounds are ignored by design. The seasonal themes are generic
  examples, and no event-specific content was found.
- **Tablet signing keys** are ignored (`*.jks`, `*.keystore`).
- **Kiosk sudoers** are tightly scoped: `openarm-up` validates every argument, is installed
  root-owned, and `kiosk` is not in `docker`.
- **Dashboard backend** has no `shell=True`, `eval`, `pickle` or `yaml.load`. Media paths are
  realpath-checked.
- **Choreo** uses only `safe_load`. The runner binds `127.0.0.1`, and the proxy has fixed routes.
- **Firmware UDP parsers** spot-checked: bounded, NUL-terminated, `snprintf`.
- **Licences:** OpenArm (Apache-2.0 plus MIT) keeps its notices and records its changes;
  `vl53l1x` keeps its BSD-3 notice; the Gradle wrapper is Apache-2.0. No GPL code and no fonts are
  vendored.
- **CI workflows** set `permissions:` and do not use `pull_request_target`.

---

## Pre-publication audit (2026-10-15)

The maintainers asked whether the repo could be made public. **Verdict: NO-GO until S1 is
rotated; after that, GO with the decisions below.** Going public is parked (2026-10-15); this
section is the reference for when it comes back. The audit was read-only.

**Scope:**
- a mirror clone: all 594 refs (271 branches, 311 PR heads, 0 tags), 1,267 commits and every blob
  version;
- all PR bodies, comments and issues;
- 216 Actions run logs (every `studio`, `voice` and `ros` run, plus the last 15 of each other
  workflow);
- artifacts: only `github-pages`, which is already public;
- releases: none exist.

The private `openarm` repo is separate and is not affected.

**Beyond the scanners.** gitleaks (default and project rules) and trufflehog found only S1, and only
through the project rule. To cover what scanners miss, the audit also did these:
- **Known values:** searched every blob, log and PR text for the actual values in the private
  `ops/` files. They were read locally and never printed.
- **Entropy:** reviewed high-entropy quoted strings, 505 after filtering. All were part names,
  joint names or identifiers.
- **Credential shapes:** reviewed 38 credential-shaped assignments by hand. All were code or prose.
- **Images:** ran EXIF on all 175 image blobs ever committed.
- **File types:** inventoried every path ever committed for media, fonts, models, archives and
  keys.

**Blocker:**
- **S1.** Not rotated (see S1 above). This is the user's action.

**Decide before publishing.** Each of these becomes irreversible once public, because of clones,
forks, public-archive crawlers, and PR refs that GitHub keeps.
1. **Commit metadata.** 7 personal email addresses from 6 contributors are in author and
   committer fields, and 71 commit messages name people. Only a history rewrite plus GitHub Support
   could remove them. Recommendation: no rewrite. Tell the contributors and get their agreement, and
   use noreply addresses from now on.
2. **`access/` is still on `main`** (N1). Copy the roster into `ops/access/`, then merge #194.
3. **Names in the current tree** (24 files) and in 15 PR or issue texts. Most are the personal
   Unix username (N2). The PR and issue texts can be edited before publishing.
4. **Unreleased upstream code.** `software/openarm/patches/teleop-optional-viewer.py` quotes
   fragments of the upstream teleop author's unpublished code as its find/replace targets. That is
   the reason the org's `openarm` copy is private. The Arms lane and the maintainers decide.
5. **A private dependency.** `software/openarm/image/repos.lock` pins the private org `openarm`
   repo, so an outside replicator cannot build the arm image. This is functional, not a leak.
6. **S2/S5.** Publishing the firmware source publishes the accepted network-command risk. Confirm
   that the acceptance holds when the code is public.
7. **Licences (L4).** The DOCS and HARDWARE licence files are stubs, and the copyright holder
   awaits legal advice. Hygiene can add the full licence texts.

**Clean:**
- No other secret appears anywhere, and the run logs are clean.
- No audio, video, fonts, model weights, archives or keys were ever committed.
- Show-related text is titles only.
- `main` has no GPS-tagged images. Four older versions with the lab's location remain at the tips
  of stale refs, which is low risk.

**Suggested order:**
1. Rotate S1, and Hygiene re-verifies.
2. Decide 1 and 6.
3. Merge #194.
4. Lanes clear 3 and 4, and the maintainers edit the PR texts and optionally delete stale branches.
5. Licence texts.
6. Flip the visibility.

## Workplan

### A. This register

Done for the first sweep. Findings go to the orchestrator, one message per owning lane.

### B. CI on every PR: `.github/workflows/hygiene.yml` and `tools/hygiene/`

Not started; waiting on review of A.

| Check | Tool | Starts as | Can block when |
|---|---|---|---|
| Secrets in the PR diff | gitleaks binary (the GitHub Action needs a paid licence for organisation repos), plus a project rule for literal Wi-Fi credentials | **blocking** (history is clean) | now |
| File size | script; limit 2 MB per added file, with an allow-list for the existing meshes and diagrams | warn | after the allow-list is reviewed |
| Image metadata | exiftool: fail on GPS, warn on camera make, model or serial | warn | after D1 is fixed |
| Personal names | script reading a deny-list from the `HYGIENE_NAME_DENYLIST` Actions secret | **skipped by decision** | The secret will not be set (2026-10-14). The check reports "skipped: no deny-list configured" and passes, and the bundle gate is called with `--allow-no-denylist`. Names are caught by review and by the no-personal-names rule. |
| Licence | `reuse lint` with a REUSE.toml (a draft already passes on an export) | **parked** | a REUSE.toml must name a copyright holder, which awaits legal advice (decision 5) |
| Ignore sanity | `git ls-files -ci --exclude-standard` must be empty; no build or download artefact paths | blocking | now |

Making any check blocking also needs the maintainers to add it as a required status check (G1).

### C. Weekly sweep

To be set up after B, with the user's approval of the schedule. It re-runs the slow checks: the
full-history secret scan, licence drift, new dependencies (`requirements*.txt`, `package.json`,
`platformio.ini`, `idf_component.yml`, `software/stack/versions.env`), and quality drift (file
sizes, untested risky paths). It reports to the orchestrator and the user, and edits nothing.
