#include "badge_screen.h"

#define LGFX_USE_V1
#include <LovyanGFX.hpp>
#include <cstring>
#include <initializer_list>
#include <cstdio>

namespace badge {
namespace {

// The M5Dial's panel: GC9A01, 240 x 240, 3-wire SPI. No touch is declared, on purpose (see the header).
class Panel : public lgfx::LGFX_Device {
    lgfx::Panel_GC9A01 panel_;
    lgfx::Bus_SPI      bus_;
    lgfx::Light_PWM    light_;
public:
    Panel() {
        {
            auto c = bus_.config();
            c.spi_host = SPI3_HOST;
            c.spi_mode = 0;
            c.freq_write = 40000000;      // M5GFX runs it at 80 MHz; 40 is the safe start
            c.freq_read = 16000000;
            c.spi_3wire = true;
            c.use_lock = true;
            c.dma_channel = SPI_DMA_CH_AUTO;
            c.pin_sclk = 6; c.pin_mosi = 5; c.pin_miso = -1; c.pin_dc = 4;
            bus_.config(c);
            panel_.setBus(&bus_);
        }
        {
            auto c = panel_.config();
            c.pin_cs = 7; c.pin_rst = 8; c.pin_busy = -1;
            c.panel_width = 240; c.panel_height = 240;
            c.offset_x = 0; c.offset_y = 0; c.offset_rotation = 0;
            c.readable = false; c.invert = true; c.rgb_order = false;
            c.dlen_16bit = false; c.bus_shared = false;
            panel_.config(c);
        }
        {
            auto c = light_.config();
            c.pin_bl = 9; c.invert = false; c.freq = 44100; c.pwm_channel = 7;
            light_.config(c);
            panel_.setLight(&light_);
        }
        setPanel(&panel_);
    }
};

Panel* g_panel = nullptr;
View   g_shown;
bool   g_drawn = false;

constexpr uint32_t kGrey = 0x5a6068;
constexpr uint32_t kHoldYellow = 0xffd23f;

uint16_t c565(uint32_t rgb) { return lgfx::color565((rgb >> 16) & 0xff, (rgb >> 8) & 0xff, rgb & 0xff); }

bool same(const View& a, const View& b) {
    return a.told == b.told && a.stale == b.stale && !strcmp(a.state, b.state) && !strcmp(a.driver, b.driver)
        && a.rgb == b.rgb && !strcmp(a.local, b.local) && a.tiltLo == b.tiltLo && a.tiltHi == b.tiltHi
        && a.wifi == b.wifi;
}

// The STATES name as the face labels it: underscores become spaces, and E_STOP is "E-STOP".
void label(const char* state, char* out, size_t n) {
    if (!strcmp(state, "E_STOP")) { snprintf(out, n, "E-STOP"); return; }
    snprintf(out, n, "%s", state);
    for (char* p = out; *p; ++p) if (*p == '_') *p = ' ';
}

void upper(const char* in, char* out, size_t n) {
    size_t i = 0;
    for (; in[i] && i + 1 < n; ++i) out[i] = (in[i] >= 'a' && in[i] <= 'z') ? in[i] - 32 : in[i];
    out[i] = 0;
}

// THE TEXT FITS THE RING. The space is the chord inside the ring (inner radius 100) at a line's height,
// less a margin: about 190 px for the state's label just above the middle (kLabelW), 170 and 150 for the
// lines below it. A fixed size cut AUTONOMOUS off under the ring (2026-10-23).
constexpr int kLabelW = 190;

// The largest of `fonts` in which `text` fits `maxW` pixels; the smallest if none does.
const lgfx::IFont* fit(Panel& d, const char* text, int maxW, std::initializer_list<const lgfx::IFont*> fonts) {
    const lgfx::IFont* pick = nullptr;
    for (const lgfx::IFont* f : fonts) {
        pick = f;
        d.setFont(f);
        if (d.textWidth(text) <= maxW) return f;
    }
    return pick;                                     // none fits: the smallest
}

void draw(const View& v) {
    Panel& d = *g_panel;
    d.startWrite();
    d.fillScreen(TFT_BLACK);
    d.setTextDatum(textdatum_t::middle_center);

    // The ring and the state: only once the robot has said what it is.
    if (v.told) {
        const uint32_t ring = v.stale ? kGrey : v.rgb;
        d.fillArc(120, 120, 119, 100, 0, 360, c565(ring));
        char l[20]; label(v.state, l, sizeof(l));
        // ONE TYPEFACE FOR EVERY STATE, scaled down only as far as a word needs to fit the ring (about
        // 190 px here): AUTONOMOUS is 260 px at full size, DEGRADED and CHARGING just under 200, the rest
        // fit as they are. Stepping down a whole font size instead would shrink the warning states too.
        d.setFont(&fonts::FreeSansBold18pt7b);
        const int w = d.textWidth(l);
        d.setTextSize(w > kLabelW ? (float)kLabelW / w : 1.0f);
        d.setTextColor(TFT_WHITE);
        d.drawString(l, 120, 104);
        d.setTextSize(1.0f);
    }

    // The line beneath: the most important thing the neck has to say, one at a time.
    char line[40] = "", line2[40] = "";
    uint32_t colour = 0xd0d4da;
    if (v.tiltLo >= 0) {
        snprintf(line, sizeof(line), "press to confirm");
        snprintf(line2, sizeof(line2), "tilt %d-%d", v.tiltLo, v.tiltHi);
        colour = kHoldYellow;
    } else if (!strcmp(v.local, "hold")) {
        snprintf(line, sizeof(line), "HOLD");
        colour = kHoldYellow;
    } else if (!strcmp(v.local, "releasing")) {
        snprintf(line, sizeof(line), "releasing hold");
        colour = kHoldYellow;
    } else if (v.local[0]) {
        upper(v.local, line, sizeof(line));                 // RC, TOUCH: the neck knows these itself
    } else if (v.told && v.stale) {
        snprintf(line, sizeof(line), "no word from robot");
    } else if (v.told && v.driver[0]) {
        upper(v.driver, line, sizeof(line));
    } else if (!v.wifi) {
        snprintf(line, sizeof(line), "no Wi-Fi");
        colour = kGrey;
    }
    if (line[0]) {
        if (line2[0]) d.setFont(fit(d, strlen(line) > strlen(line2) ? line : line2, 150, {&fonts::FreeSans12pt7b, &fonts::FreeSans9pt7b}));
        else d.setFont(fit(d, line, 170, {&fonts::FreeSansBold18pt7b, &fonts::FreeSans12pt7b, &fonts::FreeSans9pt7b}));
        d.setTextColor(c565(colour));
        d.drawString(line, 120, v.told ? 150 : 120);
        if (line2[0]) d.drawString(line2, 120, v.told ? 176 : 146);
    }
    d.endWrite();
}

}  // namespace

bool begin() {
    if (g_panel) return true;
    g_panel = new Panel();
    if (!g_panel->init()) { delete g_panel; g_panel = nullptr; return false; }
    g_panel->setBrightness(160);
    g_panel->fillScreen(TFT_BLACK);       // blank until told
    return true;
}

bool running() { return g_panel != nullptr; }

void show(const View& v) {
    if (!g_panel) return;
    if (g_drawn && same(v, g_shown)) return;
    draw(v);
    g_shown = v;
    g_drawn = true;
}

}  // namespace badge
