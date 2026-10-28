# A Spot in OpenDubs shows

A Boston Dynamics Spot can share a show with opendubs2: ENCORE, the choreography studio, already
authors Spot's part (its `spot@arm-v1` profile, the `spot.*` snippets, and the Werewolves and Duet
shows). This note plans the **driver** that plays that part on a real Spot, on the same clock as
opendubs2's show runner. Owned by the Spot lane, managed by the Choreography lane. **Status: a plan.
Nothing is built and nothing has been sent to a Spot.**

## Fixed

- **Nothing moves without a person present and the maintainers' GO,** step by step, as for opendubs2.
- **A person holds Spot's E-Stop** (Spot's tablet or controller). The driver is never the E-Stop client,
  so a crash in the driver can't be what stops Spot, nor what keeps it going.
- **The mapping from ENCORE cues to Spot commands lives once, in ENCORE** (`spot_driver.plan()`). The
  driver takes that plan as its input, re-checks it and sends it. When the plan lacks something the
  driver needs (Spot's limits, a window field, a refusal), the change is asked of Choreography and
  made in ENCORE, never forked here.
- **The driver implements ENCORE's player interface** (`describe`, `load`, `ready`, `start(at)`, `stop`,
  `status`; ENCORE `docs/design/shows-and-programs.md`). Choreography's decision, 2026-10-27.
- **Per-unit values stay off the repository:** Spot's address, its credentials, and its limits once
  measured.

## Open: for the maintainers

1. **The arm.** Is the arm and gripper fitted? ENCORE's `spot@arm-v1` assumes it; without it, any show
   with ARM or GRIP is refused.
2. **The software release** (on Spot's tablet: Settings, About). `bosdyn-client` is pinned to match it.
3. **The Choreography licence.** Boston Dynamics' Choreography service needs one. Without it, the body,
   arm and jaw cues still play, but Spot can't step on the beat.
4. **Who holds the stops.** Recommended: two people for the first runs, one on opendubs2's deadman and
   one on Spot's E-Stop.

## Spot's interface

The Boston Dynamics Spot SDK, Python (`bosdyn-client`), pinned to Spot's release, never vendored.
Hygiene checks its licence before this repository is public.

- **Login.** `robot.authenticate()` with Spot's user and password, typed at a hidden prompt (see
  Secrets), then `robot.time_sync.wait_for_sync()`.
- **The lease.** Spot's tablet holds it by default. The person at the tablet releases control, then
  the driver acquires the lease and keeps it alive. The driver never takes it by force.
- **The E-Stop.** A person's tablet or controller registers the E-Stop endpoint and checks in. The
  driver reads the E-Stop status (read-only) and refuses to power on or send while no endpoint is
  registered, or one is stopped. Where a stop level is chosen, SETTLE_THEN_CUT (Spot sits, then power is
  cut) over CUT (power cut at once: Spot drops).
- **Two ways to move Spot:**
  - **A. Plain commands (no licence), built first.** ENCORE's spec:
    - BODY: a stand command whose mobility parameters carry a `base_offset_rt_footprint` SE3 trajectory
      (the body moves over planted feet);
    - ARM: an `ArmJointTrajectory`, six joints with velocities;
    - GRIP: a claw-gripper open-fraction trajectory (the jaw: 0 closed, 1 open);
    - BASE: an SE2 trajectory (where Spot stands; Spot plans its own steps);
    - one synchro command per window (20 s, a placeholder), each with a `reference_time` from TimeSync.
  - **B. The Choreography service (licence needed), added later if available.** Spot's own dance engine:
    a sequence cut into quarter-beat slices at one tempo, started at a time on Spot's clock, with
    ENCORE's keyframes uploaded as animated moves. Only B can step on the beat (`march`).
- **The mouth.** The gripper's jaw, GRIP `open`. The arm is unfolded and folded by the show itself
  (`spot.unstow`, `spot.settle`).

## Mapping ENCORE's cues

| Cue | Parts | Mode A | Notes |
|---|---|---|---|
| `spot.unstow`, `spot.settle` | ARM (+ BODY, GRIP) | yes | |
| `spot.head_bob`, `spot.head_tilt`, `spot.wag` | BODY, ARM | yes | |
| `spot.howl` | ARM, GRIP, BODY | yes | the jaw opens on the note |
| `spot.play_bow` | ARM, BODY | yes | front down, within the body's pitch range |
| `spot.sniff`, `spot.sniff_slow_lift` | ARM, BODY, BASE | yes | the 0.15 m creep forward is Spot's own gait |
| `spot.turn` | BASE | yes | turns up to 60 deg; Spot's own steps |
| Duet's BODY track | BODY | yes | |
| `spot.march` | BASE `march`, `y` | **B only** | in A the side-steps play and the march is dropped, said in the log |
| `spot.react` (rule) | | **refused** | needs `touch` and `person_seen` events Spot doesn't produce yet |
| `spot.wag_policy`, `spot.keyboard` | | **refused** | a live stream needs the runtime guard in the driver: the last step of the rollout |

