# Making a show

The [quickstart](quickstart.md) gets you from installing the composer to running a first show. This
page goes further: what each part of the studio shows you, how the checks keep the robot safe, every
place a show can play (the tablet, a projector, a stage screen, the floor), and how to work with a
coding agent. It assumes you have done the quickstart once.

A **show** is a file: a cue sheet that says what each part of the robot does and when. On the robot,
one program, the **runner**, reads the show and plays it, and it is the only thing that moves the
robot for a show. Everything on this page except the last two sections happens on your own computer
and cannot move a robot.

## The studio

`software/choreo/studio-up.sh` starts three things, all on your computer:

| | What it shows |
|---|---|
| **The composer** (in the browser) | The show: one lane per part of the robot, the bar and beat ruler over the song, the selected cue, and the validator's verdict. |
| **The face twin** (in the browser) | The robot's real face page, playing the show's face cues: expressions, eye moves, colours. |
| **The 3D twin** (its own window) | The whole robot, built from its real 3D model: arms, grippers, head, lift and base, moving exactly as the show will move them. A projection screen behind it plays the show's video. |

The twins follow the composer's playhead, so you see the show as you edit it. The 3D twin uses the
same model the safety checks use, so what you see is what is checked. To stop everything, run
`software/choreo/studio-down.sh`.

## Music and sound

Every song, video and image a show plays comes from the **show library**: a folder of media kept
outside the repository, each item recorded with where it came from and its licence. The robot has
its own copy of the library; a show names items by their library id, so it can travel to the robot
without its media.

Set the tempo carefully. Cues written on the beat grid (`bar: "9.1"` means bar 9, beat 1) stay on
the music only if the grid matches the song. Tap along with **tap**, check with the metronome
(**click**), and set **beat 1** on the first downbeat. If the bar lines feel half a bar off, move
beat 1, not the cues.

Each sound plays as **dialogue** or **music**:

- **Dialogue** (the default) always plays from the robot's head tablet, so the voice comes from the
  robot.
- **Music** plays from a projector or stage speaker when one is ready. The tablet keeps a silent copy
  and takes over at the same point in the song if the projector drops out.

A library video can carry the show's song in its own soundtrack. Then the projector plays the music
with the picture, in step.

## What a show can do

Each lane is one part of the robot. The validator knows each part's limits and checks every cue.

| Part | What a cue does |
|---|---|
| Arms | Move to joint angles, or reach a point (the composer works out the angles: an **IK target**). |
| Grippers | Open and close. |
| Head | Turn and tilt. |
| Face | Show an expression, move the eyes, change the look. |
| Lift | Raise or lower the arms. |
| Light | Set the robot's light colour. |
| Sound | Play a sound file. |
| Video | Play a library video on the projector or stage screen. |
| Dance | Small steps of the base that always return to where they started. |
| Crawl | A robotic hand on the floor in front of the robot walks, turns and dances its fingers. |

**Clips** are reusable stretches of a show, timed in beats: write a dance move once and place it
again, repeated, slowed down or mirrored left to right.

## The checks, and why they matter

The composer re-checks the show after every change, and the robot checks it again before every run.
A show that fails is refused, not trimmed.

- **Joint limits.** Each arm joint may only turn so far. The checks use the real robot's limits,
  copied from the robot (see the quickstart).
- **Speed caps.** Each joint may only turn so fast. The caps were measured on the robot: faster than
  this, the arm falls behind its path.
- **Clearance.** The arms must stay at least 30 mm from each other and from the robot's own body,
  the whole way through every move, not only at each cue. When you save, the composer **sweeps** the
  show: it plays the whole show on the 3D twin's collision model and records the closest approach in
  the file. Change any movement and the record is out of date until the next save; the robot refuses
  a show with an out-of-date or failing record.
- **IK targets** are solved on your computer, and the angles are written into the show. The robot
  plays those angles exactly, so it does what the twin showed.

