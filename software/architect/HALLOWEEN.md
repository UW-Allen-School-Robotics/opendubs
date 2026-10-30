# Running the Halloween candy demo on opendubs2

How the trick-or-treat demo runs on the robot. These are the steps it was brought up and tested
with on 2026-10-29 and 30, from the `daphne/halloween` branch (PR #576). Someone says "OpenDubs …
trick or treat". The robot stops patrolling, takes a candy from the bucket on its stage with the
right arm, and hands it to a person's held-out hand, or puts it back in the bucket if there is no
hand it can reach. Then it goes back to patrolling. The background, the design and every number
below are in [README.md](README.md). This page is the how-to.

## What runs, and whose it is

```
Jabra ─▶ listener ──"trick or treat"──▶ coordinator ── takes the base from the nav, checks it is still
                                              │ grants the candy
                                              ▼
                           ARCHITECT runner (candy_gafro.py, planned on the twin)
                                              │ /perception/treat/* (ROS 2 services)
                                              ▼
                       Archit's treat node ──▶ tobi's arm service (the right arm, its gripper)
                                         └──▶ Archit's hand tracker (the palm held out)
```

| Piece | Whose | Where it comes from | Started by |
|---|---|---|---|
| Arm services (gafro) | tobi (Arms lane) | `main`, deployed to `/opt/opendubs/arm` | **a person**: `sudo arm up --supervise` (never at boot, on purpose) |
| Treat services + hand tracker | Archit (perception) | a checkout of **`daphne/treat-gripper`** (= `archit/treat-follow-gripper`) | `archit-architect-treat` unit |
| ARCHITECT runner | this branch | `software/architect` | `archit-architect-runner` unit |
| Listener (Jabra, wake word, voice) | this branch | `software/architect` | `archit-architect-listener` unit |
| Coordinator (demo day) | this branch | `software/architect` | `archit-architect-day` unit |
| Camera feed for the dashboard tab | this branch | `software/architect` | `archit-architect-camera` unit |
| Nav (patrol) | Sid | `sid/nav` in opendubs plus his repos in `/home/sid` | the dashboard's nav Start, or `curl -X POST 127.0.0.1:8080/api/nav/start` |

The units run as **the user who installs them**. They need that user's checkouts, so set them up in
your own account. Nothing here is part of the robot's deployed stack: no deploy or provisioner
touches these units.

## Once per account

You need, on opendubs2:
- `sudo` without a password for `docker`, `systemctl` and `arm` (`sudo -n docker ps` must work).
- Membership of the `audio` group (`sudo usermod -aG audio $USER`, then log in again), for the Jabra.

```sh
# 1. This branch, and Archit's services with the gripper change
git clone https://github.com/UW-Allen-School-Robotics/opendubs.git ~/opendubs-architect
cd ~/opendubs-architect && git switch daphne/halloween
git clone https://github.com/UW-Allen-School-Robotics/opendubs.git ~/treat-gripper
cd ~/treat-gripper && git switch daphne/treat-gripper
# 2. ssa (ARCHITECT's agent library) on its opendubs branch, in ~/ssa: ask for access if you do not have it

# 3. The image (FROM archit-perception:dev, already on the robot), and the listener's two models
cd ~/opendubs-architect
software/architect/tools/dev.sh build
software/architect/tools/dev.sh kws        # the keyword spotter, into ~/architect-data/models
software/architect/tools/dev.sh butler     # the butler voice (bm_george)

# 4. The hand tracker's MediaPipe models, into ~/perception-data/models/mediapipe
#    (hand_landmarker.task, pose_landmarker_full.task: Google's, Apache-2.0; a copy is in
#    /home/shamika/halloween-mediapipe/models on opendubs2)
TREAT_TOOLS=~/treat-gripper/software/perception/tools
DATA=~/perception-data $TREAT_TOOLS/hand_stack.sh models <dir with the two .task files>

# 5. The right arm's workspace and named poses for ARCHITECT, in ~/architect-data/architect-arms.yaml:
#    copy the one in /home/daphne/architect-data/ (the right box x [0.48, 0.88]; named behind_bucket)
```

`dev.sh treat` builds the two ROS interface packages it needs (`robot_api_interfaces`,
`opendubs_arm_interfaces`) into `~/robot_api_ws` the first time it runs.

## Install the boot units

```sh
cd ~/opendubs-architect
MODE=gafro NAV=hound TREAT_REPO=~/treat-gripper REPO=$PWD software/architect/tools/dev.sh install
software/architect/tools/dev.sh status        # all five: active
```

- **`MODE=gafro`:** the candy run is ARCHITECT's program on the arm service, not the old candy show.
- **`NAV=hound`:** the coordinator takes the base from Sid's nav for the candy and gives it back afterwards. Leave it out for the stub, which moves nothing.

At boot, or after this:
- The runner **arms `candy_gafro.py` by itself**. You hear its summary, then "Approved. Say trick or treat."
- The coordinator starts **PAUSED**.

Nothing moves until someone brings the arms up and resumes the coordinator. Installing again
restarts all five. `dev.sh uninstall` removes them. `MODE=show SHOW=architect-bb75cd239645
dev.sh install` goes back to the old candy show on the old arm stack.

Only one treat node can run at a time. If Archit is running his own, ours refuses to start
("treat_services.py is already running") and retries every 10 s. Ask him to stop his, or stop ours:
`sudo systemctl stop archit-architect-treat`.

## Each time: bring it up

With **someone at the e-stop**, the stage and the bucket in place, and the area clear:

```sh
sudo arm up --supervise           # tobi's arm services, both arms; holds them where they are
# CH5 in AUTO, hold the deadman (CH6):
sudo arm activate right           # the right arm to ready (refused when it is far from stow or ready: `sudo arm park right` first)
curl -X POST 127.0.0.1:8080/api/nav/start     # Sid's nav, if patrolling; its mission is set on his page (port 8081)
curl -X POST 127.0.0.1:8096/api/resume        # the coordinator: patrol, then candy on "trick or treat"
```

Check it before the first visitor:

```sh
software/architect/tools/dev.sh status
curl -s 127.0.0.1:8098/api/state      # the runner: "ARMED"
curl -s 127.0.0.1:8096/api/state      # the coordinator: "IDLE_NAV"; nav_state owner "nav" when patrolling
sudo arm status right                 # supervised: yes
```

## Each visitor

1. **The deadman is held all the time.** It's what lets the base patrol and the arm move, and letting go stops both.
2. The visitor says **"OpenDubs … trick or treat"**. Within a metre or so of the Jabra is best.
3. The coordinator takes the base and waits until it's still. The robot greets them, then:
   - the head tips down;
   - the arm goes out behind and over the bucket, opens wide, goes down to just above the candy, and closes on one;
   - it lifts out and goes to the side.
4. **The hand-off:** the robot says "Now hold out your hand for your treat!" on the Jabra. The visitor stands **about 1–1.2 m from the robot's front** and holds a hand out, **palm up, at about 1 m off the floor**, and keeps it still. The robot reaches toward the hand (stopping 30 cm short of the palm), holds 3 s, opens, and goes back to `ready`.
   - A hand that's lower, nearer or further than that can't be reached. The candy then goes back into the bucket, and every run ends with the gripper open.
5. After the run the base stays held for 20 s, then patrol carries on.

Stop everything: release the deadman, or press the e-stop. `curl -X POST 127.0.0.1:8096/api/pause`
pauses the day. A paused coordinator still keeps the nav from driving, and only `/api/resume`
restarts it.

## What can go wrong (all seen while testing)

| Symptom | Why | Do |
|---|---|---|
| A run stops at once: "the deadman is not held" | The robot didn't receive CH6, even though it was being held | Check the remote's battery and link. `curl -s 127.0.0.1:8080/api/choreo/robot` shows `deadman` |
| Nothing moves: "CH5 is STANDBY / MANUAL" | The arm service only moves in AUTO | CH5 to AUTO |
| The base lights strobe amber | The drive contactor is open, waiting for CH5 at OFF | CH5 to OFF (after an e-stop, also hold RE-ARM on the dashboard). The arms lose power: `sudo arm down`, then `sudo arm up --supervise` |
| "no hand held out and still" | The hand was out of view, or not held out | Stand about 1 m away, hand at about 1 m off the floor, out in front of your body |
| The arm reaches somewhere else, or "out of reach" | The palm was too low, too near or too far | As above. A plan more than 50 mm from its goal is not reached for |
| "STOPPED: the permit was lost" on every move | Archit's node latched a stop after the deadman dropped | `ros2 service call /perception/treat/rearm robot_api_interfaces/srv/RobotCommand '{req: "{}"}'` (source `/opt/ros/jazzy` and `~/robot_api_ws`) |
| "FORCE LIMIT: joint 1 reached 12 N m" | The arm was near straight, holding a candy | The 30 cm standoff avoids it. If it happens, the arm holds where it stopped |
| "the head did not settle" | The neck stopped short of its target | Settles within 2.5°. If it's worse, check the neck |
| No wake word heard, `capture_lost` in the listener log | The Jabra fell off USB (a hub dropped) | Replug the hub. The listener reopens it by itself. Set the volume back: `amixer -c <card> sset PCM 100%` |
| No `/scan` after a replug | The front LIDAR came up on a different USB port | Plug it back into its port (the driver names it by port) |
| The tablet shows no face | The face page went hidden after boot | `curl -X POST 127.0.0.1:8080/api/tablet/command -H 'Content-Type: application/json' -d '{"cmd":"restart"}'` |
| The treat unit restarts every 10 s | Another treat node is running (Archit's), or the arm service is down | `pgrep -af treat_services.py`; `sudo arm status right` |

Logs:
- `~/architect-data/listener.log`
- `~/architect-data/demo_day.log`
- `~/architect-data/treat.log`
- `sudo docker logs archit-architect-server` (the runner)
- `journalctl -u archit-architect-<unit>`

## Not covered yet

- **The dances and random shows** go through the show runner, which refuses any gripper cue on the arm service: the dashboard doesn't report the gripper there yet (tobi). The coordinator's random picks are off in this mode.
- **Noticing visitors:** that needs Archit's perception "locate" service. Without it the robot patrols until someone says "trick or treat".
- **Sid's nav** isn't paused, only kept off the base: its planner keeps running while the arm works. The proper hold topic is asked for in sidtalia/hound_nav#1.
