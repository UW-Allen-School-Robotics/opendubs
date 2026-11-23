#!/bin/sh
# On Spot's Orin, as root, once: close it to campus Wi-Fi except SSH. Run from the Mac, in the
# separate Terminal app, with Spot sitting:
#   ssh -t dino@10.18.3.175 sudo sh /home/opendubs/opendubs-spot/orin/harden.sh
#
# Why (found 2026-11-23): the Orin is on Spot's payload network (eth0) and on campus Wi-Fi (wlan0). SSH
# took passwords from all of campus, and the ROS 1 master (11311), about 25 ROS node ports, port 4000 and
# rpcbind (111) listened on every interface. From the Orin, Spot's API is one hop away.
#
# What it does: enables ufw. Everything comes in on the payload network, docker0 and the USB-C rescue
# port (192.168.55.1). From campus, only SSH, with passwords still on (the owner's choice). Nothing is
# forwarded between networks (Spot's default route points at the Orin). Docker's published ports
# bypass ufw, so the end lists any that exist.
# Running it again changes nothing.
set -eu
[ "$(id -u)" = 0 ] || { echo "refused: run it with sudo" >&2; exit 1; }

command -v ufw >/dev/null || apt-get install -y ufw || { apt-get update && apt-get install -y ufw; }
ufw default deny incoming
ufw default allow outgoing
ufw default deny routed
for ifc in eth0 docker0 l4tbr0 usb0 rndis0; do
    ufw allow in on "$ifc"
done
ufw allow 22/tcp                      # SSH from campus
ufw --force enable
ufw status verbose

echo "docker containers with published ports (these bypass ufw):"
docker ps --format '{{.Names}} {{.Ports}}' | grep -- '->' || echo "  none"
echo "Done."
