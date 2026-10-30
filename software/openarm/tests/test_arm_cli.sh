#!/bin/bash
# `arm`, the operator's command (software/openarm/arm): the three things that would only go wrong on the robot. The sudoers rule lets kiosk run `systemctl start|stop|restart
# opendubs-arm@left|right.service` with the `.service`, as written, so any other spelling fails there and nowhere else; the checkout comes from the settings file the unit reads;
# a bad side is refused before anything runs. Dry run: nothing is started. Run: bash software/openarm/tests/test_arm_cli.sh
ARM=$(cd "$(dirname "$0")/.." && pwd)/arm
pass=0; fail=0
check() {  # check NAME GOT WANT
  if [ "$2" = "$3" ]; then pass=$((pass + 1)); else fail=$((fail + 1)); echo "FAIL $1: got '$2', want '$3'"; fi
}
export ARM_DRY_RUN=1
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
printf 'OPENDUBS_REPO=%s/checkout\n' "$T" > "$T/env"

check "start names the unit with .service, as the sudoers rule has it" "$(ARM_ENV_FILE=$T/env "$ARM" start left)" "sudo systemctl start opendubs-arm@left.service"
check "the checkout is the settings file's" "$(ARM_ENV_FILE=$T/env "$ARM" status | grep -o "$T/checkout/software/openarm/gafro/arm_service.py")" "$T/checkout/software/openarm/gafro/arm_service.py"
ARM_ENV_FILE=$T/env "$ARM" status middle >/dev/null 2>&1
check "a side that is not left or right is refused" "$?" "64"

check "no OPENDUBS_REPO: the service is the bundle's" "$(ARM_ENV_FILE=/nonexistent "$ARM" status | grep -o '/opt/opendubs/arm/software/openarm/gafro/arm_service.py')" "/opt/opendubs/arm/software/openarm/gafro/arm_service.py"
# The wrapper, run against a stub python that prints what it was given: the DDS profile is set whatever the environment says, and the service does not start without it.
mkdir -p "$T/w/software/openarm/gafro"; touch "$T/w/software/openarm/gafro/arm_service.py"; touch "$T/profile.xml"
printf '#!/bin/bash\necho "profile=$FASTRTPS_DEFAULT_PROFILES_FILE $FASTDDS_DEFAULT_PROFILES_FILE range=$ROS_AUTOMATIC_DISCOVERY_RANGE"\n' > "$T/py"; chmod +x "$T/py"
WRAP=$(cd "$(dirname "$0")/.." && pwd)/opendubs-arm
wrap() { OPENDUBS_REPO="$T/w" OPENDUBS_GAFRO_SETUP=/dev/null OPENDUBS_ARM_WS="$T/nows" OPENDUBS_PYTHON="$T/py" OPENDUBS_DDS_PROFILE="$T/profile.xml" "$@" bash "$WRAP" right 2>/dev/null; }
check "the wrapper sets the DDS profile over a wrong one in the environment" "$(wrap env FASTRTPS_DEFAULT_PROFILES_FILE=/tmp/wrong.xml)" "profile=$T/profile.xml $T/profile.xml range=SUBNET"
rm "$T/profile.xml"; wrap env >/dev/null; check "the wrapper refuses to start without the DDS profile" "$?" "78"
check "up starts the unit and waits for the service" "$(ARM_ENV_FILE=$T/env "$ARM" up left | head -1)" "sudo systemctl start opendubs-arm@left.service    # up to 3 attempts, then wait for: arm status left"
check "up both: left, then right" "$(ARM_ENV_FILE=$T/env "$ARM" up both | grep -o 'opendubs-arm@[a-z]*' | tr '\n' ' ')" "opendubs-arm@left opendubs-arm@right "
check "a bare up is both arms" "$(ARM_ENV_FILE=$T/env "$ARM" up | grep -o 'opendubs-arm@[a-z]*' | tr '\n' ' ')" "opendubs-arm@left opendubs-arm@right "
check "up --supervise says supervise after" "$(ARM_ENV_FILE=$T/env "$ARM" up right --supervise | tail -1)" "arm supervise right"
ARM_ENV_FILE=$T/env "$ARM" status both >/dev/null 2>&1
check "both is only for up" "$?" "64"

echo "$pass passed, $fail failed"
[ "$fail" -eq 0 ]
