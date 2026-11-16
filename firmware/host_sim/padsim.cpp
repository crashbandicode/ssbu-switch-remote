// padsim: the switch-pad core on a pseudo-terminal, for the host client's tests (tools/host/tests/test_usbpad.py).
//   padsim [--log reports.jsonl]
// Prints the pty slave path on stdout, then serves the line protocol on the master exactly as the firmware does
// on UART0: the same swpad::Pad, a 1 ms report loop, microsecond timestamps from CLOCK_MONOTONIC. Each report
// change is appended to --log as {"t_us": N, "r": [8 bytes]}. Boot noise like the ESP32-S3 ROM's is written first.
// Exits on SIGTERM or when stdin closes.
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stdlib.h>
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
    const char* log_path = nullptr;
    for (int i = 1; i + 1 < argc; i++)
        if (!std::strcmp(argv[i], "--log")) log_path = argv[i + 1];
    signal(SIGTERM, on_term);
    signal(SIGPIPE, SIG_IGN);
    int m = posix_openpt(O_RDWR | O_NOCTTY);
    if (m < 0 || grantpt(m) || unlockpt(m)) return 2;
    termios t;
    tcgetattr(m, &t);
    cfmakeraw(&t);
    tcsetattr(m, TCSANOW, &t);
    std::printf("%s\n", ptsname(m));
    std::fflush(stdout);
    FILE* log = log_path ? std::fopen(log_path, "a") : nullptr;

    static swpad::Pad pad;  // ~100 KB (the macro buffer): static, as on the device
    pad.set_usb_mounted(true);
    const uint64_t t0 = now_us();
    const char noise[] = "ESP-ROM:esp32s3-20210327\r\nBuild:Mar 27 2021\r\nrst:0x1 (POWERON),boot:0x8 (SPI_FAST_FLASH_BOOT)\r\n";
    write_all(m, noise, sizeof noise - 1);

    std::string line;
    swpad::Report last = swpad::neutral_report();
    bool first = true;
    while (!g_stop) {
        pollfd p[2] = {{m, POLLIN, 0}, {0, POLLIN, 0}};
        int ready = poll(p, 2, 1);
        if (ready > 0 && (p[1].revents & (POLLIN | POLLHUP))) {
            char c;
            if (read(0, &c, 1) <= 0) break;
        }
        if (ready > 0 && (p[0].revents & POLLIN)) {
            char buf[4096];
            ssize_t n = read(m, buf, sizeof buf);
            for (ssize_t i = 0; i < n; i++) {
                if (buf[i] != '\n') {
                    if (line.size() < 512) line.push_back(buf[i]);
                    continue;
                }
                sr::LineBuf<192> out;
                pad.handle(line, now_us() - t0, out);
                line.clear();
                if (out.len) {
                    write_all(m, out.buf, out.len);
                    write_all(m, "\n", 1);
                }
            }
        }
        uint64_t now = now_us() - t0;
        swpad::Report r = pad.report(now);
        pad.count_report();
        if (log && (first || r != last)) {
            std::fprintf(log, "{\"t_us\": %llu, \"r\": [%d, %d, %d, %d, %d, %d, %d, %d]}\n",
                         (unsigned long long)now, r.b[0], r.b[1], r.b[2], r.b[3], r.b[4], r.b[5], r.b[6], r.b[7]);
            std::fflush(log);
        }
        first = false;
        last = r;
    }
    if (log) std::fclose(log);
    close(m);
    return 0;
}
