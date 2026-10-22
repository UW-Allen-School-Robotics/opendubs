# Trusted firmware-signing keys

The **public** halves of the project's firmware-signing keys, one file per key, named `<key id>.der`.
The key id is the first 16 hex digits of the file's SHA-256, and `tools/ota_keys.py` refuses a file
whose name doesn't match it. Every OpenDubs firmware trusts exactly the keys in this folder.

- **Normally one key.** None means every board built from this tree refuses every update, and the
  neck Dial won't build.
- **Two only during a rotation**, so that an update signed with either installs.

The private keys are never here: they live only on the Thor
(`software/stack/firmware-signing/README.md`). After changing this folder, run
`python3 ../tools/ota_keys.py` and commit the regenerated `src/ota_keys.h` with it.
