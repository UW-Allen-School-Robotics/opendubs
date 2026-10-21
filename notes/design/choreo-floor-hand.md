# The floor hand: a real crawling hand in a show

A show's crawling hand (`crawler:` and its CRAWL cues, the tool registry in `software/choreo/effectors/`)
has run in the twin since the registry landed. This note is how the **real** hand plays one: an Alt
Bionics Surge hand on the floor in front of the robot, crawling and dancing its fingers in time with the
show. Owned by the Choreography lane; the arm tools stay with the Arms lane.

## What drives it

- **The link:** the hand's own text commands over RS-485 at 115200 baud (`drivers/surge_at.py`, found on
  the real hand). The robot's computer has a USB-to-RS-485 adapter; a cable runs from the robot's front to
  the hand, alongside its power, as one floor tether (the leash the twin already models: `tether`, anchored
  at `base_front`). RS-485 is built for a cable of this length.
- **The runner's floor-hand output** (`hand_output.py`, `--hand PORT`; per robot, `OPENDUBS_FLOOR_HAND` in
  `robot.env`, best a `/dev/serial/by-id/` path). A live run turns the show's CRAWL track into the real
  hand's moves (`effectors.hardware_sequence`: each segment stamped with its show time) and plays them **on
  the show's clock** (`surge_at.ShowPlayer`), at 20 Hz, in its own thread like the projector's video:
  - the port opens while the arms travel to their start pose; nothing moves before the show starts;
  - targets stream from the show's start;
  - on **every** end (complete, operator stop, abort, e-stop) the hand is sent its OPEN pose and command
    replies are turned back on. The hand has no watchdog: a target it is never told to leave, it holds.
- **The part** is `floor_hand` (`cue.EFFECTORS`, from the CRAWL slot), so a show that crawls needs it and
  may list it in `can_skip:`.

## When it plays — and the fallback

At preflight, on a live run, the floor hand answers for itself (`hand_output.availability`). It is
available only when:

1. the show's hand type has a driver (`output: surge_at`; today only `surge_left`, the hand that exists);
2. this robot has it fitted on the floor (its own tools file, `base: surge_left`);
3. every crawl move in the show **has run on the real hand** (the type's `hardware: tested_gaits` and
   `tested_patterns`): today the forward walk `bare_flexstroke` and the ripple, wave and idle beat-tap
   dances. A pivot, a backward walk or any other dance is simulation only and is not played on a floor
   with people on it until it has passed a bench test;
4. this runner has a floor-hand port, and the hand answers a query on it (the query moves nothing).

Otherwise the run is refused with the reason (EFFECTOR_UNAVAILABLE) — or, for a show that lists
`floor_hand` in `can_skip:`, the operator is offered the show without the hand (NEEDS_APPROVAL, the
effector-needs path), and approved, it runs without it, the hand untouched. A hand that stops answering
mid-show is logged and dropped; the show goes on. A runner without arms (the twin) plays the crawl in the
twin only and never asks the hand.

## Safety

- **Power** from the 24 V branch the e-stop contactor switches, fused at the robot end (a step-down
  converter on that branch if the hand needs less). An e-stop cuts the hand's power whatever the software
  does. Never the battery pack directly, never an always-on rail.
- It moves only during a live run, under the arms' gates (AUTO, the deadman held, no e-stop).
- No closing grips on the real hand: finger dances keep the thumb beside the palm (a finger closing onto it
  latches an error), and the closing presets are refused by the driver.
- The floor: a marked zone in front of the robot (the show's `area:`), kept clear by the operator; the
  tether taped flat or flagged. Someone places the hand at its start mark before each run and collects it
  after. Open loop, with no sensing: its path is a plan, not a measurement, so the area has margin.

## Open: decided by the user before anything is connected

- The hand's input voltage range and peak current (label or datasheet), and what powered it on the bench.
- The adapter and cable from the robot's computer to the hand.
- The floor tether and the e-stop-switched 24 V branch (above).
- The venue's floor surface: the walk's speed was measured on one floor only (about 10–13 cm/s).
- A bench test of each move before it joins `tested_gaits` / `tested_patterns`.
