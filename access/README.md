# Fleet access

Who can log into which robot, and with what privileges.

## Adding someone

1. Ask them for their **public** key — the file ending `.pub`. If they don't have one:
   `ssh-keygen -t ed25519 -C "them@uw.edu"`, and they should set a passphrase.
   The private key never leaves their machine and must never appear here.
2. Save it as `access/<name>.pub`.
3. Add a section to `manifest.ini` — group, robots, and an expiry.
4. Open a PR. **Merging grants root or operator access to a live robot**, so it gets
   the same review any privileged change would.
5. Apply it:

```bash
python3 tools/access.py --check              # validate the manifest, no robot needed
python3 tools/access.py --diff  opendubs2    # what would change
python3 tools/access.py --apply opendubs2    # do it
```

## Removing someone

Delete their section (and their `.pub`) and re-apply. The tool removes accounts that
are no longer listed, so revocation is a deletion rather than something to remember
to do on each robot.

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
and owns. It will not modify `emmar`, `root`, `opendubs`, or any system account, and it
refuses to run against a robot not listed under `[fleet]`. A bad edit here cannot lock
you out of your own machine.

Keys are public and belong in git. Secrets do not — those live in the git-ignored
`ops/`.
