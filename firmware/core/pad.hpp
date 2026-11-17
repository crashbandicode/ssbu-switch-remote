// switch-pad: the portable core of the ESP32-S3 wired-controller firmware (DESIGN.md section 13).
//
// The host talks to it with switch-remote's line protocol (docs/PROTOCOL.md, host channel) over the board's UART.
// It answers with the HID report of one of two USB personalities (docs/GC-MODE.md):
//   Pro mode (default at power-up): a HORI POKKEN CONTROLLER (0F0D:0092), which the Switch treats as a Pro Controller;
//   GC mode: a GameCube Controller Adapter (057E:0337) with our pad on port 1.
// `MODE gc|pro` switches at runtime; the ESP-IDF glue performs the USB detach / descriptor swap / re-attach.
// Macros reuse the sysmodule core's sr::Macro: 60 Hz frames of this core's own clock (microseconds), so a scheduled
// change does not depend on the host's timing. A line must arrive at least every second or everything goes neutral
// (the host pings every 300 ms).
//
// Platform-free: the ESP-IDF main (firmware/main/) and the host simulator (firmware/host_sim/) feed it lines and
// microsecond timestamps. Host-tested in firmware/core/tests/.
#pragma once

#include <cstdint>
#include <string_view>

#include "../../sysmodule/core/core.hpp"

namespace swpad {

constexpr std::string_view kName = "switch-pad";
constexpr std::string_view kVersion = "0.2.0";
constexpr std::string_view kUsbIdentity = "hori-0f0d:0092";
constexpr std::string_view kGcUsbIdentity = "gc-adapter-057e:0337";
constexpr uint64_t kFreq = 1000000;          // timestamps are microseconds
constexpr uint64_t kWatchdogUs = 1000000;    // 1 s of host silence -> neutral
constexpr int kHome = 18, kCapture = 19;     // Eden macro bits; only BTN may use them (the host's fixed sequences)
constexpr int kBits = 22;

enum class Mode : uint8_t { Pro, Gc };
std::string_view mode_name(Mode m);

// The 8-byte input report: buttons (LE16), hat, LX, LY, RX, RY, vendor.
struct Report {
    uint8_t b[8];
    bool operator==(const Report& o) const;
    bool operator!=(const Report& o) const { return !(*this == o); }
};

// The GameCube adapter's 37-byte input report: 0x21, then four 9-byte ports
// (status, buttons1, buttons2, stick X, stick Y, C X, C Y, L analog, R analog). Port 1 is ours.
constexpr uint8_t kGcReportId = 0x21, kGcInit = 0x13, kGcRumble = 0x11;
constexpr uint8_t kGcWired = 0x10;  // port status: a wired controller (no rumble power)
struct GcReport {
    uint8_t b[37];
    bool operator==(const GcReport& o) const;
    bool operator!=(const GcReport& o) const { return !(*this == o); }
};

// The controller state both reports are built from.
struct PadOut {
    uint32_t buttons = 0;  // Eden bits
    int32_t lx = 0, ly = 0, rx = 0, ry = 0;
    uint8_t lt = 0, rt = 0;  // analog triggers (GC mode); a held zl/zr reads 255
};

// Eden bit -> HORI button mask (0 when the bit has no HORI button: d-pad, SL/SR, stick-derived bits).
uint16_t hori_button(int eden_bit);
// D-pad bits (left 12, up 13, right 14, down 15) -> hat 0-7, or 8 when neutral; opposite directions cancel.
uint8_t hat(uint32_t eden_buttons);
// Console stick units (+-32767, +y = up) -> 0-255 with 0x80 at rest; HID y grows downwards.
uint8_t axis(int32_t v, bool invert);
Report make_report(uint32_t eden_buttons, int32_t lx, int32_t ly, int32_t rx, int32_t ry);
Report neutral_report();

// Eden bit -> GameCube (buttons1 | buttons2 << 8) mask; 0 when the GameCube pad has no such button
// (l, minus, stick clicks, home, capture, SL/SR). Mapping = CH-F004: zl -> L, zr -> R, r -> Z, plus -> Start.
uint16_t gc_button(int eden_bit);
GcReport make_gc_report(const PadOut& s);
GcReport neutral_gc_report();

class Pad {
public:
    // One host line (without LF). The reply, if any, goes to `out` (MEV success has none).
    void handle(std::string_view line, uint64_t now, sr::LineBuf<192>& out);
    // The state at `now`; applies leases, the macro and the watchdog.
    PadOut state(uint64_t now);
    // The Pro-mode report at `now` (byte-identical to 0.1.2).
    Report report(uint64_t now) { return to_report(state(now)); }
    GcReport gc_report(uint64_t now) { return make_gc_report(state(now)); }
    static Report to_report(const PadOut& s) { return make_report(s.buttons, s.lx, s.ly, s.rx, s.ry); }

    Mode mode() const { return mode_; }
    // The glue polls this: true once per accepted MODE change, with the mode to present next.
    bool take_mode_change(Mode& next) {
        if (!mode_pending_) return false;
        mode_pending_ = false;
        next = mode_;
        return true;
    }
    // An output report from the console (GC mode: 0x13 init, 0x11 rumble; ignored in Pro mode).
    // report_id 0 means the ID is the first byte of `data`.
    void output_report(uint8_t report_id, const uint8_t* data, uint16_t len);

    void set_usb_mounted(bool m) { usb_mounted_ = m; }
    void count_report() { reports_++; }
    uint32_t watchdog_trips() const { return trips_; }
    uint32_t gc_inits() const { return gc_inits_; }
    const sr::Macro& macro() const { return macro_; }

private:
    void watchdog(uint64_t now);
    void reset_live();
    bool system_button(std::string_view line, uint64_t now, sr::LineBuf<192>& out);
    bool v0(std::string_view line, uint64_t now, sr::LineBuf<192>& out);  // press/stick/trig/mode/clear (human verbs)
    bool gc_line(std::string_view line, uint64_t now, sr::LineBuf<192>& out);  // MODE, TRIG, 8-field MEV
    bool set_mode(std::string_view name, sr::LineBuf<192>& out);
    bool refuse_unmapped(uint32_t bits, sr::LineBuf<192>& out) const;

    sr::Macro macro_;
    uint8_t macro_trig_[sr::kMacroSamples][2] = {};  // analog triggers per macro sample (MEV's optional lt rt)
    uint64_t button_deadline_[kBits] = {};
    uint64_t stick_deadline_[2] = {};
    int32_t stick_[2][2] = {};
    uint64_t trig_deadline_ = 0;
    uint8_t trig_[2] = {};
    uint64_t last_rx_ = 0;
    bool rx_seen_ = false, silent_ = false, usb_mounted_ = false;
    Mode mode_ = Mode::Pro;
    bool mode_pending_ = false;
    uint32_t trips_ = 0, gc_inits_ = 0, mode_switches_ = 0;
    uint8_t gc_rumble_ = 0;
    uint64_t reports_ = 0;
};

}  // namespace swpad
