#include "cfg.h"

#ifdef ESP_PLATFORM

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <esp_http_client.h>
#include <esp_rom_crc.h>
#include <cJSON.h>
#include <esp_spiffs.h>
#include <esp_err.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include "mooncake_log.h"
#include "net_cmd.h"

namespace {

const char* kTag = "cfg";

// SPIFFS caps object names at 32 characters (CONFIG_SPIFFS_OBJ_NAME_LEN) and "/spiffs/" spends
// eight of them, so these are deliberately terse rather than tidy.
constexpr int kMaxThemes   = 4;
constexpr int kMaxNameLen  = 24;
constexpr int kMaxSwatches = 12;          // one per non-reserved state, with room to spare
constexpr int kMaxFile     = 4096;        // a palette is a few hundred bytes; this is slack

struct Swatch { char state[24]; uint32_t rgb; };
struct Theme  { char name[kMaxNameLen]; Swatch sw[kMaxSwatches]; int n; };

// ONE STATIC SCRATCH BUFFER, NOT A STACK ONE. 4 KB of locals is more than cmdTask's 6144-byte
// stack can spare once cJSON's frames sit on top of it, and the first flash of this file did not
// survive its twelve healthy seconds -- the bootloader rolled it back, which is exactly what that
// mechanism is for. Static is safe here because the two callers cannot overlap: cfgBegin() runs
// once at startup and cfgFetch() only ever from the single command task.
char g_scratch[kMaxFile];
Theme    g_theme[kMaxThemes];
int      g_nThemes = 0;
uint32_t g_generation = 0;
char     g_slot = 'a';
char     g_status[128] = "cfg: not started";
bool     g_mounted = false;

// ---- paths. Two slots, so an install never damages the set that currently works. ----
void pathMan(char slot, char* out, size_t cap)        { snprintf(out, cap, "/cfg/%cman.json", slot); }
void pathTheme(char slot, int k, char* out, size_t c)  { snprintf(out, c, "/cfg/%ct%d.json", slot, k); }
const char* kActivePath = "/cfg/act";

char otherSlot(char s) { return s == 'a' ? 'b' : 'a'; }

bool readFile(const char* path, char* buf, size_t cap, size_t* len)
{
    FILE* f = fopen(path, "rb");
    if (!f) return false;
    const size_t n = fread(buf, 1, cap - 1, f);
    fclose(f);
    buf[n] = 0;
    if (len) *len = n;
    return n > 0;
}

// ATOMIC, because the contactor can cut this board mid-write and the data model calls out the
// vendor's in-place fprintf as the thing not to copy. Write a temp, flush it to flash, rename.
bool writeFileAtomic(const char* path, const char* data, size_t n)
{
    char tmp[40];
    snprintf(tmp, sizeof(tmp), "%s.t", path);
    FILE* f = fopen(tmp, "wb");
    if (!f) return false;
    const bool ok = fwrite(data, 1, n, f) == n;
    fflush(f);
    fclose(f);
    if (!ok) { remove(tmp); return false; }
    remove(path);                      // SPIFFS rename does not replace an existing name
    return rename(tmp, path) == 0;
}

uint32_t crc32(const char* d, size_t n) { return esp_rom_crc32_le(0, (const uint8_t*)d, n); }

// "#RRGGBB" only. A colour is the one field where a lenient parse would silently produce a
// plausible wrong answer, so anything else is refused by name rather than coerced.
bool parseHex(const char* s, uint32_t* rgb)
{
    if (!s || s[0] != '#' || strlen(s) != 7) return false;
    char* end = nullptr;
    const unsigned long v = strtoul(s + 1, &end, 16);
    if (!end || *end != 0) return false;
    *rgb = (uint32_t)v;
    return true;
}

int stateIndexOf(const char* n);

// Parse one theme file into `t`. Warnings are appended to `msg` and are NOT failures.
bool parseTheme(const char* json, Theme* t, char* msg, size_t cap)
{
    cJSON* root = cJSON_Parse(json);
    if (!root) { snprintf(msg + strlen(msg), cap - strlen(msg), " [parse failed]"); return false; }

    const cJSON* pal = cJSON_GetObjectItemCaseSensitive(root, "palette");
    if (!cJSON_IsObject(pal)) {
        snprintf(msg + strlen(msg), cap - strlen(msg), " [no palette object]");
        cJSON_Delete(root);
        return false;
    }

    t->n = 0;
    // WALK THE COMPILED STATES, NOT THE FILE'S KEYS. This is what makes a reserved state
    // impossible to colour: DEGRADED and E_STOP are skipped here, so their keys are never looked
    // for and no future schema version can expose them by accident.
    for (int i = 0; i < faceStateCount() && t->n < kMaxSwatches; ++i) {
        if (faceStateReserved(i)) continue;
        const char*  sname = faceStateName(i);
        const cJSON* v     = cJSON_GetObjectItemCaseSensitive(pal, sname);
        if (!v) continue;                                   // absent == inherit the compiled colour
        uint32_t rgb = 0;
        if (!cJSON_IsString(v) || !parseHex(v->valuestring, &rgb)) {
            snprintf(msg + strlen(msg), cap - strlen(msg), " [%s: bad colour]", sname);
            continue;                                       // one bad field, not a bad file
        }
        snprintf(t->sw[t->n].state, sizeof(t->sw[t->n].state), "%s", sname);
        t->sw[t->n].rgb = rgb;
        ++t->n;
    }

    // Name the keys we did not understand, so an author learns rather than guesses.
    for (const cJSON* k = pal->child; k; k = k->next) {
        if (!k->string) continue;
        bool known = false;
        for (int i = 0; i < faceStateCount(); ++i)
            if (strcmp(faceStateName(i), k->string) == 0) { known = true; break; }
        if (!known)
            snprintf(msg + strlen(msg), cap - strlen(msg), " [ignored key %s]", k->string);
        else if (faceStateReserved(stateIndexOf(k->string)))
            snprintf(msg + strlen(msg), cap - strlen(msg), " [%s is reserved, not read]", k->string);
    }

    cJSON_Delete(root);
    return t->n > 0;
}

int stateIndexOf(const char* n)
{
    for (int i = 0; i < faceStateCount(); ++i)
        if (strcmp(faceStateName(i), n) == 0) return i;
    return -1;
}

// ---- HTTP, bounded ----
bool fetch(const char* url, char* buf, size_t cap, size_t* len)
{
    esp_http_client_config_t c = {};
    c.url         = url;
    c.timeout_ms  = 8000;
    esp_http_client_handle_t h = esp_http_client_init(&c);
    if (!h) return false;
    bool ok = false;
    if (esp_http_client_open(h, 0) == ESP_OK) {
        esp_http_client_fetch_headers(h);
        const int n = esp_http_client_read_response(h, buf, (int)cap - 1);
        if (n > 0 && esp_http_client_get_status_code(h) == 200) {
            buf[n] = 0;
            if (len) *len = (size_t)n;
            ok = true;
        }
    }
    esp_http_client_close(h);
    esp_http_client_cleanup(h);
    return ok;
}

bool loadSlot(char slot, char* msg, size_t cap)
{
    char path[40];
    char* buf = g_scratch;
    pathMan(slot, path, sizeof(path));
    size_t n = 0;
    if (!readFile(path, buf, kMaxFile, &n)) return false;

    cJSON* man = cJSON_Parse(buf);
    if (!man) return false;
    const cJSON* gen   = cJSON_GetObjectItemCaseSensitive(man, "generation");
    const cJSON* files = cJSON_GetObjectItemCaseSensitive(man, "files");
    const uint32_t generation = cJSON_IsNumber(gen) ? (uint32_t)gen->valuedouble : 0;
    const int count = cJSON_IsObject(files) ? cJSON_GetArraySize(files) : 0;
    cJSON_Delete(man);

    int loaded = 0;
    for (int k = 0; k < count && k < kMaxThemes; ++k) {
        pathTheme(slot, k, path, sizeof(path));
        if (!readFile(path, buf, kMaxFile, &n)) continue;
        Theme t = {};
        // The theme name comes from its own file, so a set names its themes rather than relying on
        // file order -- "an index is not an identity", per the data model.
        cJSON* root = cJSON_Parse(buf);
        if (root) {
            const cJSON* nm = cJSON_GetObjectItemCaseSensitive(root, "name");
            snprintf(t.name, sizeof(t.name), "%s",
                     (cJSON_IsString(nm) && nm->valuestring[0]) ? nm->valuestring : "authored");
            cJSON_Delete(root);
        }
        if (parseTheme(buf, &t, msg, cap)) g_theme[loaded++] = t;
    }
    g_nThemes   = loaded;
    g_slot      = slot;
    g_generation = generation;
    return loaded > 0;
}

}  // namespace

