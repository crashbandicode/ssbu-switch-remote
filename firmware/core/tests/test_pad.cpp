// Host tests for firmware/core (switch-pad). Build and run: make -C firmware test
#include <cstdio>
#include <string>

#include "pad.hpp"

using namespace swpad;

static int failures = 0, checks = 0;
#define CHECK(cond)                                                          \
    do {                                                                     \
        checks++;                                                            \
        if (!(cond)) {                                                       \
            failures++;                                                      \
            std::printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);      \
        }                                                                    \
    } while (0)

static std::string say(Pad& p, std::string_view line, uint64_t now) {
    sr::LineBuf<192> out;
    p.handle(line, now, out);
    return std::string(out.view());
}
static constexpr uint64_t MS = 1000;
static Report rep(std::initializer_list<int> v) {
    Report r{};
    int i = 0;
    for (int x : v) r.b[i++] = uint8_t(x);
    return r;
}

static void report_layout_matches_the_hori_descriptor() {
    CHECK(neutral_report() == rep({0, 0, 8, 128, 128, 128, 128, 0}));
    // A -> bit 2, B -> 1, X -> 3, Y -> 0, L R ZL ZR -> 4-7, minus 8, plus 9, L3 10, R3 11, HOME 12, CAPTURE 13
    CHECK(make_report(1u << 0, 0, 0, 0, 0).b[0] == 0x04);
    CHECK(make_report(1u << 1, 0, 0, 0, 0).b[0] == 0x02);
    CHECK(make_report(1u << 2, 0, 0, 0, 0).b[0] == 0x08);
    CHECK(make_report(1u << 3, 0, 0, 0, 0).b[0] == 0x01);
    CHECK(make_report((1u << 6) | (1u << 7) | (1u << 8) | (1u << 9), 0, 0, 0, 0).b[0] == 0xf0);
    CHECK(make_report(1u << 11, 0, 0, 0, 0).b[1] == 0x01);  // minus
    CHECK(make_report(1u << 10, 0, 0, 0, 0).b[1] == 0x02);  // plus
    CHECK(make_report((1u << 4) | (1u << 5), 0, 0, 0, 0).b[1] == 0x0c);
    CHECK(make_report(1u << kHome, 0, 0, 0, 0).b[1] == 0x10);
    CHECK(make_report(1u << kCapture, 0, 0, 0, 0).b[1] == 0x20);
    CHECK(make_report((1u << 16) | (1u << 17) | (1u << 20) | (1u << 21), 0, 0, 0, 0) == neutral_report());
}

static void dpad_becomes_the_hat() {
    const uint32_t L = 1u << 12, U = 1u << 13, R = 1u << 14, D = 1u << 15;
    CHECK(hat(0) == 8);
    CHECK(hat(U) == 0 && hat(U | R) == 1 && hat(R) == 2 && hat(D | R) == 3);
    CHECK(hat(D) == 4 && hat(D | L) == 5 && hat(L) == 6 && hat(U | L) == 7);
    CHECK(hat(L | R) == 8 && hat(U | D) == 8 && hat(U | D | R) == 2);
    CHECK(make_report(U | (1u << 0), 0, 0, 0, 0).b[1] == 0 && make_report(U, 0, 0, 0, 0).b[2] == 0);
}

static void sticks_scale_and_y_points_down() {
    CHECK(axis(0, false) == 128 && axis(0, true) == 128);
    CHECK(axis(32767, false) == 255 && axis(-32767, false) == 1);
    CHECK(axis(32767, true) == 1);    // stick up (+y) -> HID 1 (top)
    CHECK(axis(-32767, true) == 255);
    CHECK(axis(40000, false) == 255 && axis(-40000, false) == 1);
    CHECK(axis(16384, false) == 192);
    Report r = make_report(0, 32767, 32767, -32767, 0);
    CHECK(r.b[3] == 255 && r.b[4] == 1 && r.b[5] == 1 && r.b[6] == 128);
}

