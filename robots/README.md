# Robot Records

One folder per physical OpenDubs unit, named by its immutable unit name (`opendubs1`, `opendubs2`, ...). Its record is `robots/<unit>/robot.yaml`; the unit's other files go in the same folder.

`tools/deploy-audit.py` reads the record with PyYAML when it is installed, and otherwise with a small reader for the subset the records use: top-level `key: value`, `key: []`, and under a bare `key:` either `- item` lines or one level of `name: value` lines. Keep records to that subset.

Used by `deploy-all.sh` (recorded into `/opt/opendubs/DEPLOYED`) and `tools/deploy-audit.py` to answer:
- **Hardware generation (`revision`)**: which physical build revision the unit carries (`rev-A`, `rev-B`, ...).
- **Purpose (`profile`)**: which operational profile the unit is deployed for (e.g. `demo`, `sensor-rig`).
- **Fitted modules (`modules`)**: optional hardware/software subsystems present on the unit.
- **Runtime expectations (`containers`)**: Docker containers expected to exist on the unit.