// Mount and load on FIRST USE. See the note at the removed onCreate() call for why this is not on
// the boot path.
void cfgBegin()
{
    // NOT bsp_spiffs_mount(). The vendor defines BSP_ERROR_CHECK_RETURN_ERR as ESP_ERROR_CHECK, so
    // that helper ABORTS THE DEVICE when the mount fails -- which it does on a partition that has
    // never been formatted. It cost three flashes to find: the image downloaded, rebooted, aborted
    // inside the helper, and the bootloader rolled it back before anything could say why. A
    // convenience wrapper that turns a recoverable error into a reboot is worse than no wrapper.
    //
    // Our own base path too. /spiffs is the vendor's configured mount point and sharing it invites
    // a second component to find someone else's filesystem there.
    esp_vfs_spiffs_conf_t conf = {};
    conf.base_path              = "/cfg";
    conf.partition_label        = "storage";
    conf.max_files              = 6;
    conf.format_if_mount_failed = true;      // first use formats; see mountTask for why that is slow
    const esp_err_t me = esp_vfs_spiffs_register(&conf);
    if (me != ESP_OK) {
        snprintf(g_status, sizeof(g_status), "cfg: storage mount failed (%s)", esp_err_to_name(me));
        mclog::tagError(kTag, "{}", g_status);
        return;
    }
    g_mounted = true;

    char act[8] = "";
    size_t n = 0;
    const char slot = (readFile(kActivePath, act, sizeof(act), &n) && (act[0] == 'a' || act[0] == 'b'))
                      ? act[0] : 'a';

    char msg[128] = "";
    if (loadSlot(slot, msg, sizeof(msg)))
        snprintf(g_status, sizeof(g_status), "cfg: slot %c gen %u, %d theme(s)%s",
                 g_slot, (unsigned)g_generation, g_nThemes, msg);
    else
        // COMPILED DEFAULTS, and say so. The data model is explicit that a parse failure must fall
        // back to the compiled set and report which table fell back -- not to an empty config.
        snprintf(g_status, sizeof(g_status), "cfg: no authored set, compiled defaults%s", msg);
    mclog::tagInfo(kTag, "{}", g_status);
}

