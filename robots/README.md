# Robot Records

One folder per physical OpenDubs unit, named by its immutable unit name (`opendubs1`, `opendubs2`, ...). Its record is `robots/<unit>/robot.yaml`; the unit's other files go in the same folder.

`tools/deploy-audit.py` reads the record with PyYAML when it is installed, and otherwise with a small reader for the subset the records use: top-level `key: value`, `key: []`, and under a bare `key:` either `- item` lines or one level of `name: value` lines. Keep records to that subset.

Used by `deploy-all.sh` (recorded into `/opt/opendubs/DEPLOYED`) and `tools/deploy-audit.py` to answer:
- **Hardware generation (`revision`)**: which physical build revision the unit carries (`rev-A`, `rev-B`, ...).
- **Purpose (`profile`)**: which operational profile (`robots/profiles/<profile>.yaml`, e.g. `demo`, `sensor-rig`, `dora-eval`) the unit runs.
- **Fitted modules (`modules`)**: optional hardware/software subsystems present on the unit.
- **Runtime expectations (`containers`)**: Docker containers expected to exist on the unit.

## Profiles (`robots/profiles/`)
Each profile YAML (`demo.yaml`, `sensor-rig.yaml`, `dora-eval.yaml`, ...) declares:
- `flags`: `/etc/opendubs/*.enabled` `ConditionPathExists` flag files (`true` = `touch`, `false` = `rm -f`).
- `services.start` / `services.stop`: systemd units to restart or stop when switching profiles.

Common commands:
```sh
python3 tools/profiles.py --list                                          # list available profiles
python3 tools/profiles.py --check                                         # validate profiles + robot records offline
OPENDUBS_APPLY_PROFILE=1 ./deploy-all.sh opendubs2                        # apply the record's profile on deploy
OPENDUBS_APPLY_PROFILE=1 OPENDUBS_PROFILE=sensor-rig ./deploy-all.sh opendubs2  # override profile for a rig run
python3 tools/deploy-audit.py opendubs2                                   # audit files, services, containers, and active profile
```
