#!/usr/bin/env bash
# Install the AirPlay-compatible receiver (shairport-sync) on a robot. Idempotent; run as root on
# the Thor after software/stack has been deployed to /opt/opendubs/stack:
#
#   sudo /opt/opendubs/stack/airplay/provision-airplay.sh            install; leave on/off as it is
#   sudo /opt/opendubs/stack/airplay/provision-airplay.sh --enable   install and turn it on
#
# Not chained into provision-all.sh yet: it is proven on one robot first.
# Pulls the image BY DIGEST from software/stack/versions.env, and never ships it anywhere (see
# README.md: the image carries GPL-2.0 and LGPL code).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
STACK="$(dirname "$HERE")"
UNIT=opendubs-airplay.service

# shellcheck disable=SC1091
. "$STACK/versions.env"
case "${SHAIRPORT_IMAGE:-}" in
  *@sha256:*) ;;
  *) echo "!! SHAIRPORT_IMAGE in $STACK/versions.env is missing or not pinned by digest" >&2; exit 2 ;;
esac

echo "== OpenDubs AirPlay-compatible receiver (shairport-sync) =="
command -v docker >/dev/null || { echo "!! docker is not installed" >&2; exit 2; }
systemctl is-active --quiet avahi-daemon || { echo "!! avahi-daemon is not running" >&2; exit 2; }

# nqptp (AirPlay 2 timing) needs EXCLUSIVE use of UDP 319/320. Refuse rather than fight a PTP daemon.
if ss -ulnH '( sport = :319 or sport = :320 )' | grep -q . && ! docker ps -q -f name=opendubs-airplay | grep -q .; then
  echo "!! UDP 319/320 are in use by something else (a PTP daemon?):" >&2
  ss -ulnpH '( sport = :319 or sport = :320 )' >&2
  exit 2
fi

echo "-- pulling $SHAIRPORT_IMAGE"
docker pull -q "$SHAIRPORT_IMAGE" >/dev/null

chmod 0755 "$HERE/airplay-prestart.sh" "$HERE/airplay-run.sh"
install -d -m 0755 /etc/opendubs/airplay
install -m 0644 "$HERE/shairport-sync.conf" /etc/opendubs/airplay/shairport-sync.conf
install -m 0644 "$HERE/$UNIT" "/etc/systemd/system/$UNIT"
install -m 0440 "$HERE/opendubs-airplay-sudoers" /etc/sudoers.d/opendubs-airplay
visudo -cf /etc/sudoers.d/opendubs-airplay >/dev/null || { rm -f /etc/sudoers.d/opendubs-airplay; echo "!! sudoers rule invalid; removed" >&2; exit 2; }
systemctl daemon-reload

if [ "${1:-}" = "--enable" ]; then
  systemctl enable --now "$UNIT" >/dev/null
elif systemctl is-enabled --quiet "$UNIT"; then
  systemctl restart "$UNIT"          # already on: pick up a new image, unit or config
fi
echo "== done. receiver: $(systemctl is-enabled "$UNIT" 2>/dev/null || true), $(systemctl is-active "$UNIT" 2>/dev/null || true) =="
