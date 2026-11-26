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

## This Spot (from the maintainers, 2026-10-27)

- **The arm and gripper are fitted,** so ENCORE's `spot@arm-v1` profile applies.
- **The latest software release.** Confirmed at the robot, and updated there if needed.
  `bosdyn-client` is pinned to whatever release it reports.
- **No Choreography licence.** The driver uses only the basic mobility, arm and gripper commands. It
  doesn't use the Choreography service or uploaded dance sequences.
- **One person runs a two-robot show** and must be able to reach both stops: opendubs2's deadman and
  e-stop, and Spot's E-Stop (its tablet or controller). The two are separate devices. Letting go of
  opendubs2's deadman ends the run, and the driver then stops Spot (see Stopping); Spot's own E-Stop
  stays the backstop for Spot.

## Spot's interface

The Boston Dynamics Spot SDK, Python (`bosdyn-client`), pinned to Spot's release, never vendored.
Hygiene checks its licence before this repository is public.

- **Login.** `robot.authenticate()` with Spot's user and password, read from the Keychain (see
  Secrets), then `robot.time_sync.wait_for_sync()`.
- **The lease.** Spot's tablet holds it by default. The person at the tablet releases control, then
  the driver acquires the lease and keeps it alive. The driver never takes it by force.
- **The E-Stop.** A person's tablet or controller registers the E-Stop endpoint and checks in. The
  driver reads the E-Stop status (read-only) and refuses to power on or send while no endpoint is
  registered, or one is stopped. Where a stop level is chosen, SETTLE_THEN_CUT (Spot sits, then power is
  cut) over CUT (power cut at once: Spot drops).
- **Moving Spot: the basic commands only,** as ENCORE's spec has them:
  - BODY: a stand command whose mobility parameters carry a `base_offset_rt_footprint` SE3 trajectory
    (the body moves over planted feet);
  - ARM: an `ArmJointTrajectory`, six joints with velocities;
  - GRIP: a claw-gripper open-fraction trajectory (the jaw: 0 closed, 1 open);
  - BASE: an SE2 trajectory: where Spot stands. Spot plans its own steps and their timing, so a BASE move
    arrives roughly when asked, never step by step on the beat;
  - one synchro command per window (20 s, a placeholder), each with a `reference_time` from TimeSync.
- **The mouth.** The gripper's jaw, GRIP `open`. The arm is unfolded and folded by the show itself
  (`spot.unstow`, `spot.settle`).

## Mapping ENCORE's cues

Re-checked against the basic commands, with no Choreography licence:

| Cue | Parts | Plays | Notes |
|---|---|---|---|
| `spot.unstow`, `spot.settle` | ARM (+ BODY, GRIP) | yes | |
| `spot.head_bob`, `spot.head_tilt`, `spot.wag` | BODY, ARM | yes | the body and arm keep the beat: both are timed trajectories |
| `spot.howl` | ARM, GRIP, BODY | yes | the jaw opens on the note |
| `spot.play_bow` | ARM, BODY | yes | front down, within the body's pitch range |
| Duet's BODY track | BODY | yes | |
| `spot.sniff`, `spot.sniff_slow_lift` | ARM, BODY, BASE | **mostly** | the head and body play on time; the 0.15 m creep forward and back is Spot's own steps, roughly on time |
| `spot.turn` | BASE | **loosely** | the turn (up to 60 deg) arrives roughly when asked, in Spot's own steps, not stepping on the beats; with `march: 1` the march is dropped |
| `spot.march` | BASE `march`, `y` | **mostly not** | the side-steps (±0.09 m) play loosely in Spot's own steps; marching in place on the beat doesn't: the basic commands have no step-in-place. The driver drops `march` and says so in the log |
| `spot.react` (rule) | | **refused** | needs `touch` and `person_seen` events Spot doesn't produce yet |
| `spot.wag_policy`, `spot.keyboard` | | **refused** | a live stream needs the runtime guard in the driver: the last step of the rollout |

