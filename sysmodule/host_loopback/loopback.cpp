// Host loopback build of the sysmodule: sr::Service on POSIX sockets with a fake platform.
// It lets the host client and tests run the real protocol code without a console.
//
//   loopback --port P --root DIR --pad-log FILE [--token-file F]
//
// - The fake SD is DIR (roots from kRootPaths live under it, e.g. DIR/atmosphere/crash_reports).
// - The "application" runs while DIR/fake-app exists (text: "<pid> <program hex>"); TERMINATE deletes it.
// - Every state sent to the virtual controller is appended to FILE as JSON with a host timestamp.
// - The scene channel listens on 127.0.0.1:P+1, as on the console.
#include <arpa/inet.h>
#include <dirent.h>
#include <netinet/in.h>
#include <poll.h>
#include <pthread.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#include <atomic>
#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>

#include "service.hpp"

using namespace sr;

namespace {

std::string g_root, g_pad_log;
std::atomic<bool> g_stop{false};

uint64_t mono_ns() {
    timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return uint64_t(ts.tv_sec) * 1000000000ull + uint64_t(ts.tv_nsec);
}

class FakePlatform final : public Platform {
public:
    uint64_t now() override { return mono_ns(); }
    uint64_t freq() override { return 1000000000ull; }
    void lock() override { pthread_mutex_lock(&m_); }
    void unlock() override { pthread_mutex_unlock(&m_); }
    void log(const char* line) override { std::fprintf(stderr, "[loopback] %s\n", line); }
    void app(uint64_t& pid, uint64_t& program) override {
        pid = program = 0;
        FILE* f = std::fopen((g_root + "/fake-app").c_str(), "r");
        if (!f) return;
        unsigned long long p = 0, prog = 0;
        if (std::fscanf(f, "%llu %llx", &p, &prog) == 2) {
            pid = p;
            program = prog;
        }
        std::fclose(f);
    }
    uint32_t terminate(uint64_t) override { return std::remove((g_root + "/fake-app").c_str()) == 0 ? 0 : 1; }
    void sleep_ms(uint64_t ms) override { usleep(useconds_t(ms * 1000)); }
    bool hdls_attach() override {
        attached_ = true;
        record("{\"event\":\"attach\"");
        return true;
    }
    void hdls_detach() override {
        attached_ = false;
        record("{\"event\":\"detach\"");
    }
    void hdls_send(const PadState& p) override {
        char b[200];
        std::snprintf(b, sizeof b, "{\"event\":\"state\",\"buttons\":%llu,\"lx\":%d,\"ly\":%d,\"rx\":%d,\"ry\":%d",
                      (unsigned long long)(p.buttons & (kInjectableMask | kButtonHome)), p.lx, p.ly, p.rx, p.ry);
        record(b);
    }
    bool list(const char* path, void (*entry)(void*, const Entry&), void* ctx) override {
        std::string full = g_root + path;
        DIR* d = opendir(full.c_str());
        if (!d) return false;
        while (dirent* e = readdir(d)) {
            if (!std::strcmp(e->d_name, ".") || !std::strcmp(e->d_name, "..")) continue;
            struct stat st = {};
            stat((full + "/" + e->d_name).c_str(), &st);
            entry(ctx, Entry{S_ISDIR(st.st_mode), uint64_t(st.st_size), uint64_t(st.st_mtime), e->d_name});
        }
        closedir(d);
        return true;
    }
    long read(const char* path, uint64_t offset, char* buf, uint32_t max, uint64_t& size) override {
        FILE* f = std::fopen((g_root + path).c_str(), "rb");
        if (!f) return -1;
        std::fseek(f, 0, SEEK_END);
        size = uint64_t(std::ftell(f));
        long n = 0;
        if (offset < size) {
            std::fseek(f, long(offset), SEEK_SET);
            n = long(std::fread(buf, 1, max, f));
        }
        std::fclose(f);
        return n;
    }

private:
    void record(const char* body) {
        FILE* f = std::fopen(g_pad_log.c_str(), "a");
        if (!f) return;
        timespec ts;
        clock_gettime(CLOCK_REALTIME, &ts);
        std::fprintf(f, "%s,\"wall\":%.6f}\n", body, double(ts.tv_sec) + double(ts.tv_nsec) / 1e9);
        std::fclose(f);
    }
    pthread_mutex_t m_ = PTHREAD_MUTEX_INITIALIZER;
    bool attached_ = false;
};

FakePlatform g_platform;
Service* g_service;

void* pad_thread(void*) {
    PadState last;
    uint64_t last_ns = 0;
    while (!g_stop) {
        usleep(1000);
        uint64_t now = mono_ns();
        PadState want;
        bool need = g_service->pad_step(now, want);
        if (need && !g_service->attached()) g_service->set_attached(g_platform.hdls_attach());
        if (!g_service->attached()) continue;
        if (!(want == last) || now - last_ns > 50000000ull) {
            g_platform.hdls_send(want);
            last = want;
            last_ns = now;
        }
    }
    return nullptr;
}

struct FdSink final : Sink {
    int fd;
    explicit FdSink(int f) : fd(f) {}
    bool write(const void* data, size_t n) override {
        const char* p = static_cast<const char*>(data);
        while (n) {
            ssize_t w = send(fd, p, n, MSG_NOSIGNAL);
            if (w <= 0) return false;
            p += w;
            n -= size_t(w);
        }
        return true;
    }
};

struct Conn {
    int fd = -1;
    Session session;
    char buf[kLineMax];
    size_t len = 0;
};

int listen_on(uint32_t addr, uint16_t port) {
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    int one = 1;
    setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
    sockaddr_in sa = {};
    sa.sin_family = AF_INET;
    sa.sin_port = htons(port);
    sa.sin_addr.s_addr = htonl(addr);
    if (bind(fd, (sockaddr*)&sa, sizeof sa) != 0 || listen(fd, 2) != 0) {
        std::perror("listen");
        std::exit(2);
    }
    return fd;
}

bool pump(Conn& c) {
    ssize_t n = recv(c.fd, c.buf + c.len, sizeof c.buf - c.len, 0);
    if (n <= 0) return false;
    c.len += size_t(n);
    FdSink out(c.fd);
    while (char* nl = static_cast<char*>(std::memchr(c.buf, '\n', c.len))) {
        size_t line_len = size_t(nl - c.buf);
        if (!g_service->handle(c.session, std::string_view(c.buf, line_len), out)) return false;
        std::memmove(c.buf, nl + 1, c.len - line_len - 1);
        c.len -= line_len + 1;
    }
    return c.len < sizeof c.buf;
}

void accept_into(int lfd, Conn& c, bool scene, const Config& cfg) {
    sockaddr_in sa = {};
    socklen_t len = sizeof sa;
    int fd = accept(lfd, (sockaddr*)&sa, &len);
    if (fd < 0) return;
    uint32_t ip = ntohl(sa.sin_addr.s_addr);
    bool ok = scene ? (ip >> 24) == 127 : peer_allowed(cfg, ip);
    if (!ok || c.fd >= 0) {
        close(fd);
        return;
    }
    c = Conn{};
    c.fd = fd;
    c.session.scene = scene;
}

}  // namespace

