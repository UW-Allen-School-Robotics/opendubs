# --no-stub must be PREPENDED, and that is the whole reason this file exists.
#
# It is a GLOBAL esptool option, so it has to appear before the `write_flash` subcommand.
# `upload_flags` in platformio.ini appends after it, where esptool does not accept it -- which is
# why an earlier version of this board's config passed --no-stub and still failed to connect.
#
# Why --no-stub at all: esptool normally uploads a small helper ("stub") to speed up flashing, and
# on the Dial that helper restarts the chip's USB. The Mac loses the connection mid-upload and the
# flash fails. Skipping it is slower and works. Taken from the maintainers' own M5Dial project.
Import("env")  # noqa: F821
env.Prepend(UPLOADERFLAGS=["--no-stub"])  # noqa: F821
