// padsim: the switch-pad core on a pseudo-terminal or a TCP port, for the host client's tests
// (tools/host/tests/test_usbpad.py). The TCP mode is also the shape of the Raspberry Pi daemon (DESIGN.md 13a):
// the same core, a network line link guarded by a token, reports to /dev/hidg0 instead of a log.
//   padsim [--log reports.jsonl]                                   pty: prints the slave path on stdout
//   padsim --tcp PORT --token TOKEN [--bind 127.0.0.1] [--log ..]  TCP: prints "tcp PORT" on stdout
// Serves the line protocol exactly as the firmware does on UART0: one swpad::Pad, a 1 ms report loop,
// microsecond timestamps from CLOCK_MONOTONIC. Each report change is appended to --log as
// {"t_us": N, "r": [8 bytes]} (Pro mode) or {"t_us": N, "gc": [37 bytes]} (GC mode); a mode switch logs
// {"t_us": N, "mode": "gc|pro"}, then 500 ms without reports (the detach), then "the console" re-enumerates and, in GC mode,
// sends the adapter init 0x13. On the pty, boot noise like the ESP32-S3 ROM's is written first. On TCP, the first
// line must be `AUTH <token>` (else `ERR auth` and the connection closes); one client at a time.
// Exits on SIGTERM or when stdin closes.
#include <arpa/inet.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <poll.h>
#include <signal.h>
#include <stdlib.h>
#include <sys/socket.h>
#include <termios.h>
#include <time.h>
#include <unistd.h>

#include <cstdio>
#include <cstring>
#include <string>

#include "pad.hpp"

static volatile sig_atomic_t g_stop = 0;
static void on_term(int) { g_stop = 1; }

static uint64_t now_us() {
    timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return uint64_t(ts.tv_sec) * 1000000 + uint64_t(ts.tv_nsec) / 1000;
}

static void write_all(int fd, const char* p, size_t n) {
    while (n) {
        ssize_t w = write(fd, p, n);
        if (w <= 0) return;
        p += w;
        n -= size_t(w);
    }
}