int main(int argc, char** argv) {
    int port = 0;
    std::string config_text;
    for (int i = 1; i + 1 < argc; i += 2) {
        std::string k = argv[i];
        if (k == "--port") port = std::atoi(argv[i + 1]);
        else if (k == "--root") g_root = argv[i + 1];
        else if (k == "--pad-log") g_pad_log = argv[i + 1];
        else if (k == "--config") {
            FILE* f = std::fopen(argv[i + 1], "rb");
            if (!f) return 2;
            char b[2048];
            size_t n = std::fread(b, 1, sizeof b, f);
            std::fclose(f);
            config_text.assign(b, n);
        }
    }
    Config cfg;
    if (const char* e = parse_config(config_text, cfg)) {
        std::fprintf(stderr, "config: %s\n", e);
        return 2;
    }
    if (port) cfg.port = uint16_t(port);
    static Service service(g_platform, cfg);
    g_service = &service;
    pthread_t pad;
    pthread_create(&pad, nullptr, pad_thread, nullptr);
    int host_l = listen_on(INADDR_LOOPBACK, cfg.port), scene_l = listen_on(INADDR_LOOPBACK, uint16_t(cfg.port + 1));
    std::printf("{\"serving\":%u,\"scene\":%u}\n", cfg.port, cfg.port + 1);
    std::fflush(stdout);
    Conn host, scene;
    while (true) {
        pollfd p[4];
        int n = 0, hi = -1, si = -1;
        p[n++] = {host_l, POLLIN, 0};
        p[n++] = {scene_l, POLLIN, 0};
        if (host.fd >= 0) hi = n, p[n++] = {host.fd, POLLIN, 0};
        if (scene.fd >= 0) si = n, p[n++] = {scene.fd, POLLIN, 0};
        int ready = poll(p, nfds_t(n), 100);
        g_service->refresh_app();
        if (ready <= 0) continue;
        if (p[0].revents & POLLIN) accept_into(host_l, host, false, cfg);
        if (p[1].revents & POLLIN) accept_into(scene_l, scene, true, cfg);
        if (hi >= 0 && p[hi].revents && !pump(host)) close(host.fd), host = Conn{};
        if (si >= 0 && p[si].revents && !pump(scene)) close(scene.fd), scene = Conn{};
    }
}
