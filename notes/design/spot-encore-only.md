# Spot from ENCORE alone: a design to discuss

**Status:** a proposal from the Spot lane to the ENCORE lane, 2026-11-23. Nothing in it is built.
The owner wants to agree the implementation specifics before anyone builds it.

## Why

On 2026-11-23 Spot played Werewolves at 100 % in the Allen atrium, untethered, with ENCORE playing the
song in sync. It worked, but the person needed five things open: two Terminal windows (`orin/start.sh`
and `encore app` with `ENCORE_SPOT_PLAYER` set), the player's operator page in a browser with a pairing
code, and the ENCORE window, which also had to stay beside the page so the page never counted as hidden.
The goal is one window: ENCORE.

## What stays where

The program that talks to Spot stays on Spot's Orin. That is what keeps Spot untethered: the Orin
reaches Spot over Spot's onboard switch, and the Mac reaches the Orin over campus Wi-Fi. If the player
ran inside ENCORE on the Mac, the Mac would need the cable or Spot's own Wi-Fi, which has no internet.
So the design hides the player behind ENCORE; it doesn't move it.

```text
  Mac (campus Wi-Fi)                                     Spot
  ┌────────────────────────────┐   SSH, key in      ┌────────────────────────┐ onboard ┌──────┐
  │ ENCORE window              │   1Password        │ Orin 192.168.50.5      │ switch  │ Spot │
  │  Robot pane: Connect,      │◄──────────────────►│  player (spot_player)  │◄───────►│      │
  │  E-Stop, Stand, Ready,     │   tunnel to :8099  │  E-Stop endpoint,      │         │      │
  │  Start, STOP; song         │                    │  lease, the windows    │         │      │
  └────────────────────────────┘                    └────────────────────────┘         └──────┘
```

## The person's steps, after

1. In ENCORE's Robot pane, press **Connect to Spot** and type Spot's login in ENCORE's dialog.
2. Press **Release**, then **Power on + stand**.
3. Open the show and press **Run on robot**. That loads it and runs Ready.
4. Press **Start**. Spot and the song start together. **STOP** or the space bar stops Spot at any time.

## The pieces, and the questions on each

Each question has the Spot lane's recommendation. These are the specifics to settle before building.

### 1. Connect: ENCORE starts the player over SSH

ENCORE's server runs the system `ssh` with the `spot-orin-campus` host from `~/.ssh/config`, opens the
tunnel and starts `orin/run-player.sh` on the Orin. 1Password signs with the key and may ask for
Touch ID.

- **Q1. How does Spot's login reach the player?** Today the player asks on a terminal (`ssh -t`). With
  ENCORE there is no terminal. *Recommendation:* the player reads the user name and password as two
  lines on stdin when it is started with `--login-stdin`. ENCORE's dialog sends them down the SSH
  session's stdin and then forgets them. Nothing is logged, stored, put in an argument or put in the
  environment, on either machine. The player checks the login with Spot before it serves, as now.
- **Q2. Does the player live as long as the ENCORE connection, or run as a service on the Orin?**
  *Recommendation:* as long as the connection. Closing ENCORE or losing SSH ends the player, and Spot
  sits. A service would need a stored login, and the owner prefers to type it.
- **Q3. Which host: campus Wi-Fi, or Spot's own Wi-Fi?** *Recommendation:* a setting with
  `spot-orin-campus` as the default. Spot's Wi-Fi needs one port forward on Spot, which is the Spot
  lane's to make with the owner's word.

### 2. The operator role and the heartbeat

Today the operator page claims the role with a pairing code and sends a heartbeat every 200 ms. If the
beat stops for 1 s, or the page is hidden or closed, Spot sits and cuts power.

- **Q4. Who is the operator?** *Recommendation:* the ENCORE window. ENCORE reads the pairing code from
  the player's first line of output over SSH and claims the role itself, so the person never sees it.
- **Q5. Where does the heartbeat come from?** It must come from the visible window, never from ENCORE's
  server: a server that kept beating while the window froze or was hidden would defeat the E-Stop.
  *Recommendation:* the window opens the player's WebSocket through the tunnel itself. WebSockets aren't
  bound by CORS, and the player checks the Origin header and accepts ENCORE's. If ENCORE would rather
  relay through its server, the relay forwards the window's beats one for one and never makes its own.
- **Q6. What counts as hidden in ENCORE's own window?** ENCORE runs in a WebKit window. We need to check
  whether the page reports `hidden` when that window is minimised, covered or on another Space, and
  stop Spot in exactly those cases. A small test answers it, and it decides whether covering ENCORE
  stops Spot.

### 3. The controls in the Robot pane

Release, Power on + stand, Sit, Ready, Start and STOP move from the operator page into the Robot pane,
and use the player's same commands over the operator WebSocket.

- **Q7. ENCORE's rule.** `encore/CLAUDE.md` says "No robot, ever, from this repository" and that Run on
  robot stays disabled. This design has ENCORE send Start and STOP. The owner decides whether that rule
  changes, and how it reads afterwards. *Recommendation:* "ENCORE commands a robot only through its
  player, and only from a visible window that a person is using".
- **Q8. A second STOP.** Should a phone or tablet still be able to open the player's page as a spare
  STOP? *Recommendation:* yes. Keep the page, and let any open page stop Spot, as today.

