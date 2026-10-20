# tools/network — the robot router, read and switched from the Thor

The Network lane's tools (CLAUDE.md "Lanes"). The designs are in
[`notes/design/network-router-api.md`](../../notes/design/network-router-api.md) and
[`notes/design/network-uplinks.md`](../../notes/design/network-uplinks.md); the plan is
[`notes/workplan-network.md`](../../notes/workplan-network.md). Python 3.10+, standard library only.

| File | What it is |
|---|---|
| `routeros.py` | A small client for RouterOS's REST API, and parsers for its value formats |
| `netmon.py` + `opendubs-netmon.service` | The poller. Read-only, every 5 s. Writes `/run/opendubs-network/network.json` (the dashboard's uplink status) and `/var/lib/opendubs-network/events.jsonl` (uplink drops, which the router forgets at reboot) |
| `netdiag.py` | One command naming the first failing network layer. Read-only |
| `netjoin.py` | Adds, orders and switches the uplink's saved networks, without the admin page. Writes to the router, so it needs the lane rule below |
| `sample-network.json` | What `network.json` looks like, from 2026-10-19's readings, for building the dashboard against |
| `network.env.example` | The template for `/etc/opendubs/network.env`. The real file never enters the repository |
| `tests/` | `python3 -m unittest discover -s tools/network/tests` |

**Lane rule:** any change on the router needs the user's word in the same session and a person at
the robot. `netmon` and `netdiag` only read.

## Using them

```sh
sudo /opt/opendubs/network/netdiag.py                 # what's wrong with the network, if anything
sudo /opt/opendubs/network/netjoin.py list            # the saved uplink networks, in order
sudo /opt/opendubs/network/netjoin.py add "Venue WiFi" --top --now
cat /run/opendubs-network/network.json                # what the dashboard sees
```

- `netjoin` asks for the router's **admin** password each time. You type it from the password
  manager, and it's never stored.
- The router's uplink radio is **5 GHz only** (the robot's own Wi-Fi keeps 2.4 GHz for its ESP32
  devices). Set a phone hotspot to 5 GHz before adding it.
- `netjoin scan` takes the uplink offline for about 5 s while it looks.

## One-time setup (at the robot, with the user's word)

1. **The read-only account** for `netmon` and `netdiag`, in a router terminal (SSH as admin):

   ```
   /certificate add name=netmon-https common-name=opendubs2-router days-valid=3650
   /certificate sign netmon-https
   /ip service set www-ssl certificate=netmon-https disabled=no
   /user group add name=netread policy=read,rest-api,!local,!telnet,!ssh,!ftp,!reboot,!write,!policy,!test,!winbox,!password,!web,!sniff,!sensitive,!api,!romon
   /user add name=netmon group=netread address=10.42.2.10/32 disabled=yes
   ```

   - Set `netmon`'s password in the admin page's Users dialog, typed by the user, then enable the
     account.
   - The plain-HTTP admin page stays as it is.
   - The built-in `read` group is not used: it includes `sensitive`, which would show the Wi-Fi
     passphrases.
   - If REST refuses the right password with 403, the group also needs `web`. MikroTik's policy
     page couldn't be read when this was written, so add it only if refused.
   - Without the right to run `monitor`, the dashboard shows the uplink's network name as unknown
     once the saved list is in use. Everything else still works.
2. **On the Thor**, create `/etc/opendubs/network.env` (root:root, mode 0600) from
   `network.env.example`, with the password and the certificate's fingerprint.
3. **Saved networks**: `netjoin init` (the current network becomes the first saved one), then
   `netjoin wired-first`.

## Installing

The Thor lane's deploy scripts copy this directory to `/opt/opendubs/network/` and install
`opendubs-netmon.service`. The unit starts only when `/etc/opendubs/network.env` exists, so
deploying it before step 2 is harmless.

## The dashboard

The Tablet lane reads `network.json` into the status block as `status.net.uplink` and
`status.net.robot_wifi`. The contract is in `notes/design/network-router-api.md` §4.
