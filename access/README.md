# Fleet access

Who can log into which robot, and with what privileges.

`tools/access.py` creates, updates and removes the robots' personal SSH accounts from one
manifest. **The manifest names real people, so it is not in this repository.** The roster is the
private repository `UW-Allen-School-Robotics/opendubs-access`. Clone it into the git-ignored
`ops/access/` of the checkout you run the tool from:

```bash
gh repo clone UW-Allen-School-Robotics/opendubs-access ops/access
```

which gives

```
ops/access/manifest.ini     # the roster: one section per person
ops/access/<account>.pub    # each person's public key(s), named in their section
```

Only the maintainers can read that repository. Anyone who can write to it can grant root on every
managed robot. Its history is the audit trail: who was added or removed, by whom, and when.
[`manifest.example.ini`](manifest.example.ini) shows the format with role handles. To keep the
roster somewhere else, set `OPENDUBS_ACCESS_DIR` to that directory.

## Adding or removing someone

Follow the procedure in the `opendubs-access` README: change the roster there, commit, and push.
Then pull it into `ops/access/` and apply it from this repo:

```bash
git -C ops/access pull
python3 tools/access.py --check              # validate the manifest, no robot needed
python3 tools/access.py --diff  opendubs2    # what would change
python3 tools/access.py --apply opendubs2    # do it
```

**The section name is the Unix account**, so choose it once: renaming a section later deletes that
account and its home directory and creates a new one. The tool removes accounts that are no longer
listed, so revocation is a deletion rather than something to remember to do on each robot.

## Expiry

Every entry carries an `expires` date. Past it, the tool treats the person as absent
and removes the account on the next apply. Set it to the end of the term — access
that lapses on its own is the difference between a fleet with four accounts and one
with forty.

## Groups

| Group | What they get |
|---|---|
| `operator` | SSH in, read logs, restart the robot's own services. No firmware flashing, no network or user changes. |
| `admin` | Passwordless root. For firmware and bring-up work. |

## Safety properties

The tool only ever touches accounts in the `opendubs-managed` group, which it creates
and owns. It will not modify `root`, `opendubs`, any system account, or any account listed
under `[fleet] protect =` (put your own admin account there). It refuses to run against a robot
not listed under `[fleet]`, and it refuses to run at all without a manifest. A bad edit cannot
lock you out of your own machine.