So for the Werewolves show, every head, body and jaw cue plays on the beat. Spot's stepping (the
marches and the turn) plays loosely or not at all. ENCORE's preview still draws Spot stepping on the
beats: a gap Choreography may want ENCORE to show, through a `spot` profile flag for a build without
the licence.

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
  - **This stop is best effort, not a safety function.** It crosses two network hops (the runner's
    status to the driver, the driver to Spot) and depends on the driver being alive. Its latency, as a
    target until measured at rollout step 6: the driver reads the runner's status at 10 Hz (up to
    0.1 s), plus the Wi-Fi round trips and Spot taking the command, under 0.5 s in all from the runner
    ending to Spot stopping. **Spot's own E-Stop is the real backstop** for Spot, as opendubs2's
    deadman and e-stop are for opendubs2.
  - A driver that stops sending leaves Spot finishing at most the window it already has. So windows
    are kept short (about 2 s, sent 1 s ahead), and every command carries an end time at its window's
    end. ENCORE's plan uses 20 s windows today: the window length becomes a plan parameter, asked of
    Choreography for ENCORE. Until then, the most a dead driver leaves playing is one window of
    already-checked motion.
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
- **The lease is held for as long as Spot is up.** On this Spot (5.1.5, 2026-11-19), returning the lease
  with the motors on made Spot sit and power off by itself a second or two later, with the E-Stop
  released and no fault. So the driver holds the lease from power-on to sit.
- **When the driver dies.** Its last window plays out (see Stopping), then Spot stands still. Losing the lease does not by
  itself make Spot sit: per Boston Dynamics' documentation, sitting and powering off on a lost link
  comes from the E-Stop's check-in timeout or a policy set with the Keepalive service. So the driver
  registers a Keepalive policy (sit after a few seconds without the driver) where the release has that
  service, and the person's E-Stop stays the backstop. What Spot actually does is checked with its
  motors off, before it ever stands.
- **Rollout, one step per GO, a person on Spot's E-Stop throughout:**
  1. Connect read-only, motors off: the release (updated first if it isn't the latest), the limits,
     the E-Stop status, the lost-link behaviour.
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

- Spot's user and password are stored once in the operator Mac's Keychain, typed at `security`'s hidden
  prompt in a terminal application separate from any agent's tools
  (`software/spot/spot_credentials.py` has the command). The item trusts no application, so each read
  by the driver pops a macOS dialog that the person at the Mac allows (Allow, never Always Allow).
- They are never passed as arguments, never put in an environment file, never printed, and never
  enter any repository. Any session token stays in the driver's memory.
- The driver's JSON-lines log carries no secrets and lives outside every repository
  (`~/.opendubs-spot/logs/`).

### The player's login on the Orin (planned; the owner's decision, 2026-11-20)

The Orin has no Keychain. The player gets **its own Spot user**, and that user's password goes in a
**file only root can read**. This is a plan only: nothing is created on Spot or the Orin until the
owner does it, at the robot, typing the password in the separate Terminal app.

1. **On Spot**, the owner adds a user for the player (for example `opendubs-player`) on Spot's admin
   page. It is separate from `admin`, so it can be removed without touching anyone else's login. If
   Spot offers a role below administrator that can still take the lease and register an E-Stop, use
   it; check that on the admin page.
2. **On the Orin**, over ssh from the separate Terminal app, the owner stores the password. It is
   read from a hidden prompt, so it never appears in a command line, the shell history or on screen:
   ```sh
   sudo install -d -m 700 -o root -g root /etc/opendubs-spot
   sudo bash -c 'umask 077; IFS= read -rsp "Spot password: " p; echo; printf %s "$p" > /etc/opendubs-spot/spot-player.password'
   ```
   `bash`, not `sh`: Ubuntu's `sh` has no hidden `read -s`. `printf` is bash's own command, so the
   password never appears in a process list. The file is `root:root`, mode `0600`. The user name is
   not secret and goes in the service file.
3. **The player runs as an unprivileged system user**, as a systemd service. The service gets the
   password through `LoadCredential=spot-password:/etc/opendubs-spot/spot-player.password`: systemd
   reads the root-only file and hands it to this service alone, in `$CREDENTIALS_DIRECTORY`. The
   password never goes in the environment or the arguments, and other users can't read it.
4. **`spot_credentials.py`** then has two sources. On macOS it reads the Keychain, as today. Under
   systemd it reads `$CREDENTIALS_DIRECTORY/spot-password`, with the user name from the service's
   configuration. Nothing else changes in the player.
5. **To revoke:** delete the user on Spot's admin page and the file on the Orin. To rotate the
   password: change it on Spot, then repeat step 2.

The service file and the `spot_credentials.py` change are written when the Orin is reachable. Its
address on the payload network is still unknown.

## The console (`software/spot/spot_console.py`)

A window on the operator's Mac, the avenue towards playing ENCORE shows on Spot. Built 2026-11-19 at
the maintainers' request:

- **It holds the session.** One Keychain read per session (the macOS dialog), the login kept in memory
  with the SDK refreshing the token hourly. Nothing is written to disk.
- **It is the person's E-Stop.** A big red STOP and the space bar (while the window is in front) stop
  Spot: sit, then cut. It starts STOPPED, and only a click on Release, in the window, releases it. A
  window frozen for over a second stops Spot; a dead console stops checking in and Spot stops within
  the 9 s timeout. When the tablet releases control it also gives up its E-Stop (seen 2026-11-19), so
  the console is then Spot's only stop: the person using it stays at the Mac, within reach of Spot.
- **Commands:** power on + stand (the lease held until sit), sit + power off. Loading an ENCORE plan
  (`spot_driver.plan()` as JSON) shows its windows, parts and problems; playing it is the next step.
- **Other local programs** (an agent session, a show player) send `status`, `stand`, `sit` or `stop` on a
  private socket (`~/.opendubs-spot/console.sock`, mode 0600). Releasing the E-Stop is never accepted
  there: a person releases it, in the window.

**Agreed with ENCORE, 2026-11-19:**
- **Stand-alone, under ENCORE.** The console is its own program. ENCORE's player interface (`describe`,
  `load`, `ready`, `start(at)`, `stop`, `status`) goes on its socket, and `load` takes the spot plan JSON
  that ENCORE's push dry run writes (`~/.encore/exports/<show-id>-<robot>.spot-plan.json`). ENCORE calls
  the socket only once its Run on robot is approved; until then the console is used on its own.
