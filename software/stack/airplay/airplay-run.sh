#!/usr/bin/env bash
# ExecStart for opendubs-airplay.service. A script rather than an inline ExecStart so the robot's
# name (which has a space) is quoted once, correctly, and so an unpinned image is refused.
#
#   --net host         AirPlay control (TCP 7000), its dynamic data ports, and nqptp's PTP
#                      (UDP 319/320, exclusive) are on the robot's own interfaces.
#   ENABLE_AVAHI=0     do not start the image's own avahi and D-Bus; advertise through the host's,
#                      reached through the two sockets mounted below. One mDNS responder per host.
#   apparmor=unconfined
#                      Docker's default AppArmor profile has no D-Bus rules, so the host's bus
#                      refuses the container's very first call ("An AppArmor policy prevents this
#                      sender…", measured on opendubs2 2026-10-14) and shairport-sync exits without
#                      advertising. Unconfined is the minimal fix; the container already runs as
#                      root on the host network. Hardening follow-up: a profile that is docker-default
#                      plus D-Bus access to org.freedesktop.Avahi only (workplan AP.5).
set -euo pipefail
: "${SHAIRPORT_IMAGE:?software/stack/versions.env must pin SHAIRPORT_IMAGE}"
case "$SHAIRPORT_IMAGE" in
  *@sha256:*) ;;
  *) echo "refusing an image that is not pinned by digest: $SHAIRPORT_IMAGE" >&2; exit 2 ;;
esac
NAME="${OPENDUBS_ROBOT_NAME:-OpenDubs}"

# The robot's IPv4 address on the interface the host's avahi advertises on (see shairport-sync.conf
# for why IPv4). Per robot and from DHCP, so it is read here, at each start, never committed.
# OPENDUBS_AIRPLAY_IFACE (robot.env) overrides the interface.
IFACE="${OPENDUBS_AIRPLAY_IFACE:-$(sed -n 's/^allow-interfaces=\([^,]*\).*/\1/p' /etc/avahi/avahi-daemon.conf | head -1)}"
ADDR="$(ip -4 -o addr show dev "${IFACE:-none}" 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -1)"
if [ -z "$ADDR" ]; then
  echo "no IPv4 address on interface '${IFACE}' yet; systemd will retry" >&2
  exit 1
fi
CONF=/run/opendubs/airplay/shairport-sync.conf
sed "s|@ADDRESS@|$ADDR|" /etc/opendubs/airplay/shairport-sync.conf > "$CONF"
echo "receiver '$NAME' on $IFACE $ADDR"

docker rm -f opendubs-airplay >/dev/null 2>&1 || true
# --pull never: the image comes only from provisioning (provision-airplay.sh). At start a missing
# image is a clear error at once, rather than a registry pull that hangs on DNS with no internet.
exec docker run --rm --pull never --name opendubs-airplay \
  --net host --cap-add SYS_NICE --security-opt apparmor=unconfined \
  -e ENABLE_AVAHI=0 \
  -v /run/dbus:/run/dbus \
  -v /run/avahi-daemon:/run/avahi-daemon:ro \
  -v /run/opendubs/airplay:/run/opendubs/airplay \
  -v "$CONF":/etc/shairport-sync.conf:ro \
  "$SHAIRPORT_IMAGE" -a "$NAME"