**Limits.** The profile's body ranges and speed caps are placeholders, not Boston Dynamics' figures.
Before a run, the driver re-checks every point against the limits Spot reports and those measured,
and refuses anything outside them. Spot enforces its own limits on top. Spot has no face: a show's
FACE tracks stay opendubs2's.

## Timing and the shared clock

- **opendubs2's runner is the home.** The GO stays a fresh press of opendubs2's deadman, as today. The
  driver follows the runner's read-only status and never starts, arms or GOes anything.
- **The start:**
  1. After the GO, the runner counts down, then waits its start lead before t = 0.
  2. The driver reads t0 from the runner's status and maps it onto Spot's clock. The Thor's clock is
     mapped to the driver's machine by a round-trip offset against the runner's port; the driver's
     machine is mapped to Spot by the SDK's TimeSync.
  3. It sends Spot's first window ahead by Spot's lead (1 s, a placeholder until measured), and each
     later window the same lead ahead of its start.
  4. It refuses to start while either offset is unknown or over 20 ms (ENCORE's proposed tolerance).
- **Needed from the runner: `t0_wall`,** the epoch second the show's t = 0 lands on, in the status
  from WAITING_FOR_START onward. Today the status has `now`, plus `lead_s` in WAITING_FOR_START and `t`
  in RUNNING; the runner's own clock isn't wall time. Agreed with Choreography, 2026-10-27; built
  after the demo freeze. Until then the driver is built and tested against `runner.py` on a Mac with no
  robot, started with `start="countdown"` to get a WAITING_FOR_START phase. Rollout steps 1 to 5 don't
  need it.
- **Stopping:**
  - When the runner leaves RUNNING (completed, aborted, e-stopped), the driver drops Spot's remaining
    windows and sends a stop: Spot holds its stand, the arm where it is, as opendubs2's arms do.
  - Every command carries a short end time, so a driver that stops sending leaves Spot still, not
    finishing a 20 s window.
  - When Spot stops (its E-Stop, a lost lease, a refused command), the driver POSTs the runner's
    `/api/stop`, best effort, and logs why. Stop only: never `/api/run` or anything that starts or arms.
    Agreed with Choreography, 2026-10-27.

## Safety

- **Speed.** Spot's `vel_limit` is capped low, in the spirit of opendubs2's ~25% cap on AUTO base
  motion. BASE stays within ±0.5 m and ±60 deg of where Spot started (the profile's ranges). Spot's own
  obstacle avoidance stays on, so it treats opendubs2 as an obstacle.
- **Keep-out.**
  - The shows place Spot about 1.7 m from opendubs2 (`at: [0.9, -1.4]`).
  - Before a run, the driver checks the swept envelopes stay at least 0.5 m apart: Spot's 0.65 m
    footprint radius with the arm at its furthest pose (`sniff`), against opendubs2's arm reach and dance
    excursion.
  - People stay outside a 3 m clear radius around Spot, the figure Spot's choreography documentation
    gives (`choreo-format-v1.md`).
- **When the driver dies.** Its commands expire and Spot stands still. Losing the lease does not by
  itself make Spot sit: per Boston Dynamics' documentation, sitting and powering off on a lost link
  comes from the E-Stop's check-in timeout or a policy set with the Keepalive service. So the driver
  registers a Keepalive policy (sit after a few seconds without the driver) where the release has that
  service, and the person's E-Stop stays the backstop. What Spot actually does is checked with its
  motors off, before it ever stands.
- **Rollout, one step per GO, a person on Spot's E-Stop throughout:**
  1. Connect read-only, motors off: the release, the licences, the limits, the E-Stop status, the
     lost-link behaviour.
  2. Power on, stand, stop, sit.
  3. One body-only track (Duet's BODY).
  4. The arm and jaw (`spot.howl`).
  5. The base (`spot.turn`).
  6. Two robots on one clock (needs `t0_wall`).
  7. Live behaviours, last.

## Network

- **Spot joins opendubs2's Wi-Fi** in client mode. A maintainer types the Wi-Fi password into Spot's
  admin page. It is never shown to a session, logged or committed.
- **Spot's own access point turns off in client mode,** so the E-Stop tablet joins opendubs2's Wi-Fi
  too. Its check-ins then share the robot's access point: a Wi-Fi dropout stops Spot, by design.
- **Spot's address.** Spot takes an address by DHCP. A reserved address or a name is a router change:
  the Network lane's, made only with the maintainers' word and a person at the robot. Until then the
  address is read off Spot's tablet for the session and never written to the repository.

## Secrets

- Spot's user and password are typed only at a hidden prompt, in a terminal application separate from
  any agent's tools. They are never passed as arguments, never put in an environment file, and never
  enter any repository. Any session token stays in the driver's memory.
- So the authenticated driver runs in that terminal. Its JSON-lines log carries no secrets and lives
  outside every repository (`~/.opendubs-spot/logs/`).

## Where the code goes

**Open: the maintainers decide.** Proposed: `software/spot/` in this repository, owned by a Spot lane
(the lanes table would need its row); the alternative is a separate private repository. Either way
the driver runs on the operator's machine and is not deployed by `deploy-all.sh`: nothing on
opendubs2 changes.
