# The Halloween ghost face: handover

The tablet face for the Halloween demo day is a content code skin, `ghost.js`: a ghost with its idle
expressions (DOZE, LOOKOUT, GROOVE, SMILE, PEEK, EEK!, FOCUS, HAPPY, PUZZLED), driven by the demo day's
coordinator and the face tracker, and a choreographed face for each short show (`wave-and-nod`,
`robotic-smile`, `celebrate`) in costumes that change within the show (pumpkin, cat, monster, the
classic black and white). The state machine, every expression animated:
[`face-halloween-ghost-states.html`](face-halloween-ghost-states.html).

This page is in two parts. **Part 1** is what has to change, and who changes it, for the ghost to run
on the robot. **Part 2** is everything else: tools for previewing and testing on a laptop, which the
robot never uses.

---

## Part 1: needed on the robot

### What is in this branch (opendubs)

| File | What it is |
|---|---|
| `software/face-web/content/face/skins/ghost.js` | **The skin.** One file, imports nothing from the engine. `DANCES` holds each short show's face timeline, costumes and tempo. |
| `software/kiosk-dashboard/backend/architect_proxy.py` | Two read-only routes the ghost polls from the tablet: `GET /api/architect/day` (the coordinator, 127.0.0.1:8096) and `GET /api/architect/state` (the demo runner, 127.0.0.1:8098). The tablet can't reach those ports itself. |
| `software/kiosk-dashboard/backend/app.py` | Includes that router (a shared path: two lines). |

No engine file, show file or head-protocol message changes.

### What has to happen, in order

| # | Step | Who |
|---|---|---|
| 1 | Review and merge this branch's PR into `main`. CI runs the face conformance tests, the ghost's tests and the proxy's tests. | Tablet lane (owns `face-web` outside the engine, and the dashboard backend); the PR touches one shared path, `backend/app.py`. |
| 2 | Deploy the dashboard as usual, so the robot has the two `/api/architect/*` routes. | Whoever deploys (normal `deploy-all.sh`). |
| 3 | Put `ghost.js` in the **content repository** at `face/skins/ghost.js` (on the robot, `$OPENDUBS_SHOWS_DIR/face/skins/ghost.js`). Code skins reach the robot **only** from the content repository by its sync, never by upload (`backend/head_looks.py`). | The content repository's owner. |
| 4 | Have the coordinator (`demo_day.py`, port 8096) running on the robot for the day. It is not on `main` yet. Without it the ghost still dozes, looks round, grooves and smiles at people, but the shows' dances, EEK!, FOCUS and HAPPY need it (or the demo runner on 8098). | The coordinator's owner. |
| 5 | Turn the ghost on for the day: send `SKIN ghost` (Head & face page, or the operator). It stays on through every show: the three short shows have **no** `skin` key on their FACE track, and must not get one, or the ghost comes off for that show. | Operator on the day; Choreography lane for the show files. |

### Optional

- **Music for the short shows.** Each has an original synthesized Halloween track, made by ENCORE's
  `demo/halloween-shorts/make_music.py` (Part 2). To play one on the robot, copy it to
  `software/choreo/shows/audio/` on the robot and point the show's `music.file` at it. Audio is never
  committed. **Who:** Choreography lane (owns `software/choreo/`).
- **Spoken lines.** Proposed lines per show, none of them used yet (they'd need a show to be able to
  trigger speech; today its SOUND slot plays only the six earcons). They follow the listener's rules:
  no promise of movement, nothing about how someone looks, under about 1.5 s, in the music's rests.
  **Who:** Voice lane, if wanted.

  | Show | s | Line |
  |---|---|---|
  | wave-and-nod | 0.2 | "Happy Halloween!" |
  | wave-and-nod | 17.0 | "Thank you, thank you." |
  | robotic-smile | 0.3 | "Mmm… is someone there?" |
  | robotic-smile | 25.2 | "Eek!" |
  | robotic-smile | 29.2 | "Ha ha! Happy Halloween!" |
  | celebrate | 0.1 | "Boo!" |
  | celebrate | 4.2 | "We did it!" |

### Changing a show's face later

Edit `DANCES[show].steps` in `ghost.js`: a list of `[seconds into the show, look, costume?]`. The looks
and costumes it accepts are `TIMELINE` at the end of the file. Then run
`node software/face-web/tests/test_ghost_skin.mjs`, and repeat step 3. The easiest way to draw the steps
is ENCORE's ghost lane (Part 2), which copies them out in this form.

---

## Part 2: for previewing and testing only (not used by the robot)

### In this branch (opendubs)

| File | What it is |
|---|---|
| `software/face-web/tests/test_ghost_skin.mjs` | Which expression shows for each coordinator and runner state; the dances' tables; every look drawing in every costume; each show's steps fading into each other with no jump. Run by `tests/conformance.sh`. |
| `software/face-web/README.md` | The ghost's section: what it is, the files, how to put it on the robot. |
| `notes/design/face-halloween-ghost-states.html` | The state machine, animated (download and open in a browser). |
| `notes/design/face-halloween-ghost-handover.md` | This page. |

### In ENCORE (`UW-Allen-School-Robotics/encore`, the branch of the same name as this one)

ENCORE is the choreography studio. These changes let it preview a show in the ghost and draw the
ghost's face timeline; nothing in them is needed to run the ghost on the robot. They're recorded here
in case they're not merged into ENCORE, so the work isn't lost.

| Commit | What it adds |
|---|---|
| `3fc3f87` | **Wear…** in the Face pane: preview a show in one of the content folder's skins (`ENCORE_FACE_CONTENT`), without putting it in the show. ENCORE registers the skin in its vendored face engine from its own page; `vendor/` is untouched. |
| `44d269d` | The Wear menu first in the toolbar; the proposal `docs/proposals/halloween-ghost.md`. |
| `5be3650` | Wearing the ghost, **Look…** and **Costume…** replace Emote… and Skin…: they put a ghost look or costume at the playhead. The draft is kept in the project (`wear/ghost/<show>.json`, served by `GET/PUT/DELETE /api/wear/<skin>/<show>`), never in the show. **Copy for the skin** copies it out as `ghost.js` writes it. |
| `a1282df` | The ghost's steps as a lane on the Timeline, under the face: drag to move, double-click to add, right-click for a look, a costume, delete, Copy for the skin, Back to the skin's own. |
| `9e649fd` | Steps snap onto markers (a held note, a section) when dragged near one. |
| `18f0d9a` | `demo/halloween-shorts/`: one project per short show, and `make_music.py`, which synthesizes the Halloween tracks (written to `~/.encore/halloween-shorts/`, never committed). Its README says how to run them. |

To use it: in ENCORE, run `uv run python demo/halloween-shorts/make_music.py`, then
`ENCORE_FACE_CONTENT=/path/to/opendubs/software/face-web/content/face uv run encore`, open one of the
three projects (File › Open project…), and choose **Wear: ghost** in the Face pane.

### Kept outside both repositories

The review pages and prototypes the design went through (the V1/V2 review pages, the dance preview,
the transitions sheet that shows each pair of neighbouring steps frame by frame) were working tools.
The state-machine page above is the one kept.