int main(int argc, char** argv) {
    const char *log_path = nullptr, *token = nullptr, *bind_ip = "127.0.0.1";
    int tcp_port = -1;
    for (int i = 1; i + 1 < argc; i++) {
        if (!std::strcmp(argv[i], "--log")) log_path = argv[i + 1];
        if (!std::strcmp(argv[i], "--tcp")) tcp_port = std::atoi(argv[i + 1]);
        if (!std::strcmp(argv[i], "--token")) token = argv[i + 1];
        if (!std::strcmp(argv[i], "--bind")) bind_ip = argv[i + 1];
    }
    signal(SIGTERM, on_term);
    signal(SIGPIPE, SIG_IGN);
    int link = -1, listener = -1;
    if (tcp_port >= 0) {
        if (!token || std::strlen(token) < 16) return 2;  // a network link needs a token
        listener = socket(AF_INET, SOCK_STREAM, 0);
        int one = 1;
        setsockopt(listener, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
        sockaddr_in a{};
        a.sin_family = AF_INET;
        a.sin_port = htons(uint16_t(tcp_port));
        inet_pton(AF_INET, bind_ip, &a.sin_addr);
        if (bind(listener, reinterpret_cast<sockaddr*>(&a), sizeof a) || listen(listener, 1)) return 2;
        socklen_t len = sizeof a;
        getsockname(listener, reinterpret_cast<sockaddr*>(&a), &len);
        std::printf("tcp %d\n", ntohs(a.sin_port));
    } else {
        link = posix_openpt(O_RDWR | O_NOCTTY);
        if (link < 0 || grantpt(link) || unlockpt(link)) return 2;
        termios t;
        tcgetattr(link, &t);
        cfmakeraw(&t);
        tcsetattr(link, TCSANOW, &t);
        std::printf("%s\n", ptsname(link));
        const char noise[] = "ESP-ROM:esp32s3-20210327\r\nBuild:Mar 27 2021\r\nrst:0x1 (POWERON),boot:0x8 (SPI_FAST_FLASH_BOOT)\r\n";
        write_all(link, noise, sizeof noise - 1);
    }
    std::fflush(stdout);
    FILE* log = log_path ? std::fopen(log_path, "a") : nullptr;

    static swpad::Pad pad;  // ~70 KB (the macro buffer): static, as on the device
    pad.set_usb_mounted(true);
    const uint64_t t0 = now_us();
    std::string line;
    bool authed = tcp_port < 0;
    swpad::Report last = swpad::neutral_report();
    swpad::GcReport last_gc = swpad::neutral_gc_report();
    uint64_t detach_until = 0;
    bool first = true;
    while (!g_stop) {
        pollfd p[3] = {{0, POLLIN, 0}, {link, POLLIN, 0}, {listener, POLLIN, 0}};
        int ready = poll(p, 3, 1);
        if (ready > 0 && (p[0].revents & (POLLIN | POLLHUP))) {
            char c;
            if (read(0, &c, 1) <= 0) break;
        }
        if (ready > 0 && listener >= 0 && (p[2].revents & POLLIN)) {
            int c = accept(listener, nullptr, nullptr);
            if (link >= 0) {
                close(c);  // one client at a time
            } else {
                link = c;
                authed = false;
                line.clear();
            }
        }
        if (ready > 0 && link >= 0 && (p[1].revents & (POLLIN | POLLHUP))) {
            char buf[4096];
            ssize_t n = read(link, buf, sizeof buf);
            if (n <= 0 && listener >= 0) {
                close(link);  // the client left: the watchdog makes the pad neutral within 1 s
                link = -1;
            }
            for (ssize_t i = 0; i < n; i++) {
                if (buf[i] != '\n') {
                    if (line.size() < 512) line.push_back(buf[i]);
                    continue;
                }
                if (!authed) {
                    std::string want = std::string("AUTH ") + token;
                    if (line != want && line != want + "\r") {
                        write_all(link, "ERR auth\n", 9);
                        close(link);
                        link = -1;
                        line.clear();
                        break;
                    }
                    authed = true;
                }
                sr::LineBuf<192> out;
                pad.handle(line, now_us() - t0, out);
                line.clear();
                swpad::Mode next;
                if (pad.take_mode_change(next)) {  // as the firmware: detach, 500 ms, swap, re-attach
                    uint64_t at = now_us() - t0;
                    detach_until = at + 500000;
                    pad.set_usb_mounted(false);
                    if (log) {
                        std::fprintf(log, "{\"t_us\": %llu, \"mode\": \"%s\"}\n", (unsigned long long)at,
                                     next == swpad::Mode::Gc ? "gc" : "pro");
                        std::fflush(log);
                    }
                    first = true;
                }
                if (out.len) {
                    write_all(link, out.buf, out.len);
                    write_all(link, "\n", 1);
                }
            }
        }
        uint64_t now = now_us() - t0;
        if (detach_until) {
            if (now < detach_until) continue;  // detached: no reports
            detach_until = 0;
            pad.set_usb_mounted(true);  // "the console" re-enumerated the new personality
            if (pad.mode() == swpad::Mode::Gc) {
                const uint8_t init[] = {swpad::kGcInit};
                pad.output_report(0, init, 1);  // and starts the adapter, as the Switch does
            }
        }
        swpad::PadOut s = pad.state(now);
        pad.count_report();
        if (pad.mode() == swpad::Mode::Gc) {
            swpad::GcReport g = swpad::make_gc_report(s);
            if (log && (first || g != last_gc)) {
                std::fprintf(log, "{\"t_us\": %llu, \"gc\": [", (unsigned long long)now);
                for (size_t i = 0; i < sizeof g.b; i++) std::fprintf(log, i ? ", %d" : "%d", g.b[i]);
                std::fprintf(log, "]}\n");
                std::fflush(log);
            }
            first = false;
            last_gc = g;
            continue;
        }
        swpad::Report r = swpad::Pad::to_report(s);
        if (log && (first || r != last)) {
            std::fprintf(log, "{\"t_us\": %llu, \"r\": [%d, %d, %d, %d, %d, %d, %d, %d]}\n",
                         (unsigned long long)now, r.b[0], r.b[1], r.b[2], r.b[3], r.b[4], r.b[5], r.b[6], r.b[7]);
            std::fflush(log);
        }
        first = false;
        last = r;
    }
    if (log) std::fclose(log);
    if (link >= 0) close(link);
    if (listener >= 0) close(listener);
    return 0;
}
