#!/usr/bin/env bash
# ExecStart for opendubs-airplay.service. A script rather than an inline ExecStart so the robot's
# name (which has a space) is quoted once, correctly, and so an unpinned image is refused.
#
#   --net host         AirPlay control (TCP 7000), its dynamic data ports, and nqptp's PTP
#                      (UDP 319/320, exclusive) are on the robot's own interfaces.
#   ENABLE_AVAHI=0     do not start the image's own avahi and D-Bus; advertise through the host's,
#                      reached through the two sockets mounted below. One mDNS responder per host.
set -euo pipefail
: "${SHAIRPORT_IMAGE:?software/stack/versions.env must pin SHAIRPORT_IMAGE}"
case "$SHAIRPORT_IMAGE" in
  *@sha256:*) ;;
  *) echo "refusing an image that is not pinned by digest: $SHAIRPORT_IMAGE" >&2; exit 2 ;;
esac
NAME="${OPENDUBS_ROBOT_NAME:-OpenDubs}"
docker rm -f opendubs-airplay >/dev/null 2>&1 || true
exec docker run --rm --name opendubs-airplay \
  --net host --cap-add SYS_NICE \
  -e ENABLE_AVAHI=0 \
  -v /run/dbus:/run/dbus \
  -v /run/avahi-daemon:/run/avahi-daemon:ro \
  -v /run/opendubs/airplay:/run/opendubs/airplay \
  -v /etc/opendubs/airplay/shairport-sync.conf:/etc/shairport-sync.conf:ro \
  "$SHAIRPORT_IMAGE" -a "$NAME"
