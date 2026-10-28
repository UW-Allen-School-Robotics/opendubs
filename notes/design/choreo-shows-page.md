# The Shows page

Choreography lane; the Video and Sound rows and the projector and stage box detail are the Tablet lane's.
Rebuilt on 2026-10-28, after the user found the page overwhelming: the only sound they had got out in
a day of trying was the stage box's TEST SOUND.

## The rule

- **Every control has a one-line label saying what it does.** A tooltip alone does not count.
- **A new control earns a place on the main page, or goes under Advanced.** The main page holds only
  what an operator needs to run a show at an event.
- **No setting silently overrides another.** Where a choice beats another (the operator's beats a
  show's own, and a video on the stage brings the sound with it), the page says so where it happens.

## The page

1. **Before you start.**
   - *Video plays on* and *Sound plays on*, for every show started from here, each with one ready or
     not-ready line and the reason. TEST SOUND tests the chosen speaker.
   - *Robot*: mode, e-stop, faults and the head. It is read-only and checked again at GO.
   - On a robot with a stage box, both pickers start on the stage (the user's decision). They last
     until changed. "Each show decides" hands the choice back to each show.
2. **Pick a show.** A tap selects; it never starts. Each show says where its video and sound will go
   and why: *your choice*, *the show's own* or *the robot's default*. In amber, when your choice
   replaces the show's own. The runner works this out (`plays_on` on each routine), so the page never
   guesses.
3. **GO.** GO arms the selected show; nothing moves until the deadman. STOP sits beside it. What
   happened shows beside GO: ARMED and the countdown, or the refusal with its code and help link, and
   RUN WITHOUT … when a part can be left out.
4. **Advanced, folded.** The start mode and countdown, this browser as the head tablet's speaker, and
   the Tablet lane's timing leads and buffer, stage box setup (speaker, volumes, pairing, software)
   and diagnostics.

Precedence, the same in a run and on the page: the operator's choice, else the show's own
(`video.output`, `music.output`), else the robot's default. For sound, that default is the stage box
when the show's video plays on the stage (ready or not, so a stage box that is down refuses the run,
SHOW_AUDIO_OUTPUT_MISSING, rather than moving the sound elsewhere), else the head tablet.

## Proposed next: dialogue on the face

A show's spoken lines (text-to-speech narration, as in Werewolves in Gates, which today is mixed into
its song) should be able to play from the head tablet, under the face, while the music plays from the
stage box.

**Format.** Dialogue is the existing *dialogue* role: SOUND cues `{slot: SOUND, do: play, sound: …,
role: dialogue}`, or a whole narration track `music: {…, role: dialogue}` beside the song. A show
names where its dialogue goes with `dialogue: {output: face | tablet | stage}`. Music and dialogue are
then two separate choices, each with its own operator override on the page (*Dialogue plays on*).

**Playing it.** The face page joins the show-audio hub as a third output, `face`. It uses the hub's
existing commands (load the files, start at a Thor time, stop, cut), which already keep a device on
the show's clock, and drives the mouth from the sound it is playing. The hub carries one load per
output, so a run can load music on one device and dialogue on another.

**Refusal.** A chosen dialogue output that is not connected at preflight refuses the run
(SHOW_AUDIO_OUTPUT_MISSING), as for music.

**Who does what.**
- The `face` client on the hub is a head message, so it is agreed with the Tablet lane, which owns the
  head protocol.
- Face & HRI builds the face page's side.
- Choreography builds the format, the runner, the hub's per-output loads and the page.
- ENCORE exports the narration as its own track instead of mixing it into the song.
