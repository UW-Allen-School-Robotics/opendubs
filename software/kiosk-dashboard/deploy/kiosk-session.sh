#!/usr/bin/env bash
# OpenDubs kiosk session — the X client launched by xinit from the
# opendubs-kiosk systemd service. Rotates the panel to landscape, disables
# blanking, sets the touch transform, and runs Chromium fullscreen on the
# dashboard. Output goes to the service journal (journalctl -u opendubs-kiosk).
set -u
echo "=== kiosk session start $(date) ==="

URL="http://localhost:8080"

# 1) Rotate HDMI-0 to 1920x480 landscape (Tegra nvidia rotates via RandR only).
xrandr --output HDMI-0 --rotate left || echo "xrandr rotate failed"

# 1b) Match the touch coordinate transform to the rotation (belt-and-suspenders
#     alongside the InputClass). Matrix for xrandr "left" (90deg CCW).
xinput set-prop "QDtech MPI7019" "Coordinate Transformation Matrix" \
  0 -1 1 1 0 0 0 0 1 2>/dev/null || echo "touch matrix set failed (device not ready yet)"

# 2) Kill all screen blanking / power management.
xset s off || true
xset s noblank || true
xset -dpms || true

# 3) Hide the cursor when idle (belt-and-suspenders; CSS also hides it).
command -v unclutter >/dev/null && unclutter -idle 0.5 -root &

# 4) Wait for the dashboard backend to answer before opening the browser.
for i in $(seq 1 60); do
  if curl -sf "$URL/api/health" >/dev/null 2>&1; then echo "backend up"; break; fi
  sleep 1
done

# 5) Launch Chromium in kiosk mode, restarting it if it ever exits so a browser
#    crash can't drop us back to gdm (which, with autologin, would loop-login).
#    NOTE: snap Chromium's confinement blocks hidden dirs in $HOME, so we do NOT
#    pass --user-data-dir; it uses its own writable snap profile.
while true; do
  echo "launching chromium $(date)"
  chromium \
    --kiosk --app="$URL" \
    --noerrdialogs --disable-infobars --disable-session-crashed-bubble \
    --disable-features=TranslateUI --no-first-run --fast --fast-start \
    --check-for-update-interval=31536000 \
    --overscroll-history-navigation=0 --disable-pinch \
    --autoplay-policy=no-user-gesture-required || echo "chromium exited $?"
  sleep 3
done
