"""Spot's login: kept in the macOS Keychain on a Mac, typed at the start on the Orin (or any Linux).

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

On Linux there is no Keychain. ask() asks for the login on the terminal (the password hidden) and keeps
it in this process's memory only: nothing on disk, in the environment or in the arguments. The player
calls it before it starts serving, so the person starting it over SSH types the login once per run.
"""
import getpass
import re
import shutil
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


_typed = None                       # (user, password) typed at the start, on Linux; memory only


def keychain():
    return sys.platform == "darwin" and shutil.which("security") is not None


def ask(check=None, tries=3):
    """Ask for Spot's login on the terminal and keep it in memory for load(). `check(user, password)`
    raises on a wrong login, which is asked again. Returns the user name."""
    global _typed
    if not sys.stdin.isatty():
        raise NotStored("no terminal to ask for Spot's login on: start this over `ssh -t`, in the foreground")
    for n in range(tries):
        name = input("Spot user name: ").strip()
        password = getpass.getpass(f"Spot password for {name} (hidden): ")
        try:
            if check:
                check(name, password)
        except Exception as e:
            print(f"login refused ({type(e).__name__}); try again" if n + 1 < tries else "login refused")
            continue
        _typed = (name, password)
        return name
    raise PermissionError("Spot refused the login")


def from_stdin(check=None):
    """Read Spot's login as two lines on stdin (user name, then password), as ENCORE's Connect sends it
    over SSH, and keep it in memory for load(). `check(user, password)` raises on a wrong login. Nothing
    is echoed or logged. Returns the user name."""
    global _typed
    name = sys.stdin.readline().rstrip("\r\n")
    password = sys.stdin.readline().rstrip("\r\n")
    if not name or not password:
        raise NotStored("no login on stdin: expected the user name and the password, one per line")
    if check:
        check(name, password)
    _typed = (name, password)
    return name


def load(service=SERVICE):
    """(user, password). On a Mac, macOS asks the person at the Mac to allow this read. On Linux, the
    login typed at the start (ask()), or asked for now when there is a terminal."""
    if _typed is not None:              # typed at the start, or sent on stdin by ENCORE
        return _typed
    if not keychain():
        ask()
        return _typed
    name = user(service)
    r = subprocess.run(["security", "find-generic-password", "-s", service, "-a", name, "-w"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise PermissionError("the Keychain read was refused or cancelled")
    return name, r.stdout.rstrip("\n")


if __name__ == "__main__":
    if not keychain():
        sys.exit("no Keychain here: the login is typed when the player or a script starts")
    try:
        print(f"Keychain item {SERVICE!r} holds a login for user {user()!r}. Password not read.")
    except NotStored as e:
        sys.exit(str(e))
