// WALK-IN TEST MODE. An instrument you can read from six metres away.
//
// WHY THIS EXISTS. The measurement the whole motion-saliency design rests on is "walk in from 6 m
// and find where detection first fires". That test was asked for three times and could not be
// performed once, for three separate reasons, all of them failures of the instrument rather than
// of the person:
//
//   1. The only cue was EYE MOVEMENT, which is subtle by design AND indistinguishable from the
//      autonomous saccades still running. "Did it track me or was that a random glance?" has no
//      answer from across a room.
//   2. The preview is 320x180 on a 1280x720 panel -- about an inch wide, unreadable past a couple
//      of feet. Every number needed was on screen and none of it could be seen.
//   3. The motion code was not even on the device; it lived on another branch. The test was
//      impossible regardless.
//
// So this mode throws away subtlety. THE WHOLE SCREEN IS THE INDICATOR: a full-screen colour that
// states what is being attended to, which is legible at any distance a person can see the panel
// at all. The numbers are 44 pt -- the largest font built into this image -- for when you are close
// enough to want them.
//
// It also answers the IMU question, which I previously asked for with no readout anywhere: the
// gate state and the measured rotation rate are on the glass in large type, so "does the IMU read"
// is a glance rather than a serial session.
//
// Build-flagged, absent when off. The face is HIDDEN in this mode rather than drawn behind the
// panel: leaving it visible would reintroduce exactly the ambiguity this exists to remove.
#pragma once

// Builds the walk-in panel. Call with the LVGL lock held. No-op unless OPENDUBS_WALKIN is set.
void walkinBuild();

// Updates it from the perception layer. Call with the LVGL lock held, from the face's own loop.
// Returns true if walk-in mode is active, so the caller can skip the normal face update.
bool walkinTick(unsigned nowMs);
