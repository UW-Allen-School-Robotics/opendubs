# Playing sound on the robot

The robot plays sound from the face tablet's speaker. There are two ways to send it something: an
iPhone can stream to it over **AirPlay**, and any phone or computer can send it a sound
file or a link from the dashboard's **Play to robot** page.

A **show always comes first.** While a show runs, AirPlay and Play to robot go quiet and pick up
again when it ends. The robot's background music pauses while either is playing.

## AirPlay from an iPhone

The robot runs an AirPlay-compatible receiver
([shairport-sync](https://github.com/mikebrady/shairport-sync)). AirPlay is Apple's trademark; the
receiver is independent and not certified by Apple.

### Turn it on

On the dashboard, open the menu → **Head & face** → **SOUND**, and switch **AirPlay** on. It stays on
across reboots. Switched off, the robot disappears from AirPlay lists.

If the robot has no receiver installed, the switch is greyed out and says so. The receiver is set up
from the robot's software stack (`software/stack/airplay/README.md` in the repository).

### Connect

1. **Join the robot's own Wi-Fi network** — the robot router's local network, which is bridged with
   the robot's wired network. A phone on any other network, such as a building's Wi-Fi, will not see
   the robot.
2. On the iPhone, open **Control Center → AirPlay**, or tap the AirPlay button in the app, and choose
   the robot by its name.

The face shows what is playing: **♪ title — artist**, or **♪ AirPlay** when the app sends no track
name.

### Volume

The phone's volume slider works as usual. Two limits apply on top of it: the robot's own ceiling,
**Never louder than** on the Head & face page, and the face tablet's volume.

### If nothing plays

- Check the face tablet's volume.
- Tap the face once: that resumes the face page's audio.
- Make sure no other browser has the robot's face page open: only one page plays the robot's
  sound.

### Not yet supported

**A Mac** can see the robot and connect, but plays nothing: its AirPlay timing does not lock with the
robot's receiver.

**An iPad** should work the same way, but hasn't been tested yet.

## Play to robot — a file or a link

From any phone or computer on the robot's network, open **`http://<robot>:8080/play/`**. There you
can:

- **Choose a sound** from the device and send it, or
- paste **a link to an audio file** and press **FETCH AND PLAY**.

The page has its own **STOP** button and volume slider. Like AirPlay, it steps aside for a show.

## See also

- [Operating the robot](operating.md)
- [Troubleshooting](reference/troubleshooting.md)
