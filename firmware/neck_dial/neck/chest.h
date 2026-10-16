// THE CHEST LIGHT'S ARGUMENT, parsed and validated away from the hardware.
//
// The goBILDA RGB Indicator Light (3118-0808-0002) behind the chest plate takes an ordinary servo
// pulse, and the PULSE WIDTH IS THE COLOUR. goBILDA's Product Insight #4 publishes the map:
//
//     500 us  off        1300  yellow      1700  blue
//    1100     red        1400  sage        1800  indigo
//    1200     orange     1500  green       1900  violet
//                        1600  azure       2500  white
//
// with a smooth gradient between, and two rules that shape everything here:
// ANYTHING BELOW 1100 IS OFF, and ANYTHING ABOVE 1900 IS SOLID WHITE.
//
// THE COLOUR IS NOT DECIDED HERE and deliberately so. The Thor maps the robot's winning state
// colour -- the same one the PRISM shows -- to a pulse, and sends the PULSE over the wire. One map,
// in the backend, next to the PRISM table, instead of two that can disagree about what red is.
//
// `CHEST OFF` SENDS 500 us RATHER THAN STOPPING THE PULSES. Silence would leave the light in a
// state nobody has characterised: it might hold the last colour, go dark, or do something else
// entirely, and "off" that might not be off is not off. 500 us is a DEFINED off, from the table.
//
// Parsing lives here, as pure compute, for the same reason the control law and the tilt interlock
// do: the board it runs on has no USB cable and no console, so "it compiles" is not a result.

#pragma once
#include <cstdint>

namespace neck {

// The light's own limits, from the vendor. Outside these it is refused, not clamped: a caller
// asking for 3000 has a bug, and quietly showing it white would hide that.
constexpr uint16_t kChestMinUs = 500;
constexpr uint16_t kChestMaxUs = 2500;
constexpr uint16_t kChestOffUs = 500;      // a DEFINED off; see above

/**
 * Parse a CHEST argument: a pulse in microseconds, or `OFF`.
 *
 * `off` (optional) says WHICH FORM was used, not what the pulse happens to be. `CHEST OFF` and
 * `CHEST 500` apply the identical pulse, and the protocol says the reply states what was applied
 * -- so the reply has to echo the caller's own form back, or a backend that sends 500 and reads
 * back "off" has to special-case a reply it did not ask for.
 *
 * Returns false and touches nothing for anything else -- an empty argument, a word that is not
 * OFF, trailing rubbish after a number, or a value outside the light's range.
 */
bool parseChest(const char* args, uint16_t* us, bool* off = nullptr);

}  // namespace neck
