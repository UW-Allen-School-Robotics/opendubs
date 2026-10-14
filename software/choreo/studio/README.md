# The choreography studio

The composer, the face twin and the MuJoCo body twin, as one app on a laptop. It edits and previews
shows; it cannot move a robot.

## Install

The studio is a Python wheel attached to the repository's `studio-v*` releases. You need
[uv](https://docs.astral.sh/uv/) and the GitHub CLI (`gh`), signed in to an account that can read
the repository.

```bash
gh release download studio-v0.1.0 -R UW-Allen-School-Robotics/opendubs -p '*.whl'
uv tool install ./opendubs_studio-*.whl
```

Upgrading is the same two commands with the newer tag (`uv tool install --force`).

From a checkout, without installing:

```bash
uv run software/choreo/studio/studio.py up
```

## Use

| Command | What it does |
|---|---|
| `opendubs-studio up` | starts the composer, the face page and the MuJoCo window, and opens the pages |
| `opendubs-studio status` | what runs, and on which ports |
| `opendubs-studio open` | opens the composer and the face page again |
| `opendubs-studio down` | stops what `up` started, and nothing else |
| `opendubs-studio paths` | the folders and settings in use |
| `opendubs-studio limits --from-robot <host>` | uses that robot's arm joint limits (over ssh) from now on |
| `opendubs-studio selftest` | renders one off-screen frame of the twin |

`up --headless` starts the composer and the face door without a window or a browser.

## Settings

`studio.toml` in the config folder that `paths` prints. Every key is optional:

```toml
composer_port = 8091
door_ports = [8765, 8766, 4244, 4245]   # face page http, websocket, face, neck
head = "tablet"                          # the face twin: tablet, or none
body_head = "galaxy_tablet"              # the head the MuJoCo twin draws
open_browser = true
shows_dir = "~/opendubs-shows"           # default: that folder if it exists, else one in the data folder
library_dir = "~/opendubs-library"       # the show library (songs, skins); never inside the app
limits = "/path/to/joint_limits.yaml"    # default: what `limits --from-robot` saved, else the TEST fixture
```

Environment variables override the file: `OPENDUBS_STUDIO_PORT`, `OPENDUBS_FACE_WEB_PORTS`,
`OPENDUBS_STUDIO_SHOWS`, `OPENDUBS_LIBRARY`, `OPENDUBS_STUDIO_LIMITS`, `OPENDUBS_STUDIO_HEAD`,
`OPENDUBS_NO_BROWSER`. `OPENDUBS_STUDIO_HOME` puts every studio folder under one directory.

A port in use is never taken over: the studio uses the usual ports when they are free and asks the
system for free ones otherwise; `status` shows which.

## What is in the wheel

`build_wheel.py` copies the files git tracks from `software/choreo`, `software/face-web`,
`software/head-protocol`, `sim/` and `description/`, in the repository's own layout, without tests,
deploy files, `software/face-web/vendor/` (camera tracking is off in the studio) or
`software/face-web/test/` (bench files the studio never loads). `.github/workflows/studio.yml` builds it, installs it and runs the whole
app on macOS and Linux for every change to those folders.