- **Invariant: the socket never releases the E-Stop.** The robot side keeps the last word, the E-Stop
  and the lease.
- **t = 0 of a Spot-only show is a Start press in the console,** by the person at Spot's E-Stop. With
  several robots, ENCORE sends `start(at)` on the shared clock.
- **Where the code lives:** the ENCORE repo as its own package is proposed, which changes ENCORE's
  "connects to no robot" rule and is the maintainers' call. Until then it stays in `software/spot/`.

This departs from "the driver is never the E-Stop client" above: the console is both, in one process,
so that a dead console stops Spot. A separate stop (the tablet, or `estop.py` on another machine)
can come back once Spot can keep one alongside the console.

## The player (`software/spot/spot_player.py`) and parity with opendubs2

Decided by the owner, 2026-11-19: the Spot player runs beside Spot (the Orin, or a Pi, on the payload
network), the operator surface is a web page in ENCORE's look, and ENCORE is the conductor of group
shows. This gives Spot the same three roles opendubs2 has:

| Role | opendubs2 | Spot |
|---|---|---|
| Holds the shows, plays them from memory | the runner on the Thor | `spot_player.py` on the Orin or a Pi (the Mac until then) |
| The operator: arms, GO, stop | the tablet and the deadman | the player's page on any browser, such as a tablet |
| Follows | the stage box | the stage box, for its host's show |

- **The session** is `spot_session.py`, shared with the Tk console. `fake_spot.py` has the same
  interface and no robot; `--fake` uses it.
- **The E-Stop stays with the person.** The page claims the operator role with a pairing code the
  player prints where it runs (one operator at a time). It beats every 200 ms over its WebSocket. A
  beat missing for 1 s, a closed or hidden page, or a dropped Wi-Fi link stops Spot. A dead player
  stops checking in, and Spot stops within the 9 s timeout. Any open page can STOP, with the button or
  the space bar. Only the operator page releases the E-Stop.
- **Shows** are stored on the player (`~/.opendubs-spot/plans/`), loaded from the page or by ENCORE's
  `POST /api/load`. As on opendubs2, a show waits **ARMED** until the operator arms it on the page.
  Then Start on the page, or ENCORE's `start(at)` with a wall-clock t0, plays it. Each window is sent
  `lead_s` before it starts. Any E-Stop ends playback. A window already sent keeps running on Spot,
  so stopping a show is always the E-Stop.
- **The player interface** is JSON over HTTP, replacing the console's socket for ENCORE:
  `describe`, `plans`, `load`, `ready`, `start`, `stop`, `status`. It never releases the E-Stop,
  never arms a show and never sends a command.
