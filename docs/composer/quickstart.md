# Composer quickstart

The composer is the app for writing **shows**: timed routines for the robot's arms, head, lift,
face, lights and sound. A show is a file, a cue sheet that says what each part of the robot does
and when. The composer checks it against the real robot's limits before anything moves, previews it
on a digital twin, and sends it to the robot.

This page takes you from installing the composer to running a show live, in seven steps.

<!-- screenshot: ../assets/composer/composer-01-overview.png — the whole composer, its areas labelled -->

## :octicons-download-24: 1. Install

The composer runs on your own computer. It works on macOS and Linux; Windows is untested. You need
[uv](https://docs.astral.sh/uv/), which fetches everything else the first time it runs.

From a copy of the repository, run `software/choreo/studio-up.sh`. It opens three things, all on
your computer only: the composer at `http://127.0.0.1:8091/`, a preview of the robot's face in the
browser, and a 3D preview of the whole robot in its own window. `software/choreo/studio-down.sh`
stops them.

## :octicons-unmute-24: 2. Bring the music

Press **♫ music**. Drop in an audio file, or pick a song from the library and press **use**. A song
added to the library records where it came from and its licence.

Then set the tempo, so cues can sit on beats and bars. Type the **BPM** and the time of beat 1, or
play the song and press **tap** on each beat, then **use**. To set the first downbeat, stop the
playhead on it and press **beat 1 ⇤**. Cues placed on the beat grid follow the tempo if you change
it later.

<!-- screenshot: ../assets/composer/composer-02-music.png — ♫ music, tap / use, beat 1 ⇤, the ruler grid -->

## :octicons-cpu-24: 3. Know the robot

A show is checked against a real robot's arm limits. Copy them from the robot once with
`opendubs-studio limits --from-robot <robot>`. Until you do, the composer shows **TEST LIMITS**, and
its checks use test values.

Each lane in the composer is one part of the robot: the two arms and grippers, the head, the lift,
sound, face and lights. A cue for a part the robot cannot play yet is still valid, and the
composer says so in a note.

## :octicons-pencil-24: 4. Write the show

There are three ways to write a show, and you can mix them.

- **By hand.** Each lane's **+** adds a cue at the playhead. Drag a cue to move it in time, and tap
  it to change it in the **Cue** panel. **save** (Ctrl- or Cmd-S) writes the file.
- **With notes and an agent.** On the **NOTES** lane, **+** adds a note at the playhead: say what
  should happen there, in plain words. Then **⧉** copies the open notes as a prompt for a coding
  agent, such as Claude Code, which writes the cues into the file. Save first: the agent works on
  the saved file.
- **By an agent alone.** An agent can write a whole show file from the show format
  (`notes/design/choreo-format-v1.md` in the repository). Open it in the composer to check and
  adjust it.

<!-- screenshot: ../assets/composer/composer-03-cues.png — a lane's +, a placed cue, the Cue panel -->
<!-- screenshot: ../assets/composer/composer-04-notes.png — the NOTES lane, its + and ⧉ -->

## :octicons-checklist-24: 5. Check it

The composer re-checks the show after every change. The status chip reads **valid**,
**valid · N warnings** or **N problems**, and the **Validator** panel lists each one: arm limits,
speed caps, and the clearance between the shoulders and the head.

The two previews follow the playhead: the face in the browser, the whole robot in the 3D window.
Press **▶︎** to play the music and the preview together. To share a preview as a video, render it
with `opendubs-studio render <show> --out show.mp4`.

<!-- screenshot: ../assets/composer/composer-05-validate.png — the status chip and the Validator panel -->
<!-- screenshot: ../assets/composer/composer-06-twin.png — the 3D twin and the face twin at the playhead -->
<!-- screenshot: ../assets/composer/composer-07-render.png — the render command and a frame of its output -->

## :octicons-comment-24: 6. Correct it

When something in the preview is wrong, stop at that moment and add a note there on the
**NOTES** lane, saying what should change. Copy the notes as a prompt with **⧉** and give it to the
agent. It edits the show, checks it again, and marks each note done or declined.

Earlier versions of the show are kept: **history** brings one back.

## :octicons-play-24: 7. Run it live

Send the show to the robot with `python3 software/choreo/show_sync.py push <robot> <show>.yaml`. The
push refuses a show that does not pass its checks, or that uses music the robot does not have.

On the dashboard, open **Shows — routines** and tap the routine. Shows need **CH5 in AUTO**. A run
first arms. It then starts when you press the deadman, or after a countdown, chosen with **Start**.
Letting go of the deadman stops the show, and the arms hold where they are.

<!-- screenshot: ../assets/composer/composer-08-shows.png — the dashboard's Shows page -->

!!! note "Planned"
    Not in the composer yet: analysing a song for its beats and sections; listing what each
    payload module can do; reusable show skills; more output devices, such as DMX stage lights;
    cues that adapt to what the robot senses; and handing one part of a show to an agent to
    perform live.
