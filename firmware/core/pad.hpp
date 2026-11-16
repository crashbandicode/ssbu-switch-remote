// switch-pad: the portable core of the ESP32-S3 wired-controller firmware (DESIGN.md section 13).
//
// The host talks to it with switch-remote's line protocol (docs/PROTOCOL.md, host channel) over the board's UART;
// it answers with the HID report a HORI POKKEN CONTROLLER (0F0D:0092) sends, which the Switch treats as a Pro
// Controller. Macros reuse the sysmodule core's sr::Macro: 60 Hz frames of this core's own clock (microseconds),
// so a scheduled change does not depend on the host's timing. A line must arrive at least every second or
// everything goes neutral (the host pings every 300 ms).
//
// Platform-free: the ESP-IDF main (firmware/main/) and the host simulator (firmware/host_sim/) feed it lines and
// microsecond timestamps. Host-tested in firmware/core/tests/.
#pragma once

#include <cstdint>
#include <string_view>

#include "../../sysmodule/core/core.hpp"

namespace swpad {

constexpr std::string_view kName = "switch-pad";
constexpr std::string_view kVersion = "0.1.1";
constexpr std::string_view kUsbIdentity = "hori-0f0d:0092";
constexpr uint64_t kFreq = 1000000;          // timestamps are microseconds
constexpr uint64_t kWatchdogUs = 1000000;    // 1 s of host silence -> neutral
constexpr int kHome = 18, kCapture = 19;     // Eden macro bits; only BTN may use them (the host's fixed sequences)
constexpr int kBits = 22;

// The 8-byte input report: buttons (LE16), hat, LX, LY, RX, RY, vendor.
struct Report {
    uint8_t b[8];
    bool operator==(const Report& o) const;
    bool operator!=(const Report& o) const { return !(*this == o); }
};

// Eden bit -> HORI button mask (0 when the bit has no HORI button: d-pad, SL/SR, stick-derived bits).
uint16_t hori_button(int eden_bit);
// D-pad bits (left 12, up 13, right 14, down 15) -> hat 0-7, or 8 when neutral; opposite directions cancel.
uint8_t hat(uint32_t eden_buttons);
// Console stick units (+-32767, +y = up) -> 0-255 with 0x80 at rest; HID y grows downwards.
uint8_t axis(int32_t v, bool invert);
Report make_report(uint32_t eden_buttons, int32_t lx, int32_t ly, int32_t rx, int32_t ry);
Report neutral_report();

class Pad {
public:
    // One host line (without LF). The reply, if any, goes to `out` (MEV success has none).
    void handle(std::string_view line, uint64_t now, sr::LineBuf<192>& out);
    // The report to send at `now`; applies leases, the macro and the watchdog.
    Report report(uint64_t now);

    void set_usb_mounted(bool m) { usb_mounted_ = m; }
    void count_report() { reports_++; }
    uint32_t watchdog_trips() const { return trips_; }
    const sr::Macro& macro() const { return macro_; }

private:
    void watchdog(uint64_t now);
    void reset_live();
    bool system_button(std::string_view line, uint64_t now, sr::LineBuf<192>& out);
    bool v0(std::string_view line, uint64_t now, sr::LineBuf<192>& out);  // press/stick/clear (human verbs)

    sr::Macro macro_;
    uint64_t button_deadline_[kBits] = {};
    uint64_t stick_deadline_[2] = {};
    int32_t stick_[2][2] = {};
    uint64_t last_rx_ = 0;
    bool rx_seen_ = false, silent_ = false, usb_mounted_ = false;
    uint32_t trips_ = 0;
    uint64_t reports_ = 0;
};

}  // namespace swpad
