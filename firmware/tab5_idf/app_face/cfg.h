#pragma once
#include <stdint.h>
#include <stddef.h>

/*
 * AUTHORED CONFIGURATION, PULLED NOT PUSHED.
 *
 * `CFG <url>` fetches a configuration set from the Thor, validates it, and stores it on the head so
 * it survives a reboot. Specified in notes/design/face-config-data-model.md; this implements the
 * subset that needs nothing from the choreography branch:
 *
 *   IN  -- themes, as a named palette of non-reserved state colours. JSON keyed by name.
 *   OUT -- background images and other assets. Those need the size and cost bounds that are
 *          choreography 3.4 work, and a theme that is only colours needs none of them.
 *
 * Three rules from the data model that are easy to get subtly wrong:
 *
 *  1. RESERVED STATES ARE NEVER LOOKED FOR. The loader walks the compiled kStates and reads a
 *     colour only where `reserved == false`. A file naming "E_STOP" is not rejected -- the key is
 *     never read. The note is explicit about why: "a rejection is a code path that can have a bug,
 *     and an absent key is not a code path at all."
 *  2. ABSENT MEANS COMPILED DEFAULT, NEVER ZERO. On this face zero is a plausible-looking wrong
 *     answer -- kStates[0], kEmotes[0] -- so a missing key inherits and a missing file falls back.
 *  3. A NAME THIS FIRMWARE DOES NOT KNOW IS A WARNING, NOT A FAILURE. A set authored against a
 *     newer vocabulary must still load the parts this firmware does understand.
 */

// Kick the mount+load, WITHOUT waiting. Safe on the boot path: it only spawns a task. Call it at
// startup so the first `THEME <authored name>` does not merely trigger the mount and miss.
void cfgStart();

void cfgBegin();

// Fetch and install from a BASE url, whose `manifest.json` is read first. True on install.
// `msg` always receives a human verdict: what landed, what was ignored, and why.
bool cfgFetch(const char* baseUrl, char* msg, size_t cap);

const char* cfgStatus();

// ---- authored themes, for theme::name() and theme::stateColour() ----
int         cfgThemeCount();
const char* cfgThemeName(int i);
// The authored colour for a state, BY NAME. False means "this theme does not name it", which the
// caller must treat as inherit-the-compiled-colour rather than as black.
bool        cfgThemeColour(int i, const char* stateName, uint32_t* rgb);
