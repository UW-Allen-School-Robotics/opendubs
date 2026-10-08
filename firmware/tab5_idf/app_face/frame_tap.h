#pragma once

/*
 * THE TWIN'S PICTURE, SHARED. Desktop build only (choreography plan 2.5).
 *
 * The robot's MuJoCo body twin shows this face on its head, so one window shows the whole robot.
 * It cannot draw the face itself -- a second drawing of the face would drift from this one, which is
 * the thing a twin exists to prevent -- so this copies what the twin has ALREADY drawn.
 *
 * Off unless OPENDUBS_FACE_FRAMES names a file. Then, at most ~15 times a second and only after a
 * frame that actually changed, the finished frame is scaled to 320x180 RGB888 and written into that
 * file, memory-mapped, behind a small header:
 *
 *     offset 0   char[4]  "ODFF"
 *            4   u32      version (1)
 *            8   u32      width        12  u32  height        16  u32  row stride in bytes
 *           20   u32      sequence: ODD while a frame is being written, EVEN when it is whole
 *           24   u32[2]   reserved (0)
 *           32   width x height x 3 bytes, RGB, rows top to bottom
 *
 * A reader takes the sequence, copies the pixels, and keeps the copy only if the sequence was even
 * and unchanged -- so it never shows half a frame. Nothing is redrawn to make the copy: in the SDL
 * build's direct render mode the display buffer already holds the whole current frame.
 *
 * NOTHING HERE REACHES THE DEVICE IMAGE: the implementation is guarded in frame_tap.cpp (the vendor
 * CMake globs sources, so a file cannot be excluded by where it lives), and on the device this is an
 * inline no-op.
 */

#ifndef ESP_PLATFORM
void frameTapBegin();     // call once, with the display created; reads OPENDUBS_FACE_FRAMES
#else
inline void frameTapBegin() {}
#endif
