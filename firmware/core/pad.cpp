#include "pad.hpp"

#include <cstdlib>
#include <cstring>

namespace swpad {

bool Report::operator==(const Report& o) const { return std::memcmp(b, o.b, sizeof b) == 0; }
bool GcReport::operator==(const GcReport& o) const { return std::memcmp(b, o.b, sizeof b) == 0; }

std::string_view mode_name(Mode m) { return m == Mode::Gc ? "gc" : "pro"; }

// HORI button masks (GP2040-CE headers/drivers/switch/SwitchDescriptors.h, MIT; firmware/THIRD_PARTY.md).
namespace hori {
constexpr uint16_t Y = 1u << 0, B = 1u << 1, A = 1u << 2, X = 1u << 3, L = 1u << 4, R = 1u << 5, ZL = 1u << 6,
                   ZR = 1u << 7, Minus = 1u << 8, Plus = 1u << 9, L3 = 1u << 10, R3 = 1u << 11, Home = 1u << 12,
                   Capture = 1u << 13;
}

// GameCube adapter port bytes (docs/GC-MODE.md: SDL SDL_hidapi_gamecube.c, gc-adapter parsing.rs).
// Low byte = buttons1, high byte = buttons2.
namespace gc {
constexpr uint16_t A = 0x01, B = 0x02, X = 0x04, Y = 0x08, Left = 0x10, Right = 0x20, Down = 0x40, Up = 0x80,
                   Start = 0x100, Z = 0x200, R = 0x400, L = 0x800;
}

uint16_t hori_button(int bit) {
    // Eden order: a b x y lstick rstick l r zl zr plus minus left up right down sl_left sr_left home capture ...
    static const uint16_t map[kBits] = {hori::A,  hori::B,     hori::X,    hori::Y,    hori::L3, hori::R3,
                                        hori::L,  hori::R,     hori::ZL,   hori::ZR,   hori::Plus, hori::Minus,
                                        0,        0,           0,          0,          0,        0,
                                        hori::Home, hori::Capture, 0,      0};
    return bit >= 0 && bit < kBits ? map[bit] : 0;
}

uint16_t gc_button(int bit) {
    // CH-F004: a GameCube controller uses its L/R/Z in place of Switch ZL/ZR/R.
    static const uint16_t map[kBits] = {gc::A,    gc::B,  gc::X,     gc::Y,  0,       0,       0,     gc::Z,
                                        gc::L,    gc::R,  gc::Start, 0,      gc::Left, gc::Up, gc::Right, gc::Down,
                                        0,        0,      0,         0,      0,       0};
    return bit >= 0 && bit < kBits ? map[bit] : 0;
}

uint8_t hat(uint32_t b) {
    bool left = b >> 12 & 1, up = b >> 13 & 1, right = b >> 14 & 1, down = b >> 15 & 1;
    if (left && right) left = right = false;
    if (up && down) up = down = false;
    if (up) return right ? 1 : left ? 7 : 0;
    if (down) return right ? 3 : left ? 5 : 4;
    if (right) return 2;
    if (left) return 6;
    return 8;
}

uint8_t axis(int32_t v, bool invert) {
    if (v > sr::kStickMax) v = sr::kStickMax;
    if (v < -sr::kStickMax) v = -sr::kStickMax;
    if (invert) v = -v;
    // -32767 -> 1, 0 -> 128, 32767 -> 255 (rounded to nearest)
    int32_t s = v * 127;
    int32_t q = s >= 0 ? (s + sr::kStickMax / 2) / sr::kStickMax : -((-s + sr::kStickMax / 2) / sr::kStickMax);
    return uint8_t(128 + q);
}

Report make_report(uint32_t buttons, int32_t lx, int32_t ly, int32_t rx, int32_t ry) {
    uint16_t mask = 0;
    for (int i = 0; i < kBits; i++)
        if (buttons >> i & 1) mask |= hori_button(i);
    Report r{};
    r.b[0] = uint8_t(mask & 0xff);
    r.b[1] = uint8_t(mask >> 8);
    r.b[2] = hat(buttons);
    r.b[3] = axis(lx, false);
    r.b[4] = axis(ly, true);
    r.b[5] = axis(rx, false);
    r.b[6] = axis(ry, true);
    r.b[7] = 0;
    return r;
}

Report neutral_report() { return make_report(0, 0, 0, 0, 0); }

GcReport make_gc_report(const PadOut& s) {
    uint32_t b = s.buttons;
    // A physical d-pad cannot press opposite directions; cancel them as the HORI hat does.
    if ((b >> 12 & 1) && (b >> 14 & 1)) b &= ~((1u << 12) | (1u << 14));
    if ((b >> 13 & 1) && (b >> 15 & 1)) b &= ~((1u << 13) | (1u << 15));
    uint16_t mask = 0;
    for (int i = 0; i < kBits; i++)
        if (b >> i & 1) mask |= gc_button(i);
    GcReport r{};
    r.b[0] = kGcReportId;
    uint8_t* p = r.b + 1;  // port 1; ports 2-4 stay 00 (empty)
    p[0] = kGcWired;
    p[1] = uint8_t(mask & 0xff);
    p[2] = uint8_t(mask >> 8);
    p[3] = axis(s.lx, false);  // GameCube axes grow upwards: no inversion
    p[4] = axis(s.ly, false);
    p[5] = axis(s.rx, false);
    p[6] = axis(s.ry, false);
    p[7] = (mask & gc::L) ? 255 : s.lt;
    p[8] = (mask & gc::R) ? 255 : s.rt;
    return r;
}

GcReport neutral_gc_report() { return make_gc_report(PadOut{}); }

void reply_err(sr::LineBuf<256>& out, std::string_view code, std::string_view detail = {});

void Pad::reset_live() {
    for (auto& d : button_deadline_) d = 0;
    stick_deadline_[0] = stick_deadline_[1] = 0;
    trig_deadline_ = 0;
}

void Pad::watchdog(uint64_t now) {
    if (!rx_seen_ || silent_ || now < last_rx_ || now - last_rx_ <= kWatchdogUs) return;
    silent_ = true;
    bool active = macro_.phase == sr::MacroPhase::Playing;
    for (int i = 0; i < kBits; i++) active |= button_deadline_[i] > now;
    active |= stick_deadline_[0] > now || stick_deadline_[1] > now || trig_deadline_ > now;
    reset_live();
    if (macro_.phase == sr::MacroPhase::Playing) macro_.stop("watchdog");
    if (active) trips_++;
}

PadOut Pad::state(uint64_t now) {
    watchdog(now);
    PadOut o;
    if (macro_.phase == sr::MacroPhase::Playing) {
        sr::PadState p = macro_.tick(now, kFreq);  // the macro owns the pad while it plays
        if (macro_.phase != sr::MacroPhase::Playing) return o;
        o.buttons = uint32_t(p.buttons & sr::kInjectableMask);
        o.lx = p.lx;
        o.ly = p.ly;
        o.rx = p.rx;
        o.ry = p.ry;
        long i = sr::sample_at(macro_.samples.data(), macro_.loaded, macro_.hint, macro_.frame_at(now, kFreq));
        if (i >= 0) {
            o.lt = macro_trig_[i][0];
            o.rt = macro_trig_[i][1];
        }
        return o;
    }
    for (int i = 0; i < kBits; i++)
        if (button_deadline_[i] > now) o.buttons |= 1u << i;
    if (stick_deadline_[0] > now) {
        o.lx = stick_[0][0];
        o.ly = stick_[0][1];
    }
    if (stick_deadline_[1] > now) {
        o.rx = stick_[1][0];
        o.ry = stick_[1][1];
    }
    if (trig_deadline_ > now) {
        o.lt = trig_[0];
        o.rt = trig_[1];
    }
    return o;
}

void Pad::output_report(uint8_t report_id, const uint8_t* data, uint16_t len) {
    if (mode_ != Mode::Gc) return;  // Pro mode: rumble/LED output reports are ignored, as in 0.1.2
    if (report_id == 0) {
        if (len == 0) return;
        report_id = data[0];
        data++;
        len--;
    }
    if (report_id == kGcInit) gc_inits_++;
    if (report_id == kGcRumble && len >= 1) gc_rumble_ = data[0];
}

void reply_err(sr::LineBuf<256>& out, std::string_view code, std::string_view detail) {
    out.put("ERR ");
    out.put(code);
    if (!detail.empty()) {
        out.put(" ");
        out.put(detail);
    }
}

// In GC mode, live input that names a button the GameCube pad lacks is refused (macros drop such bits instead).
bool Pad::refuse_unmapped(uint32_t bits, sr::LineBuf<256>& out) const {
    if (mode_ != Mode::Gc) return false;
    for (int i = 0; i < kBits; i++)
        if ((bits >> i & 1) && gc_button(i) == 0) {
            reply_err(out, "no_gc_button", sr::kButtonNames[size_t(i)]);
            return true;
        }
    return false;
}

static std::string_view usb_identity(Mode m) { return m == Mode::Gc ? kGcUsbIdentity : kUsbIdentity; }

// --- v0 verbs (primary 2026-11-16 ~03:56Z: "press <buttons> <ms>, stick <x> <y> <ms>, clear, status") --------
// Human-typable on a terminal; they drive the same leases and watchdog as BTN/STICK/RESET.
//   press a+b 100       buttons by name, joined with '+': a b x y l r zl zr plus minus lstick rstick
//                       up down left right home capture (HOME/CAPTURE included: the operator's first test);
//                       GameCube names: z (= r), gc_l (= zl), gc_r (= zr), start / gc_start (= plus)
//   stick 0.5 -1 300    left stick, x y in -1..1 (+y = up)
//   cstick 0 1 300      right stick (the GameCube C-stick)
//   trig 1 0.5 300      analog triggers l r in 0..1 (GC mode)
//   mode [pro|gc]       as MODE
//   clear               everything neutral (also stops a macro)
//   status              as STATUS
static int name_bit(std::string_view n) {
    if (n == "dup") n = "up";
    if (n == "ddown") n = "down";
    if (n == "dleft") n = "left";
    if (n == "dright") n = "right";
    if (n == "start" || n == "gc_start") n = "plus";
    if (n == "select") n = "minus";
    if (n == "z") n = "r";
    if (n == "gc_l") n = "zl";
    if (n == "gc_r") n = "zr";
    int bit = sr::button_bit(n);
    if (bit < 0 || (bit >= 16 && bit != kHome && bit != kCapture)) return -1;
    return bit;
}

static bool parse_ms(std::string_view t, uint64_t& ms) {
    return sr::parse_u64(t, ms) && ms >= 1 && ms <= sr::kLeaseMaxMs;
}

static bool parse_float(std::string_view t, float lo, float hi, float& v) {
    char buf[24];
    if (t.empty() || t.size() >= sizeof buf) return false;
    std::memcpy(buf, t.data(), t.size());
    buf[t.size()] = 0;
    char* end = nullptr;
    v = std::strtof(buf, &end);
    return end == buf + t.size() && v >= lo && v <= hi;
}

static bool parse_unit(std::string_view t, int32_t& out) {
    float v = 0;
    if (!parse_float(t, -1.0f, 1.0f, v)) return false;
    out = int32_t(v * float(sr::kStickMax) + (v >= 0 ? 0.5f : -0.5f));
    return true;
}

static bool parse_trigger_unit(std::string_view t, uint8_t& out) {
    float v = 0;
    if (!parse_float(t, 0.0f, 1.0f, v)) return false;
    out = uint8_t(v * 255.0f + 0.5f);
    return true;
}

static bool parse_byte(std::string_view t, uint8_t& out) {
    uint64_t v = 0;
    if (!sr::parse_u64(t, v) || v > 255) return false;
    out = uint8_t(v);
    return true;
}

// Splits on single spaces into at most N fields; returns the count (N + 1 when there are more).
template <size_t N>
static size_t fields(std::string_view line, std::string_view (&f)[N]) {
    size_t n = 0;
    for (size_t i = 0; i < line.size();) {
        size_t j = line.find(' ', i);
        if (j == std::string_view::npos) j = line.size();
        if (j > i) {
            if (n == N) return N + 1;
            f[n++] = line.substr(i, j - i);
        }
        i = j + 1;
    }
    return n;
}

bool Pad::set_mode(std::string_view name, sr::LineBuf<256>& out) {
    Mode m;
    if (name == "pro") {
        m = Mode::Pro;
    } else if (name == "gc") {
        m = Mode::Gc;
    } else {
        reply_err(out, "bad_arguments", "MODE [pro|gc]");
        return true;
    }
    out.put("OK mode ");
    out.put(mode_name(m));
    if (m == mode_) {
        out.put(" unchanged");
        return true;
    }
    // Nothing held may survive into the other personality; the glue detaches, swaps and re-attaches.
    reset_live();
    if (macro_.phase == sr::MacroPhase::Playing) macro_.stop(nullptr);
    mode_ = m;
    mode_pending_ = true;
    mode_switches_++;
    usb_mounted_ = false;
    gc_rumble_ = 0;
    out.put(" switching");
    return true;
}

bool Pad::v0(std::string_view line, uint64_t now, sr::LineBuf<256>& out) {
    std::string_view f[5];
    size_t n = fields(line, f);
    if (n == 0) return false;
    if (n > 5) n = 5;  // the verbs below take at most 4 fields; more is bad_arguments
    if (f[0] == "status") return false;  // handled with STATUS below
    if (f[0] == "clear") {
        if (n != 1) return reply_err(out, "bad_arguments"), true;
        reset_live();
        if (macro_.phase == sr::MacroPhase::Playing) macro_.stop(nullptr);
        out.put("OK");
        return true;
    }
    if (f[0] == "mode") {
        if (n == 1) {
            out.put("OK mode ");
            out.put(mode_name(mode_));
            return true;
        }
        if (n != 2) return reply_err(out, "bad_arguments", "mode [pro|gc]"), true;
        return set_mode(f[1], out);
    }
    if (f[0] == "press") {
        uint64_t ms = 0;
        if (n != 3 || !parse_ms(f[2], ms)) return reply_err(out, "bad_arguments", "press <a+b...> <ms 1-10000>"), true;
        if (macro_.phase == sr::MacroPhase::Playing) return reply_err(out, "macro_busy"), true;
        uint32_t bits = 0;
        std::string_view names = f[1];
        while (!names.empty()) {
            size_t plus = names.find('+');
            std::string_view one = names.substr(0, plus);
            int bit = name_bit(one);
            if (bit < 0) return reply_err(out, "unknown_button", one), true;
            bits |= 1u << bit;
            names = plus == std::string_view::npos ? std::string_view{} : names.substr(plus + 1);
        }
        if (refuse_unmapped(bits, out)) return true;
        for (int i = 0; i < kBits; i++)
            if (bits >> i & 1) button_deadline_[i] = now + ms * 1000;
        out.put("OK");
        return true;
    }
    if (f[0] == "stick" || f[0] == "cstick") {
        int32_t x = 0, y = 0;
        uint64_t ms = 0;
        if (n != 4 || !parse_unit(f[1], x) || !parse_unit(f[2], y) || !parse_ms(f[3], ms))
            return reply_err(out, "bad_arguments", "stick <x -1..1> <y -1..1> <ms 1-10000>"), true;
        if (macro_.phase == sr::MacroPhase::Playing) return reply_err(out, "macro_busy"), true;
        int w = f[0] == "stick" ? 0 : 1;
        stick_[w][0] = x;
        stick_[w][1] = y;
        stick_deadline_[w] = now + ms * 1000;
        out.put("OK");
        return true;
    }
    if (f[0] == "trig") {
        uint8_t l = 0, r = 0;
        uint64_t ms = 0;
        if (n != 4 || !parse_trigger_unit(f[1], l) || !parse_trigger_unit(f[2], r) || !parse_ms(f[3], ms))
            return reply_err(out, "bad_arguments", "trig <l 0..1> <r 0..1> <ms 1-10000>"), true;
        if (mode_ != Mode::Gc) return reply_err(out, "wrong_mode", "trig needs mode gc"), true;
        if (macro_.phase == sr::MacroPhase::Playing) return reply_err(out, "macro_busy"), true;
        trig_[0] = l;
        trig_[1] = r;
        trig_deadline_ = now + ms * 1000;
        out.put("OK");
        return true;
    }
    return false;
}

// MODE, TRIG and MEV with analog triggers (9 fields): firmware-local lines that sr::parse_request does not know.
bool Pad::gc_line(std::string_view line, uint64_t now, sr::LineBuf<256>& out) {
    std::string_view f[9];
    size_t n = fields(line, f);
    if (n == 0) return false;
    if (f[0] == "MODE") {
        if (n == 1) {
            out.put("OK mode ");
            out.put(mode_name(mode_));
            return true;
        }
        if (n != 2) return reply_err(out, "bad_arguments", "MODE [pro|gc]"), true;
        return set_mode(f[1], out);
    }
    if (f[0] == "TRIG") {
        uint8_t l = 0, r = 0;
        uint64_t ms = 0;
        if (n != 4 || !parse_byte(f[1], l) || !parse_byte(f[2], r) || !parse_ms(f[3], ms))
            return reply_err(out, "bad_arguments", "TRIG <l 0-255> <r 0-255> <ms 1-10000>"), true;
        if (mode_ != Mode::Gc) return reply_err(out, "wrong_mode", "TRIG needs MODE gc"), true;
        if (macro_.phase == sr::MacroPhase::Playing) return reply_err(out, "macro_busy"), true;
        trig_[0] = l;
        trig_[1] = r;
        trig_deadline_ = now + ms * 1000;
        out.put("OK");
        return true;
    }
    if (f[0] == "MEV" && n == 9) {
        uint8_t lt = 0, rt = 0;
        if (!parse_byte(f[7], lt) || !parse_byte(f[8], rt)) return reply_err(out, "out_of_range"), true;
        // The first seven fields are a plain MEV for the shared parser.
        std::string_view head = line.substr(0, size_t(f[6].data() + f[6].size() - line.data()));
        sr::Request r;
        if (const char* e = sr::parse_request(head, r)) return reply_err(out, e), true;
        if (const char* e = macro_.add(r.sample)) return reply_err(out, e), true;
        macro_trig_[macro_.loaded - 1][0] = lt;
        macro_trig_[macro_.loaded - 1][1] = rt;
        return true;  // success: no reply
    }
    return false;
}

// `BTN 18|19 press|release|tap <ms>`: HOME and CAPTURE, which sr::parse_request refuses (it guards the
// sysmodule's gated path). The wired pad has no gate (primary direction 2026-11-16); the host uses these
// only inside its still-checked LAUNCH / TERMINATE / DISMISS sequences. A GameCube pad has neither.
bool Pad::system_button(std::string_view line, uint64_t now, sr::LineBuf<256>& out) {
    if (line.substr(0, 7) != "BTN 18 " && line.substr(0, 7) != "BTN 19 ") return false;
    int bit = line[5] == '8' ? kHome : kCapture;
    if (refuse_unmapped(1u << bit, out)) return true;
    std::string_view rest = line.substr(7);
    size_t sp = rest.find(' ');
    if (sp == std::string_view::npos) {
        reply_err(out, "bad_arguments");
        return true;
    }
    std::string_view state = rest.substr(0, sp);
    uint64_t ms = 0;
    if (!sr::parse_u64(rest.substr(sp + 1), ms) || ms < 1 || ms > sr::kLeaseMaxMs) {
        reply_err(out, "bad_arguments");
        return true;
    }
    if (state == "press" || state == "tap") {
        button_deadline_[bit] = now + ms * 1000;
    } else if (state == "release") {
        button_deadline_[bit] = 0;
    } else {
        reply_err(out, "bad_arguments");
        return true;
    }
    out.put("OK");
    return true;
}

void Pad::handle(std::string_view line, uint64_t now, sr::LineBuf<256>& out) {
    out.clear();
    last_rx_ = now;
    rx_seen_ = true;
    silent_ = false;
    if (!line.empty() && line.back() == '\r') line.remove_suffix(1);
    if (line.empty()) return;
    if (line == "VERSION") {
        out.put("OK version ");
        out.put(kName);
        out.put(" ");
        out.put(kVersion);
        out.put(" usb=");
        out.put(usb_identity(mode_));
        return;
    }
    if (gc_line(line, now, out)) return;
    if (v0(line, now, out)) return;
    if (line == "status") line = "STATUS";
    if (system_button(line, now, out)) return;
    sr::Request r;
    if (const char* e = sr::parse_request(line, r)) {
        reply_err(out, e);
        return;
    }
    switch (r.method) {
        case sr::Method::Auth: out.put("OK auth switch-pad"); return;  // the UART link is physical
        case sr::Method::Ping:
            out.put("OK pong ");
            out.put_u64(now / 1000);
            return;
        case sr::Method::Status: {
            if (macro_.phase == sr::MacroPhase::Playing) macro_.tick(now, kFreq);
            out.put("OK status fw=");
            out.put(kName);
            out.put("/");
            out.put(kVersion);
            out.put(" usb=");
            out.put(usb_identity(mode_));
            out.put(" usb_mounted=");
            out.put(usb_mounted_ ? "1" : "0");
            out.put(" uptime_ms=");
            out.put_u64(now / 1000);
            out.put(" reports=");
            out.put_u64(reports_);
            out.put(" watchdog_trips=");
            out.put_u64(trips_);
            out.put(" macro=");
            out.put(sr::phase_name(macro_.phase));
            out.put(" mode=");
            out.put(mode_name(mode_));
            if (mode_ == Mode::Gc) {
                out.put(" gc_init=");
                out.put_u64(gc_inits_);
                out.put(" gc_rumble=");
                out.put_u64(gc_rumble_);
            }
            out.put(" mounts=");
            out.put_u64(mounts_);
            return;
        }
        case sr::Method::Button:
            if (macro_.phase == sr::MacroPhase::Playing) return reply_err(out, "macro_busy");
            if (r.state != sr::BtnState::Release && refuse_unmapped(1u << r.bit, out)) return;
            if (r.state == sr::BtnState::Release)
                button_deadline_[r.bit] = 0;
            else
                button_deadline_[r.bit] = now + uint64_t(r.ms) * 1000;
            out.put("OK");
            return;
        case sr::Method::Stick:
            if (macro_.phase == sr::MacroPhase::Playing) return reply_err(out, "macro_busy");
            stick_[r.stick][0] = r.x;
            stick_[r.stick][1] = r.y;
            stick_deadline_[r.stick] = now + uint64_t(r.ms) * 1000;
            out.put("OK");
            return;
        case sr::Method::Reset:
            reset_live();
            if (macro_.phase == sr::MacroPhase::Playing) macro_.stop(nullptr);
            out.put("OK");
            return;
        case sr::Method::MacroBegin:
            if (const char* e = macro_.begin(r.n, r.duration)) return reply_err(out, e);
            out.put("OK");
            return;
        case sr::Method::MacroEvent:
            if (const char* e = macro_.add(r.sample)) return reply_err(out, e);
            macro_trig_[macro_.loaded - 1][0] = macro_trig_[macro_.loaded - 1][1] = 0;
            return;  // success: no reply
        case sr::Method::MacroPlay:
            if (const char* e = macro_.play(now)) return reply_err(out, e);
            reset_live();
            out.put("OK");
            return;
        case sr::Method::MacroStatus:
            if (macro_.phase == sr::MacroPhase::Playing) macro_.tick(now, kFreq);
            out.put("OK macro state=");
            out.put(sr::phase_name(macro_.phase));
            out.put(" frame=");
            out.put_u64(macro_.phase == sr::MacroPhase::Playing ? macro_.frame_at(now, kFreq) : 0);
            out.put(" duration=");
            out.put_u64(macro_.duration);
            out.put(" loaded=");
            out.put_u64(macro_.loaded);
            out.put(" error=");
            out.put(macro_.error ? macro_.error : "");
            return;
        case sr::Method::MacroStop:
            macro_.stop(nullptr);
            out.put("OK");
            return;
        case sr::Method::Bye:
            reset_live();
            if (macro_.phase == sr::MacroPhase::Playing) macro_.stop(nullptr);
            out.put("OK bye");
            return;
        default:
            // LAUNCH/TERMINATE/LIST/FILE/PUT/... are the sysmodule's; the host runs its own sequences.
            return reply_err(out, "unsupported", "switch-pad is a controller");
    }
}

}  // namespace swpad