- **Real playback** (`spot_playback.py`, 2026-11-20) sends one synchronized command per window: the
  body as a stand command with a `base_offset_rt_footprint` trajectory: every key, plus samples along a
  monotone cubic through the keys, 50 points a window at most (Spot interpolates linearly between
  points); the arm as an
  `ArmJointTrajectory` with the plan's positions and velocities (Spot's cubic is the same curve); the
  jaw as a claw-gripper trajectory. Each window keeps the previous window's points and reference time,
  so a window sent early carries on through the end of the one it replaces. Spot's default arm and
  jaw speed limits stay in place. The arm is stowed when a show ends. Every trajectory is built in the
  local clock: the SDK's `robot_command` converts each `reference_time` to Spot's clock itself.
- **The first-run envelope.** ENCORE's limits for Spot are still placeholders (`built: false`), so a
  real Spot plays only plans inside the player's own envelope (or the walking envelope, below, with
  `--walking`): no BASE (feet planted), body offsets
  within 0.10 m and 20 to 22 deg, body speeds within 45 deg/s, arm joints within the profile's ranges
  and 90 deg/s. A show is armed at 25, 50, 75 or 100 % speed. The Werewolves plan is outside the
  envelope (body yaw 114 deg/s at 3.2 s); the first-moves show (`software/spot/shows/spot_moves.py`)
  is inside it and played on the fake at 50 % speed, both windows on time.
- **What the first session at Spot taught us (2026-11-20), and the fixes:**
  - Spot refused a body trajectory of 201 points ("too many points"). The player now sends at most 50
    body and jaw points a window. Spot's real limit is not documented.
  - Our code converted reference times to Spot's clock, and the SDK converted them again. The player
    now builds every command in the local clock.
  - ENCORE gives two arm points at the same time where one key ends and the next starts. The player
    keeps one of them. The ENCORE lane has the finding.
  - A hidden operator page stopped Spot, as designed, and the SDK's E-Stop check-in thread then ended
    for good. The next Release checked in once and went quiet. Spot's E-Stop keepalive policy then held
    the motors off, so every power-on failed with `KeepaliveMotorsOffError`. Release now starts a fresh
    check-in thread. `keepalive_status.py` lists Spot's keepalive policies, read-only.
  - Ready passed while Spot was powered on but sitting, so Start would have stood Spot up as part of
    the show. A show now needs Stand to have finished.
  - The page's status asked Spot whether it was powered, four times a second, on the loop that takes
    the heartbeat. A slow answer could stall the heartbeat and stop Spot. The status now comes from
    what the session did.
- **The second session at Spot (2026-11-23).** spot-moves played to the end at 100 %, both windows
  chained, and matched ENCORE's preview. A second version added one handed move: the shoulder (sh0)
  turns +60 deg, and Spot's head turned to its own left, as the preview shows. After the last window
  the body settled back to its normal stand. Two things went wrong
  first:
  - Spot's gripper refused window 1 because its first point was 1.0 s in the past. Chaining had kept
    the last past point for Spot to blend from. The player now sends only points at least 0.3 s
    ahead, and `command_problems` flags a first point in the past.
  - The player's E-Stop check-ins timed out after the stop, and Spot cut its motors. The Mac's Ethernet
    link had come up at 100 Mbit/s. A reseated cable brought it to 1000 Mbit/s, and the next run was
    clean. Check the link speed before a run.
