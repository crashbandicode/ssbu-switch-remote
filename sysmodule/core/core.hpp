// switch-remote sysmodule: pure logic (no libnx), host-tested by core/tests/test_core.cpp.
// Protocol contract: docs/PROTOCOL.md. Design: DESIGN.md.
#pragma once
#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <string_view>

namespace sr {

// ---------------------------------------------------------------------------------------------
// Buttons. An Eden macro's `buttons` is a 22-bit mask, bit i = ButtonNames()[i] (Eden
// src/yuzu/macro_file.cpp:323-331). Bits 0..15 equal HidNpadButton bits 0..15 (A B X Y StickL
// StickR L R ZL ZR Plus Minus Left Up Right Down), so they pass straight into the virtual
// controller. Bits 16..21 (SL/SR, home, capture) are refused.
// ---------------------------------------------------------------------------------------------
constexpr std::array<std::string_view, 22> kButtonNames = {
    "a", "b", "x", "y", "lstick", "rstick", "l", "r", "zl", "zr", "plus", "minus", "left", "up",
    "right", "down", "sl_left", "sr_left", "home", "capture", "sl_right", "sr_right"};
constexpr uint32_t kInjectableMask = 0xffff;
constexpr uint32_t kMacroMask = (1u << 22) - 1;
constexpr int kButtons = 16;
constexpr int32_t kStickMax = 32767;
constexpr uint32_t kLeaseMaxMs = 10000;
constexpr size_t kMacroSamples = 4096;

int button_bit(std::string_view name);  // -1 if unknown

// Ticks are abstract (the sysmodule uses armGetSystemTick, 19.2 MHz); freq = ticks per second.
uint64_t lease_deadline(uint64_t now, uint32_t ms, uint64_t freq);

struct Sample {
    uint32_t frame = 0;
    uint32_t buttons = 0;
    int16_t sticks[4] = {0, 0, 0, 0};  // lx ly rx ry
    bool operator==(const Sample& o) const {
        return frame == o.frame && buttons == o.buttons && std::memcmp(sticks, o.sticks, sizeof sticks) == 0;
    }
};

// Null on success, else an error code.
const char* check_sample(const Sample* previous, const Sample& s, uint32_t duration);
// Index of the sample in force at `frame`, searching from `hint`; -1 before the first sample.
long sample_at(const Sample* samples, size_t n, size_t hint, uint32_t frame);

// The controller state the sysmodule hands to the virtual device.
struct PadState {
    uint64_t buttons = 0;
    int32_t lx = 0, ly = 0, rx = 0, ry = 0;
    bool operator==(const PadState& o) const {
        return buttons == o.buttons && lx == o.lx && ly == o.ly && rx == o.rx && ry == o.ry;
    }
};

// Live input: one deadline per button and per stick, as Eden's per-input leases
// (CLIControl.md "Controller input": a new press replaces the old deadline).
struct Leases {
    uint64_t button[kButtons] = {};
    uint64_t stick_deadline[2] = {};
    int32_t stick[2][2] = {};
    void press(int bit, uint64_t deadline) { button[bit] = deadline; }
    void release(int bit) { button[bit] = 0; }
    void set_stick(int which, int32_t x, int32_t y, uint64_t deadline) {
        stick[which][0] = x;
        stick[which][1] = y;
        stick_deadline[which] = deadline;
    }
    void reset() { *this = Leases{}; }
    PadState state(uint64_t now) const;
};

// Macro playback. Frames advance at 60 Hz of wall time on the sysmodule's clock
// (DESIGN.md: hiddbg has no view of the game's frames; measured on hardware).
enum class MacroPhase : uint8_t { Idle, Loading, Ready, Playing, Finished, Error };
const char* phase_name(MacroPhase p);

struct Macro {
    std::array<Sample, kMacroSamples> samples{};
    size_t expected = 0, loaded = 0;
    uint32_t duration = 0;
    MacroPhase phase = MacroPhase::Idle;
    const char* error = nullptr;
    uint64_t start_tick = 0;
    size_t hint = 0;

