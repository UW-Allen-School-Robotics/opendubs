# Robot Records

One YAML file per physical OpenDubs unit (`<unit>.yaml`), keyed by its immutable unit name (`opendubs1`, `opendubs2`, ...).

Used by `deploy-all.sh` (recorded into `/opt/opendubs/DEPLOYED`) and `tools/deploy-audit.py` to answer:
- **Hardware generation (`revision`)**: which physical build revision the unit carries (`rev-A`, `rev-B`, ...).
- **Purpose (`profile`)**: which operational profile the unit is deployed for (e.g. `demo`, `sensor-rig`).
- **Fitted modules (`modules`)**: optional hardware/software subsystems present on the unit.
- **Runtime expectations (`containers`)**: Docker containers expected to exist on the unit.
