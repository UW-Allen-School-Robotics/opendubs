#!/usr/bin/env bash
# OpenDubs dashboard autostart — runs in the kiosk user's GNOME session via
# ~/.config/autostart. Env (DISPLAY/XAUTHORITY/DBUS_SESSION_BUS_ADDRESS/
# XDG_RUNTIME_DIR) is inherited from the session. Waits for the backend, then
# runs Chromium fullscreen (kiosk) on the dashboard. Logs to ~/dashboard.log.
#
# --disable-gpu: this Jetson's GL stack fails Chromium's EGL init; software
# rendering is fine for a 2D dashboard and avoids the GPU-process crash loop.
#
# IMPORTANT: snap Chromium is single-instance. Re-running `chromium <URL>` while
# a browser is already up does NOT spawn a new process — it re-navigates the
# existing window to <URL> (resetting the SPA to its default tab) and exits.
# The old `while true; do chromium <URL>; done` loop therefore reloaded the page
# to MASTER STATUS every few seconds. So we launch ONCE and only relaunch if the
# real browser process has actually died.
set -u
exec >>"$HOME/dashboard.log" 2>&1
echo "=== dashboard autostart $(date) ==="

URL="http://localhost:8080"
# Matches the real browser process (not the crashpad handler or this launcher).
CHROME_MATCH="chrome --password-store"

for i in $(seq 1 60); do
  curl -sf "$URL/api/health" >/dev/null 2>&1 && { echo "backend up"; break; }
  sleep 1
done

HAVE_XPROP=0
command -v xprop >/dev/null 2>&1 && HAVE_XPROP=1

# Ensure every Chromium window is WM-fullscreen. --kiosk alone isn't honored by
# mutter on this rotated/forced-EDID panel, so we set _NET_WM_STATE_FULLSCREEN.
# Idempotent: skips windows that are already fullscreen (when xprop is available).
ensure_fs() {
  local wid state
  for wid in $(xdotool search --class chromium 2>/dev/null); do
    if [ "$HAVE_XPROP" = 1 ]; then
      state=$(xprop -id "$wid" _NET_WM_STATE 2>/dev/null)
      case "$state" in *_NET_WM_STATE_FULLSCREEN*) continue;; esac
    fi
    wmctrl -i -r "$wid" -b add,fullscreen 2>/dev/null && echo "asserted fullscreen wid=$wid $(date)"
  done
}

launch() {
  echo "launching chromium $(date)"
  chromium --kiosk \
    --disable-gpu --disable-gpu-compositing \
    --noerrdialogs --disable-infobars --disable-session-crashed-bubble \
    --no-first-run --check-for-update-interval=31536000 \
    --disable-pinch --overscroll-history-navigation=0 \
    --autoplay-policy=no-user-gesture-required \
    "$URL" &
}

# Launch once, then supervise: relaunch ONLY if the browser process is gone.
# Never re-issue `chromium <URL>` against a live browser (that caused the
# every-few-seconds reset-to-default-tab bug).
while true; do
  if ! pgrep -f "$CHROME_MATCH" >/dev/null 2>&1; then
    launch
    sleep 8   # give it time to come up before the next check
  fi
  ensure_fs
  sleep 3
done
