// switch-remote sysmodule: pure logic. See core.hpp.
#include "core.hpp"

#include <cstdio>

namespace sr {

int button_bit(std::string_view name) {
    if (name == "dleft") name = "left";
    else if (name == "dup") name = "up";
    else if (name == "dright") name = "right";
    else if (name == "ddown") name = "down";
    else if (name == "start") name = "plus";
    else if (name == "select") name = "minus";
    for (size_t i = 0; i < kButtonNames.size(); i++)
        if (kButtonNames[i] == name) return int(i);
    return -1;
}

uint64_t lease_deadline(uint64_t now, uint32_t ms, uint64_t freq) {
    if (ms < 1) ms = 1;
    if (ms > kLeaseMaxMs) ms = kLeaseMaxMs;
    uint64_t d = now + uint64_t(ms) * freq / 1000;
    return d ? d : 1;
}

const char* check_sample(const Sample* previous, const Sample& s, uint32_t duration) {
    if (s.buttons & ~kMacroMask) return "bits_outside_mask";
    if (s.buttons & ~kInjectableMask) return "system_button";
    if (previous && s.frame <= previous->frame) return "frames_not_ascending";
    if (s.frame >= duration) return "frame_past_duration";
    return nullptr;
}

long sample_at(const Sample* samples, size_t n, size_t hint, uint32_t frame) {
    if (n == 0 || samples[0].frame > frame) return -1;
    size_t i = hint < n ? hint : n - 1;
    if (samples[i].frame > frame) i = 0;
    while (i + 1 < n && samples[i + 1].frame <= frame) i++;
    return long(i);
}

// HidNpadButton bits 16..23: StickLLeft, StickLUp, StickLRight, StickLDown, then the right stick.
static uint64_t stick_bits(int32_t x, int32_t y, int shift) {
    const int32_t t = kStickMax / 2;
    uint64_t b = 0;
    if (x < -t) b |= 1;
    if (y > t) b |= 2;
    if (x > t) b |= 4;
    if (y < -t) b |= 8;
    return b << shift;
}

static void derive_stick_bits(PadState& p) {
    p.buttons &= ~(uint64_t(0xff) << 16);
    p.buttons |= stick_bits(p.lx, p.ly, 16) | stick_bits(p.rx, p.ry, 20);
}

PadState Leases::state(uint64_t now) const {
    PadState p;
    for (int i = 0; i < kButtons; i++)
        if (button[i] > now) p.buttons |= uint64_t(1) << i;
    if (stick_deadline[0] > now) {
        p.lx = stick[0][0];
        p.ly = stick[0][1];
    }
    if (stick_deadline[1] > now) {
        p.rx = stick[1][0];
        p.ry = stick[1][1];
    }
    derive_stick_bits(p);
    return p;
}

const char* phase_name(MacroPhase p) {
    switch (p) {
        case MacroPhase::Idle: return "idle";
        case MacroPhase::Loading: return "loading";
        case MacroPhase::Ready: return "ready";
        case MacroPhase::Playing: return "playing";
        case MacroPhase::Finished: return "finished";
        case MacroPhase::Error: return "error";
    }
    return "?";
}

const char* Macro::begin(uint32_t n, uint32_t duration_frames) {
    if (phase == MacroPhase::Playing) return "macro_busy";
    if (n == 0 || n > kMacroSamples) return "out_of_range";
    if (duration_frames == 0 || duration_frames > 216000) return "out_of_range";
    expected = n;
    loaded = 0;
    duration = duration_frames;
    phase = MacroPhase::Loading;
    error = nullptr;
    hint = 0;
    return nullptr;
}

const char* Macro::add(const Sample& s) {
    if (phase != MacroPhase::Loading) return "not_loading";
    if (loaded >= expected) return "too_many_samples";
    if (const char* e = check_sample(loaded ? &samples[loaded - 1] : nullptr, s, duration)) {
        stop(e);
        return e;
    }
    samples[loaded++] = s;
    if (loaded == expected) phase = MacroPhase::Ready;
    return nullptr;
}

const char* Macro::play(uint64_t now) {
    if (phase != MacroPhase::Ready) return phase == MacroPhase::Loading ? "macro_incomplete" : "no_macro";
    start_tick = now;
    hint = 0;
    phase = MacroPhase::Playing;
    return nullptr;
}

void Macro::stop(const char* why) {
    if (why) {
        phase = MacroPhase::Error;
        error = why;
    } else if (phase == MacroPhase::Playing || phase == MacroPhase::Loading || phase == MacroPhase::Ready) {
        phase = MacroPhase::Idle;
    }
}

uint32_t Macro::frame_at(uint64_t now, uint64_t freq) const {
    if (now <= start_tick) return 0;
    return uint32_t((now - start_tick) * 60 / freq);
}

PadState Macro::tick(uint64_t now, uint64_t freq) {
    PadState p;
    if (phase != MacroPhase::Playing) return p;
    uint32_t f = frame_at(now, freq);
    if (f >= duration) {
        phase = MacroPhase::Finished;
        return p;
    }
    long i = sample_at(samples.data(), loaded, hint, f);
    if (i < 0) return p;
    hint = size_t(i);
    const Sample& s = samples[size_t(i)];
    p.buttons = s.buttons & kInjectableMask;
    p.lx = s.sticks[0];
    p.ly = s.sticks[1];
    p.rx = s.sticks[2];
    p.ry = s.sticks[3];
    derive_stick_bits(p);
    return p;
}

const char* gate_name(Gate g) {
    switch (g) {
        case Gate::Allowed: return "allowed";
        case Gate::NoReport: return "no_scene_report";
        case Gate::Stale: return "scene_report_stale";
        case Gate::Unguarded: return "nro_guards_failed";
        case Gate::OnlineMode: return "online_mode";
        case Gate::OnlineDeluxe: return "online_deluxe";
    }
    return "?";
}

const char* first_tile_check(const AppRecord* records, size_t n, uint64_t target) {
    if (!records || n == 0) return "records_unavailable";
    if (records[0].id != target) return "first_tile_not_target";
    for (size_t i = 1; i < n; i++)
        if (records[i].last_updated >= records[0].last_updated) return "first_tile_ambiguous";
    return nullptr;
}

bool update_target(std::string_view key, char* out, size_t cap) {
    for (const auto& t : kUpdateTargets) {
        if (t.key == key) {
            snprintf(out, cap, "%.*s", int(t.path.size()), t.path.data());
            return true;
        }
    }
    if (key.substr(0, 5) == "data/") {
        std::string_view name = key.substr(5);
        if (name.empty() || name.size() > 64 || name == "backups" || name[0] == '.') return false;
        for (char ch : name)
            if (!((ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z') || (ch >= '0' && ch <= '9') || ch == '_' ||
                  ch == '-' || ch == '.'))
                return false;
        snprintf(out, cap, "%.*s/%.*s", int(kDataDir.size()), kDataDir.data(), int(name.size()), name.data());
        return true;
    }
    return false;
}

void backup_path(std::string_view target, const char* new_sha, char* out, size_t cap) {
    size_t slash = target.rfind('/');
    std::string_view name = slash == std::string_view::npos ? target : target.substr(slash + 1);
    snprintf(out, cap, "%.*s/pre-%.16s.%.*s", int(kBackupDir.size()), kBackupDir.data(), new_sha, int(name.size()),
             name.data());
}

bool boot_delay_needed(uint64_t uptime_ms) { return uptime_ms < kBootWindowMs; }

bool detach_due(bool attached, bool active_now, uint64_t now_ms, uint64_t last_active_ms) {
    return attached && !active_now && now_ms >= last_active_ms && now_ms - last_active_ms >= kIdleDetachMs;
}

bool is_stage_file(std::string_view name) {
    constexpr std::string_view suffix = ".sr-stage";
    return name.size() > suffix.size() && name.substr(name.size() - suffix.size()) == suffix;
}

uint64_t retry_delay_ms(unsigned attempt) {
    static constexpr uint64_t steps[] = {1000, 2000, 5000};
    return attempt == 0 ? 0 : attempt <= 3 ? steps[attempt - 1] : 10000;
}

bool log_attempt(unsigned attempt) { return attempt <= 5 || attempt % 6 == 0; }

void format_status(const BootStatus& s, char* out, size_t cap) {
    char ip[16] = "none";
    if (s.ip)
        snprintf(ip, sizeof ip, "%u.%u.%u.%u", unsigned(s.ip >> 24), unsigned((s.ip >> 16) & 255),
                 unsigned((s.ip >> 8) & 255), unsigned(s.ip & 255));
    snprintf(out, cap,
             "version=%.*s stage=%s ip=%s listening=%d port=%u uptime_ms=%llu socket_attempts=%u nifm_attempts=%u "
             "config=%s last_error=%s",
             int(kSysmoduleVersion.size()), kSysmoduleVersion.data(), s.stage, ip, int(s.listening), unsigned(s.port),
             (unsigned long long)s.uptime_ms, s.socket_attempts, s.nifm_attempts, s.config_ok ? "ok" : "missing",
             s.last_error);
}

const char* system_sequence_allowed(bool application_running, bool sequence_busy) {
    if (application_running) return "application_running";
    if (sequence_busy) return "sequence_busy";
    return nullptr;
}

// The game's mode word: TrainingRooms treats `mode & 0x7fff0000 == 0x07010000` as the online family
// (rc15 src/model/lobby_flag.rs:59-63). This refuses the wider 0x07xxxxxx family.
bool mode_is_online(uint32_t mode) { return (mode & 0x7f000000u) == 0x07000000u; }
bool mode_is_local_wireless(uint32_t mode) { return (mode & 0x7fff0000u) == 0x02010000u; }

Gate gate(const SceneReport& r, uint64_t now, uint64_t freq) {
    if (!r.valid) return Gate::NoReport;
    if (now < r.received_tick || (now - r.received_tick) * 1000 / freq > kSceneMaxAgeMs) return Gate::Stale;
    if (!r.guards_ok) return Gate::Unguarded;
    if (mode_is_online(r.mode)) return Gate::OnlineMode;
    // Deluxe's ONLINE bit also covers local wireless (deluxe-plugin-sdk abi lib.rs:112-114), so it refuses only
    // when the mode word does not name local wireless: where the two can't be told apart, fail closed.
    if (r.deluxe_online == 1 && !mode_is_local_wireless(r.mode)) return Gate::OnlineDeluxe;
    return Gate::Allowed;
}

// --- config ----------------------------------------------------------------------------------
static std::string_view trim(std::string_view s) {
    while (!s.empty() && (s.front() == ' ' || s.front() == '\t' || s.front() == '\r')) s.remove_prefix(1);
    while (!s.empty() && (s.back() == ' ' || s.back() == '\t' || s.back() == '\r')) s.remove_suffix(1);
    return s;
}

bool parse_u64(std::string_view s, uint64_t& v) {
    if (s.empty() || s.size() > 20) return false;
    uint64_t r = 0;
    for (char c : s) {
        if (c < '0' || c > '9') return false;
        uint64_t d = uint64_t(c - '0');
        if (r > (UINT64_MAX - d) / 10) return false;
        r = r * 10 + d;
    }
    v = r;
    return true;
}

bool parse_i32(std::string_view s, int32_t& v) {
    bool neg = !s.empty() && s[0] == '-';
    if (neg) s.remove_prefix(1);
    uint64_t u;
    if (!parse_u64(s, u) || u > 2147483648ull || (!neg && u > 2147483647ull)) return false;
    v = neg ? int32_t(-int64_t(u)) : int32_t(u);
    return true;
}

static bool parse_u32(std::string_view s, uint32_t& v) {
    uint64_t u;
    if (!parse_u64(s, u) || u > UINT32_MAX) return false;
    v = uint32_t(u);
    return true;
}

bool parse_ipv4(std::string_view s, uint32_t& out) {
    uint32_t r = 0;
    int parts = 0;
    while (true) {
        size_t dot = s.find('.');
        std::string_view part = s.substr(0, dot);
        uint64_t v;
        if (part.empty() || part.size() > 3 || !parse_u64(part, v) || v > 255) return false;
        r = (r << 8) | uint32_t(v);
        parts++;
        if (dot == std::string_view::npos) break;
        s.remove_prefix(dot + 1);
    }
    if (parts != 4) return false;
    out = r;
    return true;
}

static bool in_net(uint32_t ip, uint32_t net, uint8_t len) {
    uint32_t mask = len == 0 ? 0 : 0xffffffffu << (32 - len);
    return (ip & mask) == (net & mask);
}

static bool is_private(uint32_t ip) {
    return in_net(ip, 0x7f000000, 8) || in_net(ip, 0x0a000000, 8) || in_net(ip, 0xac100000, 12) ||
           in_net(ip, 0xc0a80000, 16) || in_net(ip, 0xa9fe0000, 16);
}

bool peer_allowed(const Config& c, uint32_t ip) {
    bool loopback = in_net(ip, 0x7f000000, 8);
    switch (c.peers) {
        case Peers::Loopback: return loopback;
        case Peers::Net: return loopback || in_net(ip, c.net, c.net_len);
        case Peers::Private: return is_private(ip);
    }
    return false;
}

const char* parse_config(std::string_view text, Config& c) {
    c = Config{};
    while (!text.empty()) {
        size_t nl = text.find('\n');
        std::string_view line = trim(text.substr(0, nl));
        text = nl == std::string_view::npos ? std::string_view{} : text.substr(nl + 1);
        if (line.empty() || line[0] == '#') continue;
        size_t eq = line.find('=');
        if (eq == std::string_view::npos) return "unknown_key";
        std::string_view key = trim(line.substr(0, eq)), value = trim(line.substr(eq + 1));
        if (key == "token") {
            if (value.size() < kTokenMin || value.size() > kTokenMax) return "bad_token";
            for (char ch : value)
                if (!((ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z') || (ch >= '0' && ch <= '9') || ch == '_' ||
                      ch == '-'))
                    return "bad_token";
            std::memcpy(c.token, value.data(), value.size());
            c.token[value.size()] = 0;
            c.token_len = value.size();
        } else if (key == "port") {
            uint32_t p;
            if (!parse_u32(value, p) || p < 1024 || p > 65534) return "bad_port";
            c.port = uint16_t(p);
        } else if (key == "peers") {
            if (value == "private") c.peers = Peers::Private;
            else if (value == "loopback") c.peers = Peers::Loopback;
            else {
                size_t slash = value.find('/');
                uint32_t net, len;
                if (slash == std::string_view::npos || !parse_ipv4(value.substr(0, slash), net) ||
                    !parse_u32(value.substr(slash + 1), len) || len > 32 || len < 8 || !is_private(net))
                    return "bad_peers";
                c.peers = Peers::Net;
                c.net = net;
                c.net_len = uint8_t(len);
            }
        } else if (key == "inject") {
            if (value == "on") c.inject = true;
            else if (value == "off") c.inject = false;
            else return "bad_inject";
        } else if (key == "bind" || key == "npad" || key == "scene_host") {
            // NRO keys (in-game fallback input path; scene_host = where the NRO reports, option d); ignored here,
            // so the parked sysmodule still starts with an option-(d) config.txt.
        } else {
            return "unknown_key";
        }
    }
    if (!c.token_len) return "no_token";
    return nullptr;
}

bool token_eq(std::string_view given, std::string_view expected) {
    if (expected.empty()) return false;
    uint8_t diff = given.size() != expected.size();
    for (size_t i = 0; i < expected.size(); i++) diff |= uint8_t((i < given.size() ? given[i] : 0) ^ expected[i]);
    return diff == 0;
}

// --- protocol --------------------------------------------------------------------------------
bool path_ok(std::string_view p, bool allow_empty) {
    if (p.empty()) return allow_empty;
    if (p.size() > 160 || p[0] == '/') return false;
    for (char ch : p)
        if (ch <= ' ' || ch > '~' || ch == '\\') return false;
    while (true) {
        size_t s = p.find('/');
        std::string_view part = p.substr(0, s);
        if (part.empty() || part == "." || part == "..") return false;
        if (s == std::string_view::npos) break;
        p.remove_prefix(s + 1);
    }
    return true;
}

static bool parse_root(std::string_view s, Root& r) {
    for (size_t i = 0; i < kRootNames.size(); i++)
        if (kRootNames[i] == s) {
            r = Root(i);
            return true;
        }
    return false;
}

static bool parse_lease(std::string_view s, uint32_t& ms, const char*& err) {
    if (!parse_u32(s, ms)) {
        err = "bad_arguments";
        return false;
    }
    if (ms == 0 || ms > kLeaseMaxMs) {
        err = "out_of_range";
        return false;
    }
    return true;
}

static bool parse_stick(std::string_view s, int32_t& v, const char*& err) {
    if (!parse_i32(s, v)) {
        err = "bad_arguments";
        return false;
    }
    if (v > kStickMax || v < -kStickMax) {
        err = "out_of_range";
        return false;
    }
    return true;
}

const char* parse_request(std::string_view line, Request& r) {
    if (!line.empty() && line.back() == '\r') line.remove_suffix(1);
    if (line.size() >= kLineMax) return "too_long";
    std::string_view f[9];
    size_t n = 0;
    while (!line.empty()) {
        while (!line.empty() && line.front() == ' ') line.remove_prefix(1);
        if (line.empty()) break;
        size_t sp = line.find(' ');
        if (n == 9) return "bad_arguments";
        f[n++] = line.substr(0, sp);
        line = sp == std::string_view::npos ? std::string_view{} : line.substr(sp);
    }
    if (n == 0) return "empty";
    r = Request{};
    auto want = [&](size_t count) { return n == count; };
    const char* err = "bad_arguments";
    std::string_view m = f[0];
    if (m == "AUTH") {
        if (!want(2)) return err;
        r.method = Method::Auth;
        r.text = f[1];
    } else if (m == "PING") {
        if (!want(1)) return err;
        r.method = Method::Ping;
    } else if (m == "STATUS") {
        if (!want(1)) return err;
        r.method = Method::Status;
    } else if (m == "BTN") {
        if (!want(4) || !parse_u32(f[1], r.bit)) return err;
        if (r.bit >= uint32_t(kButtons)) return "out_of_range";
        if (f[2] == "press") r.state = BtnState::Press;
        else if (f[2] == "release") r.state = BtnState::Release;
        else if (f[2] == "tap") r.state = BtnState::Tap;
        else return err;
        if (!parse_lease(f[3], r.ms, err)) return err;
        r.method = Method::Button;
    } else if (m == "STICK") {
        if (!want(5)) return err;
        if (f[1] == "l") r.stick = 0;
        else if (f[1] == "r") r.stick = 1;
        else return err;
        if (!parse_stick(f[2], r.x, err) || !parse_stick(f[3], r.y, err) || !parse_lease(f[4], r.ms, err)) return err;
        r.method = Method::Stick;
    } else if (m == "RESET") {
        if (!want(1)) return err;
        r.method = Method::Reset;
    } else if (m == "MBEGIN") {
        if (!want(3) || !parse_u32(f[1], r.n) || !parse_u32(f[2], r.duration)) return err;
        if (r.n == 0 || r.n > kMacroSamples || r.duration == 0 || r.duration > 216000) return "out_of_range";
        r.method = Method::MacroBegin;
    } else if (m == "MEV") {
        if (!want(7) || !parse_u32(f[1], r.sample.frame) || !parse_u32(f[2], r.sample.buttons)) return err;
        for (int i = 0; i < 4; i++) {
            int32_t v;
            if (!parse_stick(f[3 + i], v, err)) return err;
            r.sample.sticks[i] = int16_t(v);
        }
        r.method = Method::MacroEvent;
    } else if (m == "MPLAY") {
        if (!want(1)) return err;
        r.method = Method::MacroPlay;
    } else if (m == "MSTATUS") {
        if (!want(1)) return err;
        r.method = Method::MacroStatus;
    } else if (m == "MSTOP") {
        if (!want(1)) return err;
        r.method = Method::MacroStop;
    } else if (m == "LAUNCH") {
        if (!want(1)) return err;
        r.method = Method::Launch;
    } else if (m == "TERMINATE") {
        if (!want(1)) return err;
        r.method = Method::Terminate;
    } else if (m == "RELAUNCH") {
        if (!want(1)) return err;
        r.method = Method::Relaunch;
    } else if (m == "PUT") {
        // PUT <key> <size> <sha256 hex>, then exactly <size> raw bytes.
        if (!want(4) || !parse_u64(f[2], r.size)) return err;
        if (r.size == 0 || r.size > kPutMax) return "out_of_range";
        char path[256];
        if (!update_target(f[1], path, sizeof path)) return "not_updatable";
        if (f[3].size() != 64) return err;
        for (size_t i = 0; i < 64; i++) {
            char ch = f[3][i];
            if (!((ch >= '0' && ch <= '9') || (ch >= 'a' && ch <= 'f'))) return err;
            r.sha[i] = ch;
        }
        r.sha[64] = 0;
        r.key = f[1];
        r.method = Method::Put;
    } else if (m == "RESTART") {
        if (!want(1)) return err;
        r.method = Method::Restart;
    } else if (m == "SHUTDOWN") {
        if (!want(1)) return err;
        r.method = Method::Shutdown;
    } else if (m == "RECORDS") {
        if (!want(1)) return err;
        r.method = Method::Records;
    } else if (m == "DETACH") {
        if (!want(1)) return err;
        r.method = Method::Detach;
    } else if (m == "DISMISS") {
        if (!want(1)) return err;
        r.method = Method::Dismiss;
    } else if (m == "LIST") {
        if (n != 2 && n != 3) return err;
        if (!parse_root(f[1], r.root)) return "bad_root";
        r.text = n == 3 ? f[2] : std::string_view{};
        if (!path_ok(r.text, true)) return "bad_path";
        r.method = Method::List;
    } else if (m == "FILE") {
        if (!want(5)) return err;
        if (!parse_root(f[1], r.root)) return "bad_root";
        if (!parse_u64(f[2], r.offset) || !parse_u32(f[3], r.max)) return err;
        if (r.max == 0 || r.max > kFileChunkMax) return "out_of_range";
        if (!path_ok(f[4], false)) return "bad_path";
        r.text = f[4];
        r.method = Method::File;
    } else if (m == "BYE") {
        if (!want(1)) return err;
        r.method = Method::Bye;
    } else if (m == "SCENE") {
        // SCENE <seq> <mode hex> <deluxe -1|0|1> <guards 0|1>
        if (!want(5) || !parse_u64(f[1], r.scene.seq)) return err;
        std::string_view h = f[2];
        if (h.size() < 3 || h.size() > 10 || h[0] != '0' || h[1] != 'x') return err;
        uint32_t mode = 0;
        for (char ch : h.substr(2)) {
            int d = (ch >= '0' && ch <= '9') ? ch - '0' : (ch >= 'a' && ch <= 'f') ? ch - 'a' + 10 : -1;
            if (d < 0) return err;
            mode = (mode << 4) | uint32_t(d);
        }
        r.scene.mode = mode;
        int32_t dx;
        if (!parse_i32(f[3], dx) || dx < -1 || dx > 1) return err;
        r.scene.deluxe_online = dx;
        if (f[4] != "0" && f[4] != "1") return err;
        r.scene.guards_ok = f[4] == "1";
        r.scene.valid = true;
        r.method = Method::Scene;
    } else {
        return "unknown_method";
    }
    return nullptr;
}

}  // namespace sr
