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
3. **GO.** GO makes the selected show READY; nothing moves until the deadman. STOP sits beside it. What
   happened shows beside GO: READY and the countdown, or the refusal with its code and help link, and
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

**Playing it, on the head and not the hub** (Face & HRI's advice, 2026-10-28). The show-audio hub has
one holder per output, and any browser pressing "Play show audio here" takes the tablet output, so
dialogue must not depend on it. The runner sends the HEAD a new message, through the head protocol
like FACE and GAZE:
- `LINE load <id> <url>`, sent while armed; the face page fetches and decodes it, then answers `LINE
  ready <id>`;
- `LINE play <id> <t0_mono>` on the show's clock;
- `LINE stop`, on every end, cut on an e-stop.

The face plays it on voice.js's existing speech path, which already has its own audio context, a
0.12 s jitter buffer, lip-sync from the sound actually playing, no gesture needed in the kiosk, and
the head's VOLUME. The stage box is not on the hub, so music on the box and dialogue on the head
never contend; the stage-pi README already says the robot's dialogue plays on its head tablet.
`dialogue: {output: stage | tablet}` stays possible for a show without a face; their timing is the
existing paths'.

**Skins.** Audio sits outside the skin layer: a SKIN cue never cuts a line, and no skin owns dialogue.
A skin that draws no mouth (the ghost) plays the line with no lip-sync, and the page says so on that
show.

**Who yields on the head's speaker** (proposed; to agree with the Voice lane before anyone builds it).
voice.js has no arbitration today: a voice reply and a show's line would simply overlap in one
audio context. The order, highest first:
1. **Safety earcons** (an e-stop, a fault): always heard, over anything.
2. **A show's dialogue, while a show runs:** the show owns the head's speaker from ARMED to its end.
   The wake word is suppressed, a Talk press does not cut a line, and a voice reply waits until the
   show ends rather than being dropped mid-word.
3. **The voice service's replies**, outside a show.
4. **Other earcons** (acknowledgements): ducked under speech, never cutting it.
5. **AirPlay and ambient music:** already muted by the hub during a show; they duck under speech
   otherwise.

ARCHITECT's speech plays on its own speakerphone today, not the head, so it is not a claimant. If it
moves to the head, it ranks with the voice service's replies.

**Refusal.** A chosen dialogue output that is not ready at preflight refuses the run
(SHOW_AUDIO_OUTPUT_MISSING), as for music. For `face`, that means the head not up, or no `LINE ready`
by GO.

**Who does what.**
- The `LINE` message is a new head message: agreed with the Tablet lane, which owns the head
  protocol.
- The player in voice.js is agreed by Tablet and Voice.
- Face & HRI builds the mouth and the speaking look.
- Choreography builds the format, the runner's routing and readiness, and the page's *Dialogue plays
  on* row.
- ENCORE exports the narration as its own track instead of mixing it into the song.