static void leases_and_protocol() {
    Pad p;
    CHECK(say(p, "AUTH anything", 0) == "OK auth switch-pad");
    CHECK(say(p, "VERSION", 0) == "OK version switch-pad 0.1.2 usb=hori-0f0d:0092");
    CHECK(say(p, "PING", 5 * MS) == "OK pong 5");
    CHECK(say(p, "BTN 0 press 100", 10 * MS) == "OK");
    CHECK(p.report(50 * MS).b[0] == 0x04);
    CHECK(p.report(110 * MS) == neutral_report());   // lease ran out
    CHECK(say(p, "BTN 0 tap 50", 200 * MS) == "OK");
    CHECK(p.report(249 * MS).b[0] == 0x04 && p.report(251 * MS) == neutral_report());
    CHECK(say(p, "BTN 8 press 1000", 300 * MS) == "OK");
    CHECK(say(p, "BTN 8 release 1", 310 * MS) == "OK");
    CHECK(p.report(320 * MS) == neutral_report());
    CHECK(say(p, "STICK l 32767 0 200", 400 * MS) == "OK");
    CHECK(p.report(500 * MS).b[3] == 255 && p.report(601 * MS).b[3] == 128);
    CHECK(say(p, "BTN 18 tap 100", 700 * MS) == "OK");
    CHECK(p.report(750 * MS).b[1] == 0x10);
    CHECK(say(p, "BTN 19 press 100", 900 * MS) == "OK" && p.report(950 * MS).b[1] == 0x20);
    CHECK(say(p, "BTN 18 hold 100", 900 * MS) == "ERR bad_arguments");
    CHECK(say(p, "BTN 16 tap 100", 900 * MS).rfind("ERR", 0) == 0);  // SL is not a HORI button
    CHECK(say(p, "BTN 2 tap 0", 900 * MS).rfind("ERR", 0) == 0);
    CHECK(say(p, "RESET", 960 * MS) == "OK" && p.report(961 * MS) == neutral_report());
    CHECK(say(p, "LAUNCH", 970 * MS) == "ERR unsupported switch-pad is a controller");
    CHECK(say(p, "NONSENSE", 970 * MS).rfind("ERR", 0) == 0);
    CHECK(say(p, "", 970 * MS).empty());
    std::string st = say(p, "STATUS", 980 * MS);
    CHECK(st.rfind("OK status fw=switch-pad/0.1.2 usb=hori-0f0d:0092 usb_mounted=0 uptime_ms=980", 0) == 0);
    CHECK(st.find("macro=idle") != std::string::npos);
    CHECK(say(p, "BYE", 990 * MS) == "OK bye");
}

static void watchdog_neutralizes_after_one_second_of_silence() {
    Pad p;
    say(p, "BTN 0 press 10000", 0);
    say(p, "STICK r 0 32767 10000", 0);
    CHECK(p.report(999 * MS).b[0] == 0x04);
    CHECK(p.report(1000 * MS + 1) == neutral_report());
    CHECK(p.watchdog_trips() == 1);
    CHECK(p.report(5000 * MS) == neutral_report() && p.watchdog_trips() == 1);  // one trip per silence
    // Keep-alive pings hold a lease alive past 1 s.
    say(p, "BTN 1 press 3000", 6000 * MS);
    for (uint64_t t = 6300; t <= 8700; t += 300) say(p, "PING", t * MS);
    CHECK(p.report(8800 * MS).b[0] == 0x02);
    CHECK(p.report(9100 * MS) == neutral_report());  // the lease itself ended at 9000
    CHECK(p.watchdog_trips() == 1);
}

static void macro_frames_follow_the_device_clock() {
    Pad p;
    CHECK(say(p, "MBEGIN 3 30", 0) == "OK");
    CHECK(say(p, "MEV 0 1 0 0 0 0", 0).empty());
    CHECK(say(p, "MEV 10 0 32767 0 0 0", 0).empty());
    CHECK(say(p, "MEV 20 4 0 0 0 0", 0).empty());
    CHECK(say(p, "MSTATUS", 0) == "OK macro state=ready frame=0 duration=30 loaded=3 error=");
    const uint64_t t0 = 1000 * MS;
    CHECK(say(p, "MPLAY", t0) == "OK");
    // Frame n starts at t0 + n * 1e6 / 60 us: frame 10 at +166666.67 us, frame 20 at +333333.33 us.
    CHECK(p.report(t0).b[0] == 0x04);                     // A
    CHECK(p.report(t0 + 166666).b[0] == 0x04);            // still frame 9
    CHECK(p.report(t0 + 166667).b[3] == 255);             // frame 10: stick right, A released
    CHECK(p.report(t0 + 166667).b[0] == 0);
    CHECK(p.report(t0 + 333334).b[0] == 0x08);            // frame 20: X
    CHECK(say(p, "BTN 0 tap 100", t0 + 333334) == "ERR macro_busy");
    CHECK(p.report(t0 + 499999) .b[0] == 0x08);           // frame 29
    CHECK(p.report(t0 + 500000) == neutral_report());     // frame 30 = duration: finished
    CHECK(say(p, "MSTATUS", t0 + 500001).rfind("OK macro state=finished", 0) == 0);
}

