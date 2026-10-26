#!/bin/bash
# Turn a freshly imaged Raspberry Pi 4 or 5 (64-bit Raspberry Pi OS Lite) into the OpenDubs stage box.
# Run ON THE PI, from a copy of this folder, as the user Raspberry Pi Imager created:
#
#   sudo ./setup.sh                                   # install, configure, root read-only
#   sudo ./setup.sh --media /dev/sda --erase-media    # the same, and ERASE that USB stick to hold
#                                                     # the box's copies of the show videos
#   sudo ./setup.sh --no-overlay                      # leave the root writable (trying things out)
#   sudo ./setup.sh --name opendubs2-stage            # rename it (normally the image's name is kept)
#
# After this, the box UPDATES ITSELF: each deploy publishes a signed copy of this folder on the robot, and
# the box installs it between shows (stage_update.py). Run this by hand again only when packages.txt
# changes (the self-update refuses a bundle that needs a package the box lacks, and says so).
#
# Idempotent: run it again after an update to this folder (with the overlay off: see README).
# The Wi-Fi, the user, the SSH key AND THE NAME come from Raspberry Pi Imager's settings, never from
# here: one box per robot, named opendubs<N>-stage after its robot (opendubs2-stage). The name the
# image has is kept; only an image still called "raspberrypi" is refused.
set -euo pipefail
cd "$(dirname "$0")"
overlay=1; media=""; erase=0; name=""
while [ $# -gt 0 ]; do
    case $1 in
        --no-overlay) overlay=0 ;;
        --media) media=${2:-}; shift ;;
        --erase-media) erase=1 ;;
        --name) name=${2:-}; shift ;;
        *) echo "setup: unknown option $1" >&2; exit 1 ;;
    esac
    shift
done

die() { echo "setup: $*" >&2; exit 1; }
[ "$(id -u)" = 0 ] || die "run it with sudo"
[ "$(dpkg --print-architecture)" = arm64 ] || die "this needs 64-bit Raspberry Pi OS (arm64)"
grep -q "Raspberry Pi [45]" /proc/device-tree/model 2>/dev/null || die "this is for a Raspberry Pi 4 or 5"
admin=${SUDO_USER:-}
[ -n "$admin" ] && [ -s "/home/$admin/.ssh/authorized_keys" ] || die "no SSH key for '${admin:-?}': set one in Raspberry Pi Imager first (password logins are about to be turned off)"
if grep -q "^overlayroot\|boot=overlay" /proc/cmdline; then die "the root is read-only now: turn the overlay off first (README, Updating)"; fi
if [ -n "$media" ]; then
    [ -b "$media" ] || die "--media $media is not a disk"
    root_disk=$(lsblk -no PKNAME "$(findmnt -no SOURCE /)")
    [ "$(basename "$media")" != "$root_disk" ] || die "--media $media is the disk this system runs from"
    [ "$(lsblk -dno TRAN "$media")" = usb ] || die "--media $media is not a USB disk"
    [ "$erase" = 1 ] || die "--media erases $media: add --erase-media to confirm"
fi
name=${name:-$(hostname)}
[ "$name" != raspberrypi ] || die "this box is still called raspberrypi: name it opendubs<N>-stage, after its robot (in Raspberry Pi Imager, or with --name)"
[[ "$name" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?$ ]] || die "--name $name: lower-case letters, digits and hyphens only"

echo "== the name: $name"
hostnamectl set-hostname "$name"
sed -i "s/^127\.0\.1\.1.*/127.0.1.1\t$name/" /etc/hosts
grep -q "^127.0.1.1" /etc/hosts || echo -e "127.0.1.1\t$name" >> /etc/hosts

echo "== packages"
apt-get update -qq
pkgs=()                                           # packages.txt: one per line; a|b = the first the archive has
while read -r line; do
    line=${line%%#*}; [ -n "${line// /}" ] || continue
    for alt in ${line//|/ }; do apt-cache show "$alt" >/dev/null 2>&1 && { pkgs+=("$alt"); break; }; done
done < packages.txt
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${pkgs[@]}"
# No remote desktop of any kind, by decision.
for p in realvnc-vnc-server wayvnc xrdp tigervnc-standalone-server x11vnc; do
    dpkg -s "$p" >/dev/null 2>&1 && DEBIAN_FRONTEND=noninteractive apt-get purge -y -qq "$p"
done

echo "== the stage user (no password, no SSH)"
id stage >/dev/null 2>&1 || useradd -m -s /bin/bash stage
usermod -aG video,render,audio,input,bluetooth stage
passwd -l stage >/dev/null
# Its PipeWire runs from boot, not only while the browser's session is open: the sound keeps its output,
# its volumes and its speaker connection across a browser restart.
loginctl enable-linger stage
systemctl --global enable pipewire.socket pipewire-pulse.socket wireplumber.service 2>/dev/null || true
rm -f /home/stage/.asoundrc                       # the old ALSA default (before PipeWire) would bypass it

echo "== the box's own programs (the same list a self-update installs)"
bash bin/stage-install
[ -e /boot/firmware/stage.conf ] || install -m 0644 stage.conf.example /boot/firmware/stage.conf

echo "== the signing keys the self-update trusts"
# The project's firmware-signing keys, public halves (keys/, the same files as firmware/common/ota_verify/keys,
# which every board trusts). Pinned here, by hand: an update never changes them.
install -d -m 0755 /usr/local/lib/stage/keys
rm -f /usr/local/lib/stage/keys/*.pem
for k in keys/*.der; do
    openssl pkey -pubin -inform DER -in "$k" -out "/usr/local/lib/stage/keys/$(basename "$k" .der).pem"
    echo "   trusts $(basename "$k" .der)"
done

if [ -n "$media" ]; then
    echo "== the media partition: ERASING $media for the box's copies of the show videos"
    umount "$media"* 2>/dev/null || true
    parted -s "$media" mklabel gpt mkpart stage-media ext4 1MiB 100%
    sleep 2
    part=$(lsblk -lnpo NAME "$media" | sed -n 2p)
    mkfs.ext4 -F -q -L stage-media -m 0 "$part"
fi

echo "== SSH: keys only"
cat > /etc/ssh/sshd_config.d/10-stage.conf <<EOF
# The stage box: SSH with a key only (setup.sh).
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
EOF
systemctl reload ssh

echo "== Bluetooth on, for a speaker: never discoverable; it pairs only when the dashboard asks"
sed -i '/^dtoverlay=disable-bt$/d' /boot/firmware/config.txt      # a box set up before 2026-10-25 had it off
systemctl enable hciuart.service 2>/dev/null || true             # a Pi 4's Bluetooth UART; a Pi 5 has none to enable

echo "== start at boot: no login prompt, the browser instead"
systemctl daemon-reload
systemctl disable getty@tty1.service 2>/dev/null || true
systemctl set-default graphical.target
udevadm control --reload

if [ "$overlay" = 1 ]; then
    echo "== the root filesystem read-only (an overlay in RAM), so pulling the power is safe"
    raspi-config nonint do_overlayfs 0
fi
echo "== done. Reboot to start: sudo reboot"