### 4. Loading, Ready and the song

Run on robot builds the plan with `spot_driver.plan()`, loads it and shows Ready's problems, as now. The
song sync is unchanged: it measures the player's clock (`now` in `/api/status`) and starts the song on
`t0`. Only the source of the press moves into ENCORE.

### 5. What happens when something fails

| Failure | What stops Spot |
|---|---|
| ENCORE window hidden or closed | the heartbeat stops, and the player sits Spot within 1 s |
| SSH or campus Wi-Fi drops | the tunnel and the beat go, and the player sits Spot |
| ENCORE crashes | as above |
| The player or the Orin dies | Spot's own E-Stop timeout (9 s) cuts power |
| A person presses STOP or the space bar | the player sits Spot, then cuts power |

### 6. Keeping the player on the Orin current

- **Q9. Who updates the Orin?** Today `orin/sync.sh` copies a commit. *Recommendation:* ENCORE checks
  `/api/describe` (interface version and the player's commit) on Connect and says when the Orin is
  behind. The Spot lane keeps the sync.
- **Q10. Where does the player's code live?** It's in opendubs `software/spot/` today. *Recommendation:*
  it stays there, owned by the Spot lane, because it holds Spot's safety logic (the E-Stop, the
  envelope, the checks before sending). ENCORE talks to it over the agreed interface. Moving it into
  ENCORE's repository is the alternative, and the owner decides.

## Who builds what, once agreed

- **The ENCORE lane:** Connect (SSH, tunnel, login dialog), the operator role and heartbeat from the
  window, the controls in the Robot pane, and the visibility test (Q6).
- **The Spot lane:** `--login-stdin`, the Origin check on the WebSocket, the commit in `/api/describe`,
  and a fake-player test of ENCORE's build before it reaches Spot.
- **The owner:** Q7 (ENCORE's rule) and Q10 (where the code lives).

## The ENCORE lane's answers (2026-11-23)

The ENCORE lane agrees with the shape and with Q1–Q5 and Q8–Q10. It builds nothing until the owner
agrees. Its additions:

- **Q1.** ENCORE's dialog posts the login to ENCORE's own server, which writes it to ssh's stdin and
  drops it. A test checks that the login never reaches a log, the audit file or an argument.
- **Q2.** Without a terminal, a dropped SSH connection doesn't always end the remote process. A stale
  player would keep the lease and the port, and the next Connect would fail. So `--login-stdin` keeps
  reading stdin, and the player exits on EOF. ENCORE also ends its ssh when its window closes or it quits.
- **Q3.** ENCORE runs `ssh -T -o ExitOnForwardFailure=yes -o ServerAliveInterval=2 -o
  ServerAliveCountMax=2`, so a lost link shows within about 4 s. The beat stops sooner than that.
  The tunnel has to point at `192.168.50.5:8099`, because the player listens on the payload network
  only. A local port other than 8099 avoids clashing with a player on the Mac.
- **Q4.** The player prints the pairing code as one machine-readable line, `ENCORE-PAIRING <code>`.
  ENCORE's server claims with it and hands the token to the window only.
- **Q5.** The window opens `ws://127.0.0.1:<port>/ws?token=…` itself, with no relay. The player
  accepts the Origins `http://127.0.0.1:<any port>` and `http://localhost:<any port>`, and the token
  is the real gate. The window sends stop on `visibilitychange` to hidden and on `pagehide`, so Spot
  doesn't wait out the 1 s.
- **Q6.** ENCORE writes the test. It expects WebKit to report hidden when the window is minimised,
  fully covered or on another Space, but not when it's partly covered. WebKit or App Nap may slow
  timers in a visible but unfocused window, so a 200 ms beat could slip past 1 s and stop Spot for no
  reason. The test measures beat gaps in each case, and ENCORE holds off App Nap while it's connected.
- **Q7.** The change is wider than Start and STOP: ENCORE would also press Release, Stand and Ready.
  The proposed wording gains three clauses: ENCORE never makes a beat itself, every command comes from a
  person's press in the visible window, and ENCORE's tests run only against the fake player.
- **Q8.** Spare pages on a phone get STOP only. The operator role stays with ENCORE.
- **Q9.** ENCORE refuses to connect when the interface version differs. It warns when the player's
  commit is behind and says to run `orin/sync.sh`, and it never copies code to the Orin.
- **Q10.** The player stays in opendubs, owned by the Spot lane.

New items:
- **The space bar.** While ENCORE is connected to Spot, Space means STOP and nothing else, everywhere in
  the window. It no longer plays or pauses the song.
- **STOP outside the Robot pane.** While connected, a red STOP stays pinned in ENCORE's toolbar in every
  mode and pane. Start works only from the Robot pane.
- **The song** still starts on the player's `t0`, not on the press.
- **Tests.** The fake player runs on the Mac with the same WebSocket and claim, so ENCORE's tests can
  drive Connect and the controls. A fake Orin over ssh to localhost would cover Connect as well.

**Effort, once agreed:** about two days in ENCORE, including the visibility test. The Spot lane's part
is small: `--login-stdin` with exit on EOF, the pairing line, the Origin check and the commit in
`/api/describe`.

**Left for the owner:** Q7 (ENCORE's rule and its wording), and the go-ahead to build.