Warnings are worth reading too. "May peak" means a move is fast enough that, if the arm eases in
and out of it, it could briefly pass a speed cap. Give it more time when you can.

## Working with a coding agent

Notes are how you ask for changes in plain words. On the **NOTES** lane, add a note over the stretch
of the show it is about, and say what should happen: "the arms should open wider here", "make this
land on the downbeat", "the head should follow the right hand". Good notes say what is wrong and what
you want instead, not how to fix it.

When the agent works on the show, it first keeps a copy of the show as it was. It edits the cues
(never your notes), checks the show again, and replies on each note: **done**, with a line saying
what it did, or **declined**, with the reason. The composer notices the change, shows you the
difference, and can bring back any earlier version from **history**.

## Where a show plays

| Output | What it is |
|---|---|
| The 3D twin and face twin | On your computer, while you edit. |
| A video file | A render of the 3D twin, with the face and the song: `uv run software/choreo/studio/studio.py render <show> --out show.mp4`. Render a short stretch first (`render.py` takes `--from` and `--to`) to check the framing. |
| The robot | Arms, grippers, head, face, lift and light. |
| The head tablet | Dialogue, and the face. |
| A projector | The show's video, by casting to the projector over the robot's network, or over an HDMI cable. |
| A stage screen | A small computer kept on the robot and plugged into any screen (a projector, a video wall, a TV) shows the **stage page**. It plays the show's video from its own stored copy when it has one, and otherwise streams it from the robot, waiting until enough is buffered before the show starts. Between shows it shows a status card. |
| Live skeletons | On the stage page, people standing in front of the robot's camera appear as dancing cartoon skeletons. Only the positions of their joints are used: no camera image is ever shown, sent or stored. |
| The floor hand | A robotic hand on a tether in front of the robot, playing the show's crawl cues. |
| The base | Dance steps play in the 3D twin. On the robot they need the base tracker, which follows the steps using the base's own odometry and stops for people in the way. |

When a show uses a projector and none is ready, the show runs without its video and says why, unless
the show insists on it (`video: {required: true}`); then it is refused.

### Parts a show can do without

A show **needs** every part its cues use. If one is not working, the robot refuses the show, unless
the show lists that part under `can_skip:`. Then the operator is asked whether to run the show
without it, and an approved run plays everything else. Use this for parts that add to a show but
are not its point, such as the floor hand.

## Sending a show to the robot

Two steps, from your computer:

1. Send any library items the show uses that the robot does not have:
   `python3 software/choreo/library_sync.py push <robot> <item>`.
2. Send the show: `python3 software/choreo/show_sync.py push <robot> <show>.yaml`. First run it with
   `--dry-run --limits-from-robot` to check the show against the robot's own limits without sending
   anything.

Shows sent this way live in the robot's own shows folder, separate from the software, so an update
of the robot's software never removes them.

## Running it

On the robot's dashboard, **Shows** lists every show the robot has. A run starts **READY**: the
robot loads the music and video, then waits — nothing moves until you press and hold the
**deadman**. With **CH5** on the radio controller in **AUTO**, pressing the deadman shows
**STARTING in N**, a short countdown that warns the robot will move, then **SHOW RUNNING**.
Letting go of the deadman, leaving AUTO, the e-stop, or any fault ends the run at once, as
**SHOW STOPPED**: the arms hold where they are, the base and the floor hand stop, the hand's
fingers open, and the music stops. A show that plays to its end reads **SHOW FINISHED**.

## The rules that keep it safe

These hold for everyone who works on shows or the software around them:

- Only the runner moves the robot for a show. Previews, renders and tools never send movement to the
  robot.
- The safety limits (the 30 mm clearance, the measured speed caps, each robot's joint limits) are
  never loosened to make a show pass. Change the show instead.
- A clearance or speed check counts only for the movement the robot actually plays.
- Each robot's own settings (its joint limits, calibrations, which tools are fitted) stay on that
  robot.
- Songs, videos and renders stay in the show library and on the robot, never in the repository.