static void macro_refusals_and_watchdog_stop() {
    Pad p;
    CHECK(say(p, "MBEGIN 1 10", 0) == "OK");
    CHECK(say(p, "MEV 0 262144 0 0 0 0", 0) == "ERR system_button");  // HOME (bit 18) never in a macro
    CHECK(say(p, "MBEGIN 2 10", 0) == "OK");
    CHECK(say(p, "MEV 5 1 0 0 0 0", 0).empty());
    CHECK(say(p, "MEV 5 1 0 0 0 0", 0) == "ERR frames_not_ascending");
    CHECK(say(p, "MBEGIN 1 600", 0) == "OK");
    CHECK(say(p, "MEV 0 1 0 0 0 0", 0).empty());
    CHECK(say(p, "MPLAY", 0) == "OK");
    CHECK(p.report(500 * MS).b[0] == 0x04);
    CHECK(p.report(1200 * MS) == neutral_report());  // host silent > 1 s: stopped
    CHECK(say(p, "MSTATUS", 1300 * MS) == "OK macro state=error frame=0 duration=600 loaded=1 error=watchdog");
    CHECK(p.watchdog_trips() == 1);
}

static void v0_verbs() {
    Pad p;
    CHECK(say(p, "press a 100", 0) == "OK");
    CHECK(p.report(50 * MS).b[0] == 0x04 && p.report(101 * MS) == neutral_report());
    CHECK(say(p, "press home 100", 200 * MS) == "OK" && p.report(250 * MS).b[1] == 0x10);
    CHECK(say(p, "press capture 100", 400 * MS) == "OK" && p.report(450 * MS).b[1] == 0x20);
    CHECK(say(p, "press a+b+up 200", 600 * MS) == "OK");
    Report r = p.report(650 * MS);
    CHECK(r.b[0] == 0x06 && r.b[2] == 0);
    CHECK(say(p, "press dright+zl 100", 900 * MS) == "OK" && p.report(950 * MS).b[2] == 2);
    CHECK(say(p, "clear", 960 * MS) == "OK" && p.report(961 * MS) == neutral_report());
    CHECK(say(p, "stick 0.5 1 300", 1000 * MS) == "OK");
    r = p.report(1100 * MS);
    CHECK(r.b[3] == 192 && r.b[4] == 1);
    CHECK(p.report(1301 * MS) == neutral_report());
    CHECK(say(p, "stick -1 -1 100", 1400 * MS) == "OK" && p.report(1450 * MS).b[3] == 1);
    CHECK(say(p, "status", 1500 * MS).rfind("OK status fw=switch-pad/0.1.2", 0) == 0);
    CHECK(say(p, "press sl_left 100", 1500 * MS) == "ERR unknown_button sl_left");
    CHECK(say(p, "press q 100", 1500 * MS) == "ERR unknown_button q");
    CHECK(say(p, "press a", 1500 * MS).rfind("ERR bad_arguments", 0) == 0);
    CHECK(say(p, "press a 0", 1500 * MS).rfind("ERR bad_arguments", 0) == 0);
    CHECK(say(p, "press a 10001", 1500 * MS).rfind("ERR bad_arguments", 0) == 0);
    CHECK(say(p, "stick 2 0 100", 1500 * MS).rfind("ERR bad_arguments", 0) == 0);
    CHECK(say(p, "stick x 0 100", 1500 * MS).rfind("ERR bad_arguments", 0) == 0);
    CHECK(say(p, "clear now", 1500 * MS).rfind("ERR bad_arguments", 0) == 0);
    // The watchdog covers v0 input too.
    CHECK(say(p, "press b 10000", 2000 * MS) == "OK");
    CHECK(p.report(2900 * MS).b[0] == 0x02 && p.report(3001 * MS + 1) == neutral_report());
}

int main() {
    v0_verbs();
    report_layout_matches_the_hori_descriptor();
    dpad_becomes_the_hat();
    sticks_scale_and_y_points_down();
    leases_and_protocol();
    watchdog_neutralizes_after_one_second_of_silence();
    macro_frames_follow_the_device_clock();
    macro_refusals_and_watchdog_stop();
    std::printf("%d checks, %d failures\n", checks, failures);
    return failures ? 1 : 0;
}
