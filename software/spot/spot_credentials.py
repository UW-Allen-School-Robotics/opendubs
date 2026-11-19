"""Spot's login, kept in the macOS Keychain.

The user name and password live in one Keychain item that trusts no application, so every read
pops a macOS dialog and goes ahead only when the person at the Mac clicks Allow. Click Allow, never
Always Allow: that would let any process on the Mac read the password silently. Nothing here prints
the password, writes it to disk or puts it in the environment.

Store or replace the login once, in the separate Terminal app (it asks for the password twice,
hidden):

    security add-generic-password -U -T "" -s opendubs-spot -a SPOT_USER -w

Use it in the driver:

    from spot_credentials import load
    user, password = load()
    robot.authenticate(user, password)
    del password

Check it is stored without reading the password (no dialog):

    python3 spot_credentials.py
"""
import re
import subprocess
import sys

SERVICE = "opendubs-spot"


class NotStored(RuntimeError):
    pass


def user(service=SERVICE):
    """The stored user name. Reads the item's attributes only, so no dialog appears."""
    r = subprocess.run(["security", "find-generic-password", "-s", service],
                       capture_output=True, text=True)
    m = re.search(r'"acct"<blob>="([^"]*)"', r.stdout)
    if r.returncode != 0 or not m:
        raise NotStored(f"no Keychain item {service!r}: store it with the command at the top of "
                        "spot_credentials.py, in the separate Terminal app")
    return m.group(1)


def load(service=SERVICE):
    """(user, password). macOS asks the person at the Mac to allow this read."""
    name = user(service)
    r = subprocess.run(["security", "find-generic-password", "-s", service, "-a", name, "-w"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise PermissionError("the Keychain read was refused or cancelled")
    return name, r.stdout.rstrip("\n")


if __name__ == "__main__":
    try:
        print(f"Keychain item {SERVICE!r} holds a login for user {user()!r}. Password not read.")
    except NotStored as e:
        sys.exit(str(e))
