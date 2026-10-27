# The content repository and ENCORE: layout and steps

Owned by the Choreography lane. **A plan: nothing has moved.** It builds on the Hygiene lane's
inventory (`notes/hygiene-content-split.md`, which decides *what* leaves the core) and says *where*
it goes and *in what order*, now that ENCORE (`UW-Allen-School-Robotics/encore`) is the standard
choreography tool. Taken against `main` at `6c5ccf9f` on 2026-10-26.

## Terms

- **Core**: this repository. The engine (`cue.py`, the runner, `library.py`, the sync tools), the
  generic interfaces, and three example shows (`hello`, `beat-demo`, `wave-and-nod`).
- **Content folder**: one folder outside the core holding everything that is a *use* of the
  engine. `software/choreo/content.py` finds it (`$OPENDUBS_SHOWS_DIR`, then
  `/var/lib/opendubs/shows` on a robot, then `~/opendubs-shows`) and defines its layout.
- **Content repository**: a git repository holding a content folder. The project's is
  `UW-Allen-School-Robotics/opendubs-shows` (private). A replicator makes their own the same way.
- **Show library**: the media (songs, videos, image skins), referenced by id from shows and never
  stored in either repository: `~/opendubs-library` on a workstation, `/var/lib/opendubs/library`
  on a robot.
- **ENCORE source**: an ENCORE show (`encore.show/1`) or snippet (`encore_snippet: 1`). The
  original a person edits. A cue sheet made from it is *generated*.

## The content repository's layout

The top level is exactly the content folder `content.py` already reads, so the same tree works
as a checkout on a workstation and as the robot's folder. Only `encore/` is new.

```
opendubs-shows/
  README.md                     what this is; how a robot, the composer and ENCORE read it
  .gitignore                    media and generated output (below)
  *.yaml                        hand-written cue sheets (until ENCORE has ingested them)
  moves/*.clips.yaml            clip libraries
  face/themes/<id>.json         face themes: {"label", "colors"}, #rrggbb only (e.g. halloween)
  face/skins/<id>.js            code skins (e.g. the monster skins, jack_o_lantern)
  tools/<pack>/<id>.yaml        a tool pack: tool types for the effector registry,
  tools/<pack>/<id>.gaits.json      their recorded gaits,
  tools/<pack>/drivers/<out>.py     and the drivers they name in `output:` (e.g. tools/surge/)
  encore/
    shows/<id>.yaml             ENCORE shows, the originals
    snippets/<id>.yaml          ENCORE snippets that belong to this content (song-specific ones;
                                ENCORE's generic snippets stay in the encore repository)
```

**Never committed here** (in `.gitignore`):

- media of any kind: audio, video, images, stage art. They go in the show library.
- generated cue sheets. They are written at push time into the robot's folder, or into a
  git-ignored `build/` locally.

**A robot's folder** (`/var/lib/opendubs/shows`, filled by `show_sync.py`) holds the same tree,
plus the cue sheets generated from `encore/`. The runner reads cue sheets only and never reads
`encore/`.

## One home tool per show, and the marker

Until the composer and studio are retired, a show is edited in exactly one tool:

- A hand-written cue sheet at the top level is edited in the composer or by hand. ENCORE may import
  it, which makes a *copy*.
- Once a show has an ENCORE source, that source is the original. The hand-written sheet is
  deleted from the content repository in the same commit that adds `encore/shows/<id>.yaml`. That
  commit records the round trip: the export passes `cue.py` and every track matches the original.
- Every cue sheet ENCORE generates carries a top-level marker:

  ```yaml
  generated:
    by: encore
    source: encore/shows/<id>.yaml
    sha256: <sha256 of the source file's bytes>
    revision: <the ENCORE commit that generated it>
  ```

  The hash is of the file's bytes, so anyone can check it with `sha256sum`. Snippets the show
  uses are not covered by it, which is fine: sheets are regenerated at every push.

  `cue.py` accepts `generated` as a top-level key and checks only its shape. The composer and
  studio open a marked sheet read-only, with a banner naming the source. They save only as a new
  show id, never over it. ENCORE sets the marker and is the only writer of it.

The marker is the one interface change, agreed with the ENCORE build session (2026-10-26). ENCORE
emits it only once `cue.py` on `main` accepts it, and re-vendors `cue.py` at that commit.

## Steps

Each step names its owner, and whether it touches the content repository (**content**) or the
robot (**robot**). Both need the user's word. Every other step is an ordinary draft PR.

| # | Step | Owner | Touches |
|---|---|---|---|
| 1 | Agree the `generated` marker with ENCORE | Choreography, ENCORE build | — |
| 2 | Core: `cue.py` accepts `generated`; the composer and studio open marked sheets read-only; the composer's `--shows` default moves to `content.shows_dir()`. (The stage page's neutral default tempo, Hygiene's item (d), is its own PR: the split doesn't need it.) | Choreography | — |
| 3 | Content repository's first commit: the layout above, `README.md`, `.gitignore`, `face/themes/halloween.json` (colours from the #539 body), the monster skins and `jack_o_lantern.js` in `face/skins/`, the Surge pack in `tools/surge/` (yaml, gaits, `drivers/surge_at.py`), and the shows leaving the core (`halloween`, `christmas`, `reach`, `celebrate`, `shrug`, `robotic-smile`, `agent-written`) | Choreography (shows, theme, pack); Face & HRI (the monster skins as a content skin) | **content** |
| 4 | The user's own finished shows: into the content repository, or local only. **Open with the user.** | the user | **content** if yes |
| 5 | Push the content to the robot's folder: `show_sync.py` (shows, themes, skins, tools) and `library_sync.py` (media unchanged). Then check that every show validates on the robot and that the head's looks list has `halloween` and the monster skins | Choreography | **robot** |
| 6 | Core removals, after step 5: #539 rebased and squash-merged (Tablet, Face & HRI, Choreography); Face & HRI removes `monsters.js` and swaps `test_monsters.mjs` for a fixture skin; Choreography removes the seven shows and the Surge pack, with the tests moved to fixtures under `software/choreo/tests/fixtures/` | each owner, one PR each, merged by the orchestrator | — |
| 7 | Deploy `main`, then repeat step 5's check | the orchestrator | **robot** |
| 8 | ENCORE reads the content repository as a library source (its `encore/`, cue sheets, `moves/`, `face/`), and writes new sources into `encore/` | ENCORE build | — |
| 9 | ENCORE ingests every show: each hand-written sheet gets an `encore/` source, its export passes `cue.py` and matches the original's tracks, and the sheet is deleted (one commit per show) | ENCORE build, reviewed by Choreography | **content** |
| 10 | Retire the composer and studio in one PR | Choreography | — |

**Order.** Steps 1 to 2 can start now. Step 5 must come before step 7: otherwise the robot's face
loses the Halloween theme and the monster skins at the deploy. One exception: `tools/surge/` reaches the robot
straight **after** step 7, in the same sitting. While the core still has its own Surge files, the
same tool ids in a pack are a registry error (`effectors.py`: defined in both), and a show with the
floor hand refuses. Steps 8 to 10 follow the split.

**Blocked today:**

- #539 is a draft and conflicts with `main`. #537, which it built on, merged 2026-10-24, so #539
  needs a rebase.
- The content repository has no commits.
- `~/opendubs-shows` on the maintainer's workstation is not a git checkout. It mixes finished
  shows with work in progress, and holds media (`art/`, `audio/`) that must not be committed.
