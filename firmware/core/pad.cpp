#include "pad.hpp"

#include <cstring>

namespace swpad {

bool Report::operator==(const Report& o) const { return std::memcmp(b, o.b, sizeof b) == 0; }

// HORI button masks (GP2040-CE headers/drivers/switch/SwitchDescriptors.h, MIT; firmware/THIRD_PARTY.md).
namespace hori {
constexpr uint16_t Y = 1u << 0, B = 1u << 1, A = 1u << 2, X = 1u << 3, L = 1u << 4, R = 1u << 5, ZL = 1u << 6,
                   ZR = 1u << 7, Minus = 1u << 8, Plus = 1u << 9, L3 = 1u << 10, R3 = 1u << 11, Home = 1u << 12,
                   Capture = 1u << 13;
}

uint16_t hori_button(int bit) {
    // Eden order: a b x y lstick rstick l r zl zr plus minus left up right down sl_left sr_left home capture ...
    static const uint16_t map[kBits] = {hori::A,  hori::B,     hori::X,    hori::Y,    hori::L3, hori::R3,
                                        hori::L,  hori::R,     hori::ZL,   hori::ZR,   hori::Plus, hori::Minus,
                                        0,        0,           0,          0,          0,        0,
                                        hori::Home, hori::Capture, 0,      0};
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

void Pad::reset_live() {
    for (auto& d : button_deadline_) d = 0;
    stick_deadline_[0] = stick_deadline_[1] = 0;
}

void Pad::watchdog(uint64_t now) {
    if (!rx_seen_ || silent_ || now < last_rx_ || now - last_rx_ <= kWatchdogUs) return;
    silent_ = true;
    bool active = macro_.phase == sr::MacroPhase::Playing;
    for (int i = 0; i < kBits; i++) active |= button_deadline_[i] > now;
    active |= stick_deadline_[0] > now || stick_deadline_[1] > now;
    reset_live();
    if (macro_.phase == sr::MacroPhase::Playing) macro_.stop("watchdog");
    if (active) trips_++;
}

Report Pad::report(uint64_t now) {
    watchdog(now);
    if (macro_.phase == sr::MacroPhase::Playing) {
        sr::PadState p = macro_.tick(now, kFreq);  // the macro owns the pad while it plays
        if (macro_.phase == sr::MacroPhase::Playing)
            return make_report(uint32_t(p.buttons & sr::kInjectableMask), p.lx, p.ly, p.rx, p.ry);
        return neutral_report();
    }
    uint32_t buttons = 0;
    for (int i = 0; i < kBits; i++)
        if (button_deadline_[i] > now) buttons |= 1u << i;
    int32_t s[2][2] = {};
    for (int w = 0; w < 2; w++)
        if (stick_deadline_[w] > now) {
            s[w][0] = stick_[w][0];
            s[w][1] = stick_[w][1];
        }
    return make_report(buttons, s[0][0], s[0][1], s[1][0], s[1][1]);
}

static void reply_err(sr::LineBuf<192>& out, std::string_view code, std::string_view detail = {}) {
    out.put("ERR ");
    out.put(code);
    if (!detail.empty()) {
        out.put(" ");
        out.put(detail);
    }
}

// `BTN 18|19 press|release|tap <ms>`: HOME and CAPTURE, which sr::parse_request refuses (it guards the
// sysmodule's gated path). The wired pad has no gate (primary direction 2026-11-16); the host uses these
// only inside its still-checked LAUNCH / TERMINATE / DISMISS sequences.
bool Pad::system_button(std::string_view line, uint64_t now, sr::LineBuf<192>& out) {
    if (line.substr(0, 7) != "BTN 18 " && line.substr(0, 7) != "BTN 19 ") return false;
    int bit = line[5] == '8' ? kHome : kCapture;
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

void Pad::handle(std::string_view line, uint64_t now, sr::LineBuf<192>& out) {
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
        out.put(kUsbIdentity);
        return;
    }
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
            out.put(kUsbIdentity);
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
            return;
        }
        case sr::Method::Button:
            if (macro_.phase == sr::MacroPhase::Playing) return reply_err(out, "macro_busy");
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
            if (const char* e = macro_.add(r.sample)) reply_err(out, e);
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
