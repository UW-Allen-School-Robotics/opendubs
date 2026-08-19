#!/usr/bin/env bash
# OpenDubs AGX-Thor panel session script. Runs INSIDE the kiosk GNOME (Xorg)
# session via ~/.config/autostart/opendubs-panel.desktop, so DISPLAY/XAUTHORITY
# are inherited. Does the two things that must happen in-session (not from a root
# service): rotate the forced no-EDID HDMI bar panel to landscape *after* mutter
# applies its own config (retry loop), and run the evdev->XTEST touch bridge with
# the correct rotation. kiosk is in the `input` group so it can read the device.
#
# Why in-session, not a root systemd service: (1) mutter overrides a root
# service's xrandr rotation when the session starts; (2) a root bridge can't
# reconnect across the greeter->session Xorg switch. See memory thor-kiosk-display.
#
# TOUCH_ROT is per-unit (this AGX mount = "right", 180deg from the old Thor's
# "left"). Change here if the panel is remounted.
exec >>"$HOME/opendubs-panel.log" 2>&1
echo "=== session-panel start $(date) DISPLAY=$DISPLAY ==="
export TOUCH_ROT=right

# Rotate to landscape (retry until it sticks — mutter sets its own config first).
for i in $(seq 1 25); do
  xrandr --output HDMI-0 --rotate right --primary 2>/dev/null
  if xrandr 2>/dev/null | grep -q "HDMI-0 connected primary 1920x480"; then
    echo "landscape after ~${i}s"; break
  fi
  sleep 1
done

# Kill all blanking (this combo panel powers its touch board from the display).
xset s off 2>/dev/null || true
xset s noblank 2>/dev/null || true
xset -dpms 2>/dev/null || true
gsettings set org.gnome.desktop.session idle-delay 0 2>/dev/null || true
gsettings set org.gnome.desktop.screensaver idle-activation-enabled false 2>/dev/null || true
gsettings set org.gnome.desktop.screensaver lock-enabled false 2>/dev/null || true

exec python3 -u /opt/opendubs/kiosk-dashboard/tools/touch_bridge.py