- **The onboard Orin (2026-11-23).** Spot carries an AGX Orin (JetPack 5.1.2, Ubuntu 20.04) on its
  payload network at 192.168.50.5, listed on Spot as the "RL KIT" payload. Another team uses it, so
  the player has its own account, `opendubs`, with key login only and no sudo. Its files live in
  `~/opendubs-spot` (copied from a commit, which `COMMIT` names), with uv in `~/.local/bin`. The Orin
  is also on campus Wi-Fi, so the player listens on 192.168.50.5 only (`--listen 192.168.50.5`). The
  fake player there played spot-moves to the end, driven from the Mac over the wired network
  (`fake_run.py CODE PLAN 1 192.168.50.5:8099`). Never run the other team's RL stack while the player
  is connected: both talk to Spot, and the player refuses to connect while another E-Stop checks in.
  The Orin runs ufw (`orin/harden.sh`, 2026-11-23): from campus only SSH answers, with passwords still
  on by choice. Everything is allowed on the payload network, docker0 and the USB-C rescue port, and
  nothing is forwarded between networks. Before it, campus could reach the ROS 1 master, its node ports,
  port 4000 and rpcbind. Start the player from the Mac with `orin/start.sh` (it asks for Spot's login
  and tunnels the operator page to http://127.0.0.1:18099/), and update the Orin with `orin/sync.sh`.
- **The checks before Spot.** `check_plan.py` builds every window of every stored plan at every speed,
  as the player sends it, and says what Spot would refuse. The fake Spot runs the same envelope,
  builder and check, so a fake run fails where a real Spot would. Each SDK error in the player's log
  carries what it means and what to do (`ADVICE` in `spot_session.py`). When Spot's arm planner cannot
  follow a window, the log says so and suggests a lower speed.
- **Open:**
  - Spot's login on the Orin. There is no macOS Keychain there. The plan is Spot's payload
    credentials: the player registers as its own payload, an admin approves it once on Spot's admin
    page, and it logs in with its own ID and secret, kept in a file only its account reads;
  - trying, at Spot, the refusal to connect while another E-Stop (the tablet's, or a second console's)
    is still checking in (`_refuse_live_estop`).

Sound, lights and the buzzer for Spot and for group shows: `notes/design/spot-show-outputs.md`.

## Walking (BASE)

The player can walk Spot. It is built and played on the fake, and has not run on Spot yet. It walks
only when started with `--walking`, and that takes the owner's word at Spot. Without the flag the
first-run envelope holds as before, with the feet planted.

- **The command.** A window with BASE becomes one SE2 trajectory command in Spot's `vision` frame.
  ENCORE gives BASE in Spot's starting frame (x forward, y left). The session reads Spot's pose in
  `vision` (its gravity-aligned body) when it sends the first window, and lays the whole track out
  from there. The angle stays unwrapped, so a lap never reads as a turn back. Start a walking show with
  the body's yaw offset at zero, because the start is read from the body.
- **The window's MobilityParams** carry the gait (`locomotion_hint`), the swing height, a speed cap
  that Spot holds to itself (`vel_limit`), and the BODY offsets. The body rides in the walking command's
  params, so a walking window sends no stand command. The step style comes from the BASE command's
  optional `mobility` field: `{"gait": "CRAWL"|"AMBLE"|"TROT", "swing": "LOW"|"MEDIUM"|"HIGH",
  "speed_cap": m/s}`. The defaults are TROT, MEDIUM and the envelope's cap. Spot's SDK has no WALK
  hint.
- **The end time.** Spot requires an end time on a trajectory command. Each walking window ends 1 s after
  its last point. If no next window comes, Spot finishes the trajectory and stands.
- **Smooth through window edges.** Each window used to have its own curve, which slowed to zero at the
  window's edges: a body barely shows it, but a walk stutters at every window. `link_windows()` now gives
  each window its neighbours' points next to its edges, so the curve through a boundary is the same from
  both sides. On Run Rudolph Run the base speed at window starts went from as low as 0.11 m/s (with
  0.42 m/s between) to a steady 0.32 m/s. The body and the jaw get the same fix.
- **The walking envelope** (`WALKING` in `spot_playback.py`) is the first-run envelope plus BASE: at
  most 0.35 m/s and 30 deg/s, within 3.0 m of the start, gaits CRAWL, AMBLE and TROT only (never JOG or
  HOP), swing LOW, MEDIUM or HIGH. These caps are ours, set from the shows (Rudolph laps at
  0.28 m/s), and are placeholders until measured at Spot.
- **The guard.** The plan is checked against the envelope when it loads and at Ready. Each built window
  is checked again just before it is sent: the frame, the gait and swing, the speed cap Spot is
  sent, and every point read back into the starting frame against the radius, speed and turn caps. The
  fake runs the same checks and starts from an awkward pose (facing nearly backwards), so a frame or
  wrap mistake shows up there.
- **Not built yet:** re-anchoring the track on a fiducial on opendubs2's base, and the duet guard on
  the gap between the robots. Both wait on the measurements in `notes/design/spot-walking-session.md`.

## Where the code goes

`software/spot/` in this repository, owned by the Spot lane (decided by the maintainers, 2026-10-27;
workplan `notes/workplan-spot.md`). The driver runs on the operator's machine and is not deployed by
`deploy-all.sh`: nothing on opendubs2 changes.
