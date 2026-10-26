"""The stage box's self-update (bin/stage_update.py, release/publish-bundle.sh), on a desk:
    python3 -m pytest -q software/stage-pi/tests
A real bundle is published with a throwaway key through the real kiosk-signing settings, then installed
into a scratch root; and every way it must be refused is refused, leaving the box as it was."""
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile

import pytest

HERE = os.path.dirname(__file__)
STAGE = os.path.abspath(os.path.join(HERE, ".."))
REPO = os.path.abspath(os.path.join(STAGE, "..", ".."))
sys.path.insert(0, os.path.join(STAGE, "bin"))

import stage_update as su

pytestmark = pytest.mark.skipif(not shutil.which("openssl"), reason="needs openssl")


def _key(d):
    """A throwaway firmware-signing key, as provision-firmware-signing.sh lays it out: ota.key, ota.pub.der."""
    os.makedirs(d, exist_ok=True)
    subprocess.run(["openssl", "ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", f"{d}/ota.key"],
                   check=True, capture_output=True)
    subprocess.run(["openssl", "pkey", "-in", f"{d}/ota.key", "-pubout", "-outform", "DER", "-out", f"{d}/ota.pub.der"],
                   check=True, capture_output=True)
    os.makedirs(f"{d}/pins", exist_ok=True)                       # what setup.sh pins on the box
    subprocess.run(["openssl", "pkey", "-pubin", "-inform", "DER", "-in", f"{d}/ota.pub.der", "-out", f"{d}/pins/k.pem"],
                   check=True, capture_output=True)


@pytest.fixture
def published(tmp_path):
    ks, out = tmp_path / "ks", tmp_path / "out"
    _key(str(ks))
    # The real signing step, with "stage" among its targets (the Thor lane's change to BOARDS).
    sign = tmp_path / "sign-firmware.sh"
    src = open(os.path.join(REPO, "software/stack/firmware-signing/sign-firmware.sh")).read()
    sign.write_text(src.replace('BOARDS="stamplc neck"', 'BOARDS="stamplc neck stage"'))
    sign.chmod(0o755)
    env = dict(os.environ, OPENDUBS_FW_KS_DIR=str(ks), OPENDUBS_STAGE_BUNDLE_DIR=str(out),
               OPENDUBS_SIGN_FIRMWARE=str(sign))
    shim = tmp_path / "bin"
    shim.mkdir()
    (shim / "sha256sum").write_text("#!/bin/sh\nshasum -a 256 \"$@\"\n")      # the Mac's name for it
    (shim / "sha256sum").chmod(0o755)
    env["PATH"] = f"{shim}:{env['PATH']}"
    subprocess.run(["bash", os.path.join(STAGE, "release/publish-bundle.sh"), STAGE, "abc123"], env=env,
                   check=True, capture_output=True)
    out.joinpath("pins").symlink_to(ks / "pins")
    return out


def _get(out, swap=None):
    def get(url, limit, timeout=0):
        name = url.rsplit("/", 1)[1]
        return (swap or {}).get(name) or open(os.path.join(out, name), "rb").read()
    return get


def _run(mp, out, root, get=None, busy=lambda: False, keys=None):
    applied = []
    def apply(d, manifest):                    # install as the box does, into a scratch root, without the reboot
        subprocess.run(["bash", os.path.join(d, "bin/stage-install"), "--root", str(root)], check=True,
                       capture_output=True, env=dict(os.environ, PATH="/usr/bin:/bin"))
        applied.append(json.loads(manifest)["version"])
    mp.setattr(su, "missing_packages", lambda txt, have=None: [])        # the desk has no dpkg
    st = su.check_once(["http://robot"], busy, get=get or _get(out), apply=apply, keys=keys or str(out / "pins"),
                       root=str(root))
    return st, applied


def test_a_signed_bundle_installs_the_box_files(published, tmp_path, monkeypatch):
    st, applied = _run(monkeypatch, published, tmp_path / "root")
    assert applied == ["abc123"] and st["state"].startswith("installed abc123")
    lib = tmp_path / "root/usr/local/lib/stage"
    assert (lib / "stage-agent").stat().st_mode & 0o111 and (lib / "stage_update.py").exists()
    assert (tmp_path / "root/etc/systemd/system/stage-agent.service").exists()


def test_every_bad_bundle_is_refused_and_nothing_is_installed(published, tmp_path, monkeypatch):
    m = open(published / "manifest.json", "rb").read()
    other = tmp_path / "other"
    _key(str(other))
    evil = io.BytesIO()
    with tarfile.open(fileobj=evil, mode="w:gz") as t:
        data = b"x"
        info = tarfile.TarInfo("../escape")
        info.size = 1
        t.addfile(info, io.BytesIO(data))
    cases = {
        "tampered manifest": dict(get=_get(published, {"manifest.json": m.replace(b"abc123", b"abc124")})),
        "another key": dict(keys=str(other / "pins")),
        "archive not the signed one": dict(get=_get(published, {"stage-bundle.tar.gz": b"\x1f\x8b junk"})),
        "a show is running": dict(busy=lambda: True),
    }
    for why, kw in cases.items():
        st, applied = _run(monkeypatch, published, tmp_path / "root", **kw)
        assert not applied and (st["state"].startswith(("REFUSED", "waiting"))), (why, st)
    with pytest.raises(ValueError):
        su.safe_extract(evil.getvalue(), str(tmp_path / "x"))
    assert not (tmp_path / "root").exists()


def test_a_bundle_needing_a_new_package_waits_for_the_setup():
    have = {"cage", "chromium-browser", "curl"}.__contains__
    assert su.missing_packages("cage\nchromium|chromium-browser\n# a comment\ncurl\nnew-thing\n", have) == ["new-thing"]


def test_the_lower_root_is_found_beneath_the_overlay(tmp_path):
    (tmp_path / "usr").mkdir()
    mounts = f"overlay / overlay rw,lowerdir={tmp_path},upperdir=/media/root-rw/overlay,workdir=/x 0 0\n"
    assert su.lower_root(mounts) == str(tmp_path)
    assert su.lower_root("/dev/mmcblk0p2 / ext4 rw 0 0\n") == "/"
    assert su.lower_root("overlay / overlay rw,lowerdir=/nowhere,upperdir=/u 0 0\n") is None


def test_the_box_trusts_exactly_the_keys_every_board_trusts():
    fw = os.path.join(REPO, "firmware/common/ota_verify/keys")
    ours = os.path.join(STAGE, "keys")
    read = lambda d: {f: open(os.path.join(d, f), "rb").read() for f in os.listdir(d) if f.endswith(".der")}
    assert read(ours) == read(fw), "software/stage-pi/keys must be a copy of firmware/common/ota_verify/keys"
