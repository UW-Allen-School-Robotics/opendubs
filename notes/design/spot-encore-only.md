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
