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
browser=chromium
apt-cache show chromium >/dev/null 2>&1 || browser=chromium-browser
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq cage "$browser" curl alsa-utils python3 fonts-dejavu-core avahi-daemon e2fsprogs parted
# No remote desktop of any kind, by decision.
for p in realvnc-vnc-server wayvnc xrdp tigervnc-standalone-server x11vnc; do
    dpkg -s "$p" >/dev/null 2>&1 && DEBIAN_FRONTEND=noninteractive apt-get purge -y -qq "$p"
done

echo "== the stage user (no password, no SSH)"
id stage >/dev/null 2>&1 || useradd -m -s /bin/bash stage
usermod -aG video,render,audio,input stage
passwd -l stage >/dev/null

echo "== the box's own programs"
install -d /usr/local/lib/stage
install -m 0755 bin/stage-conf bin/stage-audio bin/stage-kiosk bin/stage-mode bin/stage-agent bin/stage-media /usr/local/lib/stage/
install -d /srv/stage-media                       # the media partition's mount point, on the read-only root
install -m 0644 systemd/*.service /etc/systemd/system/
install -m 0644 udev/99-stage-display.rules /etc/udev/rules.d/
[ -e /boot/firmware/stage.conf ] || install -m 0644 stage.conf.example /boot/firmware/stage.conf

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

echo "== Bluetooth off (nothing here uses it)"
grep -q "^dtoverlay=disable-bt" /boot/firmware/config.txt || echo "dtoverlay=disable-bt" >> /boot/firmware/config.txt
systemctl disable --now bluetooth.service hciuart.service 2>/dev/null || true

echo "== start at boot: no login prompt, the browser instead"
systemctl daemon-reload
systemctl disable getty@tty1.service 2>/dev/null || true
systemctl set-default graphical.target
systemctl enable stage-mode.service stage-media.service stage-kiosk.service stage-agent.service
udevadm control --reload

if [ "$overlay" = 1 ]; then
    echo "== the root filesystem read-only (an overlay in RAM), so pulling the power is safe"
    raspi-config nonint do_overlayfs 0
fi
echo "== done. Reboot to start: sudo reboot"