// MOUNT AND LOAD ON FIRST USE, not at boot. Doing it in onCreate() cost two flashes: the image
// downloaded, failed to stay healthy for its twelve seconds, and the bootloader rolled it back --
// so nothing could report why. Nothing needs an authored theme until something asks for one by
// name, so this runs on a task that is fully up and whose failures can be reported.
namespace {

void mountTask(void*)
{
    cfgBegin();
    vTaskDelete(nullptr);
}

// ON ITS OWN TASK, because the first mount FORMATS a 5.5 MB partition and that takes tens of
// seconds. The obvious alternative -- mount inline -- would block cmdTask, and cmdTask is also the
// 20 Hz lift publisher. A command socket that goes quiet is an annoyance; a lift heartbeat that
// stops mid-press is not.
bool ensureBegun()
{
    static bool started = false;
    if (!started) {
        started = true;
        snprintf(g_status, sizeof(g_status), "cfg: preparing storage (first use formats it)");
        xTaskCreatePinnedToCore(mountTask, "cfgmnt", 4096, nullptr, 1, nullptr, 1);
    }
    return g_mounted;
}

}  // namespace

void cfgStart() { ensureBegun(); }

bool cfgFetch(const char* baseUrl, char* msg, size_t cap)
{
    if (!ensureBegun()) { snprintf(msg, cap, "ERR %s -- retry in a moment", cfgStatus()); return false; }

    char url[320];
    char* buf = g_scratch;
    size_t n = 0;
    const char* sep = (baseUrl[strlen(baseUrl) - 1] == '/') ? "" : "/";
    snprintf(url, sizeof(url), "%s%smanifest.json", baseUrl, sep);
    if (!fetch(url, buf, kMaxFile, &n)) { snprintf(msg, cap, "ERR manifest unreachable"); return false; }

    cJSON* man = cJSON_Parse(buf);
    if (!man) { snprintf(msg, cap, "ERR manifest not JSON"); return false; }
    const cJSON* ver   = cJSON_GetObjectItemCaseSensitive(man, "schema_version");
    const cJSON* files = cJSON_GetObjectItemCaseSensitive(man, "files");
    if (!cJSON_IsNumber(ver) || (int)ver->valuedouble != 1) {
        snprintf(msg, cap, "ERR schema_version must be 1");
        cJSON_Delete(man);
        return false;
    }
    if (!cJSON_IsObject(files) || cJSON_GetArraySize(files) == 0) {
        snprintf(msg, cap, "ERR manifest lists no files");
        cJSON_Delete(man);
        return false;
    }

    // INSTALL INTO THE OTHER SLOT. The set that currently works is untouched until the very last
    // write, so a failure halfway through costs nothing.
    const char slot = otherSlot(g_slot);
    char path[40];
    int wrote = 0, skipped = 0;
    char verdict[96] = "";

    for (const cJSON* f = files->child; f && wrote < kMaxThemes; f = f->next) {
        if (!f->string) continue;
        // Only themes in this pass. Everything else in a set is an asset and waits for the cost
        // bounds; saying so by name is better than ignoring it silently.
        if (strncmp(f->string, "themes/", 7) != 0) { ++skipped; continue; }
        snprintf(url, sizeof(url), "%s%s%s", baseUrl, sep, f->string);
        if (!fetch(url, buf, kMaxFile, &n)) { ++skipped; continue; }
        if (cJSON_IsString(f) && f->valuestring[0]) {
            char want[16];
            snprintf(want, sizeof(want), "%08x", (unsigned)crc32(buf, n));
            if (strcasecmp(want, f->valuestring) != 0) {
                // REPORT THE COMPUTED VALUE. A bare "mismatch" leaves an author guessing which
                // CRC variant to use; this hands them the number the head actually wants.
                snprintf(verdict + strlen(verdict), sizeof(verdict) - strlen(verdict),
                         " [%s: crc want %s got %s]", f->string, f->valuestring, want);
                ++skipped;
                continue;
            }
        }
        pathTheme(slot, wrote, path, sizeof(path));
        if (!writeFileAtomic(path, buf, n)) { ++skipped; continue; }
        ++wrote;
    }

    if (wrote == 0) {
        snprintf(msg, cap, "ERR no themes installed (%d skipped)%s", skipped, verdict);
        cJSON_Delete(man);
        return false;
    }

    // The manifest, rewritten with our generation and only the files we actually kept.
    cJSON* out = cJSON_CreateObject();
    cJSON_AddNumberToObject(out, "schema_version", 1);
    cJSON_AddNumberToObject(out, "generation", (double)(g_generation + 1));
    cJSON* kept = cJSON_AddObjectToObject(out, "files");
    for (int k = 0; k < wrote; ++k) {
        char key[16];
        snprintf(key, sizeof(key), "t%d", k);
        cJSON_AddStringToObject(kept, key, "");
    }
    char* text = cJSON_PrintUnformatted(out);
    cJSON_Delete(out);
    cJSON_Delete(man);
    if (!text) { snprintf(msg, cap, "ERR manifest write failed"); return false; }
    pathMan(slot, path, sizeof(path));
    const bool manOk = writeFileAtomic(path, text, strlen(text));
    cJSON_free(text);
    if (!manOk) { snprintf(msg, cap, "ERR manifest write failed"); return false; }

    // THE LAST WRITE IS THE SWITCH. Until this line lands the head still boots the old set, which
    // is the whole point of having two slots.
    const char act[2] = {slot, 0};
    if (!writeFileAtomic(kActivePath, act, 1)) { snprintf(msg, cap, "ERR slot switch failed"); return false; }

    char lmsg[128] = "";
    loadSlot(slot, lmsg, sizeof(lmsg));
    snprintf(g_status, sizeof(g_status), "cfg: slot %c gen %u, %d theme(s)%s",
             g_slot, (unsigned)g_generation, g_nThemes, lmsg);
    snprintf(msg, cap, "OK cfg slot %c gen %u, %d theme(s), %d skipped%s%s",
             g_slot, (unsigned)g_generation, g_nThemes, skipped, verdict, lmsg);
    mclog::tagInfo(kTag, "{}", msg);
    return true;
}

