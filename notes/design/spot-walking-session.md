# The first walking session at Spot: a runbook

**Status:** planned. The owner asked for the remaining measurements to be bundled into one session
(2026-11-26). It takes about 45 minutes. It answers four questions for ENCORE's duet note and for
Sugar Plum:
1. how often Spot sees a tag on opendubs2;
2. how far Spot's `vision` frame drifts on a lap;
3. whether walking windows chain without a stutter;
4. which step style reads as tip-toeing.

**The rule for the whole session:** nothing moves Spot until the owner says GO for that step, with
someone at Spot. A person stays on STOP (the operator page, or the space bar) for every step that walks.
The tag logger only reads. It takes no lease and sends no command.

## Before the session (the Spot lane, at a desk)

- Sync the Orin to the commit with walking (`software/spot/orin/sync.sh`), and copy
  `walk-check.spot-plan.json` into the Orin's `~/.opendubs-spot/plans/`. Build it with
  `software/spot/shows/walk_check.py`.
- Print four AprilTags: family tag36h11, Boston Dynamics' standard 146 mm size, IDs 1 to 4 (never 500 to
  549, which are for docks). Print them flat and matte.

## At Spot

**0. Setup (no motion).** Find a smooth indoor floor with a clear circle about 4 m across. Fix one tag
flat on each face of opendubs2's base, at the height of Spot's side cameras, and check that the arms
don't cover them. Spot sits. The tablet holds control for steps 1 and 2.

**1. Tag sighting rate (GO: Spot stands still; about 5 min).**
- Stand Spot from the tablet. Put opendubs2 1.45 m to Spot's left, with a tag facing Spot.
- In the separate Terminal app, run `software/spot/capture/tags-log.sh left-145`. It logs for 60 s.
- Move opendubs2 to Spot's right and run `tags-log.sh right-145`.
- The result is each tag's sighting rate, the longest gap between sightings, and the detection's own
  noise (its wander while nothing moves).

**2. Vision drift (GO: the owner drives one lap on the tablet; about 5 min).**
- Run `tags-log.sh lap-145 180`, then drive Spot slowly once round opendubs2 at about 1.45 m, back to
  where it started.
- The tag doesn't move, so its wander in the log beyond step 1's noise is the drift. The log also shows
  which faces Spot saw from where, and how long it went without a sighting.
- Release control on the tablet afterwards. The player needs the lease and the E-Stop.

**3. Walking windows chain without a stutter (GO: Spot walks 0.4 m forward and back; about 10 min).**
- Start the player with walking: `software/spot/orin/start.sh --walking`. The owner's GO is this
  step's word for `--walking`.
- On the operator page, press Release, then Power on + stand.
- Load `walk-check` and press Ready at 50 %, then Start. Spot stays within 0.4 m of where it started, at
  about 0.05 m/s at most.
- Watch at 10, 20, 30 and 40 s (20, 40, 60 and 80 s at 50 %). Each walk crosses a window boundary
  there, so a pause or a lurch at those moments is a chaining fault.
- Then play it at 100 %.

**4. The step look for Sugar Plum (the same plan, watched for its style).**
- In `walk-check`, 5–25 s is CRAWL with swing HIGH, and 25–45 s is TROT with swing HIGH. 45–55 s turns
  45° in place.
- The owner judges by eye which reads as tip-toeing. Replay it as often as wanted.

**4b. Sugar Plum at its own speed (GO: Spot walks the show's 1.45 m circle round opendubs2).**
- Play Sugar Plum itself, at 50 % and then at 100 %. Its tip-toe sections carry CRAWL with swing HIGH.
  Its promenade peaks at 0.32 m/s, far faster than walk-check's crawl.
- Watch whether Spot keeps up with the music on the circle. Spot holds to its own top speed for each
  gait, so a crawl that is too slow falls behind the song. That is not unsafe.
- If it lags, crawl the steps in place and use a slow TROT with swing HIGH for the promenade.

**End.** Sit + power off. Copy `~/captures/fiducials/` off the Orin to OpenDubs Data.

## What each answer feeds

- **Steps 1 and 2** set how the duet re-anchors Spot on opendubs2's tags: how often it can correct, and
  how long the guard waits without a sighting before it stops the lap.
- **Step 3** confirms the 5 s windows ENCORE proposes for the laps.
- **Step 4** sets the `mobility` ENCORE writes on Sugar Plum's BASE track.
