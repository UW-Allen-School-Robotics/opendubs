#!/bin/sh
# On Spot's Orin, as root, once: close it to campus Wi-Fi except SSH by key. Run from the Mac, in the
# separate Terminal app, with Spot sitting and no player running:
#   ssh -t dino@10.18.3.175 sudo sh /home/opendubs/opendubs-spot/orin/harden.sh
#
# Why (found 2026-11-23): the Orin is on Spot's payload network (eth0) and on campus Wi-Fi (wlan0). SSH
# took passwords from all of campus, and the ROS 1 master (11311), about 25 ROS node ports, port 4000 and
# rpcbind (111) listened on every interface. From the Orin, Spot's API is one hop away.
#
# What it does:
#   1. dino gets the same SSH key as opendubs, so step 2 can't lock it out. A password still works at
#      the Orin's own screen and keyboard.
#   2. SSH takes keys only, and no root login.
#   3. ufw: everything in on the payload network, docker0 and the USB-C rescue port (192.168.55.1); from
#      campus, SSH only; nothing forwarded between networks (Spot's default route points at the Orin).
#      Docker's published ports bypass ufw, so the end lists any that exist.
# Running it again changes nothing.
set -eu
[ "$(id -u)" = 0 ] || { echo "refused: run it with sudo" >&2; exit 1; }

echo "1. dino's SSH key"
install -d -m 700 -o dino -g dino /home/dino/.ssh
touch /home/dino/.ssh/authorized_keys
while IFS= read -r key; do
    [ -n "$key" ] || continue
    grep -qxF "$key" /home/dino/.ssh/authorized_keys || echo "$key" >> /home/dino/.ssh/authorized_keys
done < /home/opendubs/.ssh/authorized_keys
chown dino:dino /home/dino/.ssh/authorized_keys
chmod 600 /home/dino/.ssh/authorized_keys

echo "2. SSH: keys only"
cat > /etc/ssh/sshd_config.d/10-opendubs-keys-only.conf <<'EOF'
# Written by opendubs software/spot/orin/harden.sh: SSH by key only (the Orin is on campus Wi-Fi).
PasswordAuthentication no
ChallengeResponseAuthentication no
PermitRootLogin no
EOF
sshd -t
systemctl reload ssh                  # open sessions stay up

echo "3. firewall"
command -v ufw >/dev/null || apt-get install -y ufw || { apt-get update && apt-get install -y ufw; }
ufw default deny incoming
ufw default allow outgoing
ufw default deny routed
for ifc in eth0 docker0 l4tbr0 usb0 rndis0; do
    ufw allow in on "$ifc"
done
ufw allow 22/tcp                      # SSH from campus, keys only
ufw --force enable
ufw status verbose

echo "docker containers with published ports (these bypass ufw):"
docker ps --format '{{.Names}} {{.Ports}}' | grep -- '->' || echo "  none"
echo "Done."