const char* cfgStatus()  { return g_status; }
int         cfgThemeCount() { ensureBegun(); return g_nThemes; }

const char* cfgThemeName(int i)
{
    return (i >= 0 && i < g_nThemes) ? g_theme[i].name : "?";
}

bool cfgThemeColour(int i, const char* stateName, uint32_t* rgb)
{
    if (i < 0 || i >= g_nThemes) return false;
    for (int k = 0; k < g_theme[i].n; ++k)
        if (strcmp(g_theme[i].sw[k].state, stateName) == 0) {
            if (rgb) *rgb = g_theme[i].sw[k].rgb;
            return true;
        }
    return false;
}

#else   // the twin

#include <cstdio>

// The desktop build has no HTTP client and no flash. Authoring on the twin is choreography 3.4
// work; this says so rather than pretending to store anything.
void cfgBegin() {}
bool cfgFetch(const char*, char* msg, size_t cap)
{
    snprintf(msg, cap, "ERR cfg fetch is head-only -- the twin has no flash to install into");
    return false;
}
void        cfgStart() {}
const char* cfgStatus()  { return "cfg: head-only"; }
int         cfgThemeCount() { return 0; }
const char* cfgThemeName(int)  { return "?"; }
bool        cfgThemeColour(int, const char*, uint32_t*) { return false; }

#endif
