#!/bin/bash
# The openarm image's entrypoint: make sure the teleop certificate exists, then run the command.
#
# The certificate is per machine -- it names the address the headset connects to -- so it is never
# in the image. run.sh mounts a host directory over the bridge's certs/; if that is empty, generate
# a pair here with the bridge's own script, which reads this machine's address.
set -o pipefail
CERTS=/openarm_ws/src/openarm/certs
if [ ! -s "$CERTS/cert.pem" ] || [ ! -s "$CERTS/key.pem" ]; then
  echo "openarm-entrypoint: no teleop certificate in $CERTS -- generating one for this machine"
  bash /openarm_ws/src/openarm/scripts/generate_certs.sh \
    || echo "openarm-entrypoint: certificate generation FAILED; teleop will not serve until it exists" >&2
fi
exec "$@"