    const char* begin(uint32_t n, uint32_t duration_frames);
    const char* add(const Sample& s);
    const char* play(uint64_t now);
    void stop(const char* why);
    // Frame number at `now` (60 Hz from start).
    uint32_t frame_at(uint64_t now, uint64_t freq) const;
    // The state to apply at `now`; advances to Finished after `duration` frames.
    PadState tick(uint64_t now, uint64_t freq);
};

// ---------------------------------------------------------------------------------------------
// Online gate. Injection is allowed only on a fresh offline report from the in-game NRO:
// fail closed (operator ruling 2026-11-15). The NRO reads the game's mode word main+0x53050f0
// (TrainingRooms rc15 runtime/lobby/game_mode.rs:44-55) and Online Deluxe's ONLINE bit.
// ---------------------------------------------------------------------------------------------
struct SceneReport {
    uint64_t seq = 0;
    uint32_t mode = 0;
    int deluxe_online = -1;  // -1 unknown (no Deluxe), 0 offline, 1 online
    bool guards_ok = false;  // the NRO's version and site guards passed
    uint64_t received_tick = 0;
    bool valid = false;
};

constexpr uint64_t kSceneMaxAgeMs = 500;

enum class Gate : uint8_t { Allowed, NoReport, Stale, Unguarded, OnlineMode, OnlineDeluxe };
const char* gate_name(Gate g);
// The sysmodule's own fixed sequences (LAUNCH: HOME then A; DISMISS: A) press buttons without a
// scene report. They run only while no application process exists, so no online game can receive
// them (DESIGN.md section 2, "the launch exception"). Null when allowed, else an error code.
const char* system_sequence_allowed(bool application_running, bool sequence_busy);

// One ns application record (ns:am2 IApplicationManagerInterface cmd 0 ListApplicationRecord; libnx
// NsApplicationRecord: application_id, last_event, last_updated).
struct AppRecord {
    uint64_t id = 0;
    uint8_t last_event = 0;
    uint64_t last_updated = 0;
};
// LAUNCH presses A on the HOME menu's first tile, so the target must be that tile (operator ruling
// 2026-11-15). Neither the record order nor the HOME rule is documented (switchbrew NS_services), so two
// signals must agree: the target is record 0 AND no other record has a LastUpdated as new or newer.
// Null when confirmed, else: records_unavailable, first_tile_not_target, first_tile_ambiguous.
const char* first_tile_check(const AppRecord* records, size_t n, uint64_t target);
constexpr uint64_t kSsbuProgramId = 0x01006A800016E000ull;
// Internet (NSO) online: the mode word's 0x07 family (Quickplay 0x07010101, Elite 0x07010400; Deluxe
// auto_policy.rs:183-196). Refused: the ban risk is there.
bool mode_is_online(uint32_t mode);
// Local wireless (LDN): the 0x0201 family (room 0x02010000; Deluxe auto_policy.rs:171-179). Allowed: no internet
// service is involved, and the game shares these paths with NSO online, so they are tested here (operator 2026-11-15).
bool mode_is_local_wireless(uint32_t mode);
Gate gate(const SceneReport& r, uint64_t now, uint64_t freq);

// ---------------------------------------------------------------------------------------------
// Config: sd:/ultimate/switch-remote/config.txt (shared with the NRO).
// ---------------------------------------------------------------------------------------------
constexpr size_t kTokenMin = 16, kTokenMax = 64;
constexpr uint16_t kDefaultPort = 47130;  // host-facing; the scene channel is port+1 on 127.0.0.1

enum class Peers : uint8_t { Private, Loopback, Net };
struct Config {
    char token[kTokenMax + 1] = {};
    size_t token_len = 0;
    uint16_t port = kDefaultPort;
    Peers peers = Peers::Private;
    uint32_t net = 0;
    uint8_t net_len = 0;
    bool inject = true;  // inject=off disables the virtual controller entirely
    std::string_view token_view() const { return {token, token_len}; }
};
// Null on success, else an error code.
const char* parse_config(std::string_view text, Config& out);
bool parse_ipv4(std::string_view s, uint32_t& out);
bool peer_allowed(const Config& c, uint32_t ip);
bool token_eq(std::string_view given, std::string_view expected);

// ---------------------------------------------------------------------------------------------
// Protocol: docs/PROTOCOL.md.
// ---------------------------------------------------------------------------------------------
constexpr size_t kLineMax = 256;
constexpr uint32_t kFileChunkMax = 64 * 1024;

enum class Method : uint8_t {
    Auth, Ping, Status, Button, Stick, Reset, MacroBegin, MacroEvent, MacroPlay, MacroStatus, MacroStop,
    Launch, Terminate, Relaunch, Dismiss, Detach, Records, List, File, Bye,
    Scene,  // scene channel only (from the in-game NRO)
};
enum class BtnState : uint8_t { Press, Release, Tap };

// Roots the log methods may read. Paths are relative to one of them; no "..".
enum class Root : uint8_t { CrashReports, FatalReports, FatalErrors, ErptReports, Ultimate, Skyline };
constexpr std::array<std::string_view, 6> kRootNames = {"crash_reports", "fatal_reports", "fatal_errors",
                                                       "erpt_reports",  "ultimate",      "skyline"};
// SD path of each root (AMS 1.12.2: creport, fatal, fusee and erpt write these; DESIGN.md section 4).
constexpr std::array<std::string_view, 6> kRootPaths = {
    "/atmosphere/crash_reports", "/atmosphere/fatal_reports", "/atmosphere/fatal_errors", "/atmosphere/erpt_reports",
    "/ultimate",                 "/atmosphere/contents/01006A800016E000/romfs/skyline"};

struct Request {
    Method method{};
    std::string_view text;  // AUTH token, LIST/FILE path
    uint32_t bit = 0, ms = 0, n = 0, duration = 0, max = 0;
    BtnState state{};
    int stick = 0;
    int32_t x = 0, y = 0;
    uint64_t offset = 0;
    Root root{};
    Sample sample{};
    SceneReport scene{};
};

// Null on success, else an error code. `line` excludes the LF; a trailing CR is ignored.
const char* parse_request(std::string_view line, Request& out);
bool path_ok(std::string_view p, bool allow_empty);
bool parse_u64(std::string_view s, uint64_t& v);
bool parse_i32(std::string_view s, int32_t& v);

// A fixed-capacity line writer (truncates).
template <size_t N>
struct LineBuf {
    char buf[N];
    size_t len = 0;
    void clear() { len = 0; }
    void put(std::string_view s) {
        size_t n = s.size() < N - len ? s.size() : N - len;
        std::memcpy(buf + len, s.data(), n);
        len += n;
    }
    void put_u64(uint64_t v) {
        char t[20];
        int i = 0;
        do {
            t[i++] = char('0' + v % 10);
            v /= 10;
        } while (v);
        while (i && len < N) buf[len++] = t[--i];
    }
    void put_hex(uint64_t v) {
        put("0x");
        char t[16];
        int i = 0;
        do {
            t[i++] = "0123456789abcdef"[v & 15];
            v >>= 4;
        } while (v);
        while (i && len < N) buf[len++] = t[--i];
    }
    std::string_view view() const { return {buf, len}; }
};

}  // namespace sr
