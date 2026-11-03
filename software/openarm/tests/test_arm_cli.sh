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

echo "$pass passed, $fail failed"
[ "$fail" -eq 0 ]
