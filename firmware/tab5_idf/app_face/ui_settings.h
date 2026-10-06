// The settings panel: audio and on-screen telemetry, as RUNTIME settings.
//
// HARVESTED, NOT WRITTEN. An earlier version of this hand-rolled LVGL input handling, a custom
// hold gesture and a widget layer -- 1,363 lines reinventing what the Tab5 demo already ships and
// what is already a dependency of this build. Emma caught it: "why are we reinventing touch? This
// HAD to be fully built out in the demo firmware."
//
// It was. `smooth_ui_toolkit`'s lvgl_cpp gives Button, Switch, Slider, Label and Container with
// `.onClick().connect([]{...})` and `.onValueChanged()`, and the demo's app_launcher drives
// fifteen working touch panels with them -- including panel_spk_volume.cpp, which does volume
// through GetHAL()->setSpeakerVolume() / getSpeakerVolume(). Those HAL calls already existed.
// So there is no input plumbing here at all: the BSP registers the pointer indev, LVGL dispatches,
// and the widgets carry their own callbacks.
//
// WHAT THIS REPLACES: kCuesEnabled, a compile-time constant that hard-disabled audio because the
// Mode tone was too loud. That was a compile-time lie about a runtime setting. Audio enable and
// volume now live here, persist in NVS, and survive a reboot.
//
// The telemetry switches are deliberately a DIFFERENT mechanism from the dev instruments. The
// walk-in panel, the perception preview and serial telemetry are compiled OUT of a release --
// absent, not quiet. These are operator-facing readouts that ship, default off, and can be turned
// on in the field without a reflash.
#pragma once

#include <stdint.h>

// Builds the panel, hidden. Call with the LVGL lock held.
void settingsBuild();

// Shows it. Called from the HMI's SETTINGS button -- settings no longer owns a corner of the
// face, because the HMI is now the screen a touch reveals and settings is a page within it.
void settingsOpen();

// True while the settings panel is showing, so the face skips its own rendering.
bool settingsOwnsPanel();

// ---- the settings themselves, read by the rest of the firmware -------------------------------
bool settingsAudioEnabled();      // replaces kCuesEnabled
bool settingsTelemetryVisible();
// The tracking reticle, off by default: on a robot it is a box chasing people across the face.
bool settingsReticleVisible();  // the on-screen readouts
