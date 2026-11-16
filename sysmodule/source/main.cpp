// switch-remote sysmodule (console half): libnx platform for sr::Service, the LAN and loopback
// servers, and the pad thread. DESIGN.md §1-§4, §8.
//
// Boot safety (DESIGN.md §8): a static 1 MiB heap, no service is touched for 10 s after start, and
// nothing calls fatalThrow: every failure is logged to sd:/ultimate/switch-remote/sysmodule.log
// and the module idles. Recovery: delete atmosphere/contents/4200000000005352/ in hekate UMS.
#include <arpa/inet.h>
#include <dirent.h>
#include <errno.h>
#include <netinet/in.h>
#include <poll.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <switch.h>
#include <unistd.h>

#include "service.hpp"

// libnx has its own global PadState and Service: name ours with sr::, import the rest.
using sr::AppRecord;
using sr::Config;
using sr::kButtonHome;
using sr::kInjectableMask;
using sr::kLineMax;
using sr::Platform;
using sr::Session;
using sr::Sink;

extern "C" {
u32 __nx_applet_type = AppletType_None;
u32 __nx_fs_num_sessions = 1;

#define INNER_HEAP_SIZE 0x200000  // 2 MiB: socket transfer memory (~296 KiB) + stdio + headroom
size_t nx_inner_heap_size = INNER_HEAP_SIZE;
char nx_inner_heap[INNER_HEAP_SIZE];

void __libnx_initheap(void) {
    extern char* fake_heap_start;
    extern char* fake_heap_end;
    fake_heap_start = nx_inner_heap;
    fake_heap_end = nx_inner_heap + nx_inner_heap_size;
}

// Nothing at process start: services come up in main() after the boot delay.
void __appInit(void) {}
void __appExit(void) {}
}

namespace {

constexpr const char* kDir = "sdmc:/ultimate/switch-remote";
constexpr const char* kLog = "sdmc:/ultimate/switch-remote/sysmodule.log";
constexpr const char* kConfig = "sdmc:/ultimate/switch-remote/config.txt";
constexpr long kLogMax = 256 * 1024;
constexpr u64 kBootDelayNs = 10'000'000'000ull;

bool g_sd = false;
// Between PSC's sleep request and the wake notification no fsp-srv or bsd IPC may happen (r5; sys-autopilot
// server.c:187-219 explains the console crash otherwise). The log and status writers stay silent then.
volatile bool g_quiet = false;

void write_log(const char* line) {
    if (!g_sd || g_quiet) return;
    mkdir("sdmc:/ultimate", 0777);
    mkdir(kDir, 0777);
    struct stat st;
    FILE* f = fopen(kLog, (stat(kLog, &st) == 0 && st.st_size > kLogMax) ? "w" : "a");
    if (!f) return;
    fprintf(f, "%llu %s\n", (unsigned long long)(armTicksToNs(armGetSystemTick()) / 1000000), line);
    fclose(f);
}

constexpr const char* kStatus = "sdmc:/ultimate/switch-remote/status.txt";
sr::BootStatus g_boot;

u64 uptime_ms() { return armTicksToNs(armGetSystemTick()) / 1000000; }

// status.txt: one line, rewritten at every stage and every 10 s while serving (readable over UMS/DBI or FILE
// even when the LAN never came up).
void write_status() {
    if (!g_sd || g_quiet) return;
    g_boot.uptime_ms = uptime_ms();
    char line[256];
    sr::format_status(g_boot, line, sizeof line);
    FILE* f = fopen(kStatus, "w");
    if (!f) return;
    fprintf(f, "%s\n", line);
    fclose(f);
}

void stage(const char* name) {
    g_boot.stage = name;
    char line[96];
    snprintf(line, sizeof line, "stage %s", name);
    write_log(line);
    write_status();
}

void fail(const char* what, Result rc) {
    static char err[64];
    snprintf(err, sizeof err, "%s_rc_0x%x", what, rc);
    g_boot.last_error = err;
}

class NxPlatform final : public Platform {
public:
    NxPlatform() {
        mutexInit(&mutex_);
        mutexInit(&hdls_mutex_);
    }
    uint64_t now() override { return armGetSystemTick(); }
    uint64_t freq() override { return armGetSystemTickFreq(); }
    void lock() override { mutexLock(&mutex_); }
    void unlock() override { mutexUnlock(&mutex_); }
    void log(const char* line) override {
        mutexLock(&log_mutex_);
        write_log(line);
        mutexUnlock(&log_mutex_);
    }
    void app(uint64_t& pid, uint64_t& program) override {
        u64 p = 0, prog = 0;
        if (R_FAILED(pmdmntGetApplicationProcessId(&p))) p = 0;
        if (p && R_FAILED(pminfoGetProgramId(&prog, p))) prog = 0;
        pid = p;
        program = prog;
    }
    long app_records(AppRecord* out, size_t max) override {
        static NsApplicationRecord rec[16];
        s32 n = 0;
        if (max > 16) max = 16;
        if (R_FAILED(nsListApplicationRecord(rec, s32(max), 0, &n))) return -1;
        for (s32 i = 0; i < n; i++) out[i] = AppRecord{rec[i].application_id, rec[i].last_event, rec[i].last_updated};
        return n;
    }
    uint32_t terminate(uint64_t pid) override {
        Result rc = nsdevTerminateApplication();
        if (R_FAILED(rc)) rc = pglTerminateProcess(pid);  // Atmosphere creport's way on 10.0+
        return rc;
    }
    void sleep_ms(uint64_t ms) override { svcSleepThread(ms * 1'000'000ull); }
    bool write_at(const char* path, uint64_t offset, const void* data, size_t n) override {
        char full[300];
        snprintf(full, sizeof full, "sdmc:%s", path);
        FILE* f = fopen(full, offset == 0 ? "wb" : "r+b");
        if (!f) return false;
        bool ok = fseek(f, long(offset), SEEK_SET) == 0 && fwrite(data, 1, n, f) == n;
        ok = fclose(f) == 0 && ok;
        return ok;
    }
    bool replace(const char* from, const char* to) override {
        char a[300], b[300];
        snprintf(a, sizeof a, "sdmc:%s", from);
        snprintf(b, sizeof b, "sdmc:%s", to);
        ::remove(b);  // FAT rename does not replace; the old file is already backed up and read back
        return rename(a, b) == 0;
    }
    bool remove(const char* path) override {
        char full[300];
        snprintf(full, sizeof full, "sdmc:%s", path);
        return ::remove(full) == 0;
    }
    bool make_dirs(const char* dir) override {
        char full[300];
        int n = snprintf(full, sizeof full, "sdmc:%s", dir);
        for (int i = 6; i <= n; i++) {
            if (i == n || full[i] == '/') {
                char c = full[i];
                full[i] = 0;
                mkdir(full, 0777);
                full[i] = c;
            }
        }
        return true;
    }
    // The pad thread attaches and sends; the network thread detaches and releases on sleep (r5): one lock.
    bool hdls_attach() override {
        mutexLock(&hdls_mutex_);
        bool ok = attach_locked();
        mutexUnlock(&hdls_mutex_);
        return ok;
    }
    void hdls_detach() override {
        mutexLock(&hdls_mutex_);
        detach_locked();
        mutexUnlock(&hdls_mutex_);
    }
    void hdls_send(const sr::PadState& p) override {
        mutexLock(&hdls_mutex_);
        send_locked(p);
        mutexUnlock(&hdls_mutex_);
    }
    // Sleep: detach and release the work buffer (hid transfer memory held across a sleep crashes it).
    void hdls_release() {
        mutexLock(&hdls_mutex_);
        detach_locked();
        if (work_ready_) {
            hiddbgReleaseHdlsWorkBuffer(session_);
            work_ready_ = false;
        }
        mutexUnlock(&hdls_mutex_);
    }

private:
    bool attach_locked() {
        if (attached_) return true;
        if (!work_ready_) {
            Result rc = hiddbgAttachHdlsWorkBuffer(&session_, work_, sizeof work_);
            if (R_FAILED(rc)) return fail("hdls work buffer", rc);
            work_ready_ = true;
        }
        HiddbgHdlsDeviceInfo info = {};
        info.deviceType = HidDeviceType_FullKey3;  // a Pro Controller, as sys-botbase attaches (study only)
        info.npadInterfaceType = HidNpadInterfaceType_Bluetooth;
        info.singleColorBody = RGBA8_MAXALPHA(0x30, 0x30, 0x30);
        info.singleColorButtons = RGBA8_MAXALPHA(0xff, 0xff, 0xff);
        info.colorLeftGrip = info.colorRightGrip = RGBA8_MAXALPHA(0x30, 0x30, 0x30);
        Result rc = hiddbgAttachHdlsVirtualDevice(&handle_, &info);
        if (R_FAILED(rc)) return fail("hdls attach", rc);
        attached_ = true;
        log("hdls attached");
        return true;
    }
    void detach_locked() {
        if (!attached_) return;
        hiddbgDetachHdlsVirtualDevice(handle_);
        attached_ = false;
        log("hdls detached");
    }
    void send_locked(const sr::PadState& p) {
        if (!attached_) return;
        HiddbgHdlsState s = {};
        s.battery_level = 4;
        s.flags = 1;  // powered
        // Bits 0..15 plus HOME for the launch sequence only (host input can't carry bit 18: core refuses
        // it). hid derives the stick-direction bits itself and masks bits 20..27 on 9.0+ (libnx hiddbg.h).
        s.buttons = p.buttons & (kInjectableMask | kButtonHome);
        s.analog_stick_l.x = p.lx;
        s.analog_stick_l.y = p.ly;
        s.analog_stick_r.x = p.rx;
        s.analog_stick_r.y = p.ry;
        hiddbgSetHdlsState(handle_, &s);
    }

public:
    bool list(const char* path, void (*entry)(void*, const Entry&), void* ctx) override {
        char full[300];
        snprintf(full, sizeof full, "sdmc:%s", path);
        DIR* d = opendir(full);
        if (!d) return false;
        while (struct dirent* e = readdir(d)) {
            char item[600];
            snprintf(item, sizeof item, "%s/%s", full, e->d_name);
            struct stat st = {};
            stat(item, &st);
            entry(ctx, Entry{S_ISDIR(st.st_mode), uint64_t(st.st_size), uint64_t(st.st_mtime), e->d_name});
        }
        closedir(d);
        return true;
    }
    long read(const char* path, uint64_t offset, char* buf, uint32_t max, uint64_t& size) override {
        char full[300];
        snprintf(full, sizeof full, "sdmc:%s", path);
        FILE* f = fopen(full, "rb");
        if (!f) return -1;
        fseek(f, 0, SEEK_END);
        long total = ftell(f);
        size = total < 0 ? 0 : uint64_t(total);
        long n = 0;
        if (offset < size) {
            fseek(f, long(offset), SEEK_SET);
            n = long(fread(buf, 1, max, f));
        }
        fclose(f);
        return n;
    }

private:
    bool fail(const char* what, Result rc) {
        char line[96];
        snprintf(line, sizeof line, "%s failed rc=0x%x", what, rc);
        log(line);
        return false;
    }
    Mutex mutex_, log_mutex_ = {}, hdls_mutex_ = {};
    bool work_ready_ = false, attached_ = false;
    HiddbgHdlsSessionId session_ = {};
    HiddbgHdlsHandle handle_ = {};
    alignas(0x1000) u8 work_[0x1000];
};

NxPlatform* g_platform;
sr::Service* g_service;
volatile bool g_pad_stop = false;
volatile bool g_asleep = false;
Config g_cfg;

void pad_thread(void*) {  // ends when g_pad_stop is set (RESTART/SHUTDOWN)
    sr::PadState last;
    u64 last_tick = 0;
    bool attached = false;
    while (!g_pad_stop) {
        svcSleepThread(1'000'000);
        if (g_asleep) {  // r5: nothing touches hid between the sleep request and the wake
            attached = false;
            continue;
        }
        u64 now = armGetSystemTick();
        sr::PadState want;
        bool need = g_service->pad_step(now, want);
        if (need && !attached) {
            attached = g_platform->hdls_attach();
            g_service->set_attached(attached);
        }
        if (!need && g_service->attached()) {  // idle detach (core detach_due): a hard kill then leaves no pad
            g_platform->hdls_detach();
            g_service->set_attached(false);
        }
        if (!g_service->attached()) {
            attached = false;
            continue;
        }
        // Re-send unchanged states every 50 ms so hid never sees an idle controller.
        if (!(want == last) || armTicksToNs(now - last_tick) > 50'000'000ull) {
            g_platform->hdls_send(want);
            last = want;
            last_tick = now;
        }
    }
}

struct SocketSink final : Sink {
    int fd;
    explicit SocketSink(int f) : fd(f) {}
    bool write(const void* data, size_t n) override {
        const char* p = static_cast<const char*>(data);
        while (n) {
            ssize_t w = send(fd, p, n, 0);
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
    char buf[4096];
    size_t len = 0;
};

void close_conn(Conn& c) {
    if (c.fd >= 0) close(c.fd);
    c = Conn{};
}

int listen_on(u32 addr, u16 port) {
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) return -1;
    int one = 1;
    setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
    sockaddr_in sa = {};
    sa.sin_family = AF_INET;
    sa.sin_port = htons(port);
    sa.sin_addr.s_addr = htonl(addr);
    if (bind(fd, (sockaddr*)&sa, sizeof sa) != 0 || listen(fd, 2) != 0) {
        close(fd);
        return -1;
    }
    return fd;
}

void accept_into(int lfd, Conn& c, bool scene) {
    sockaddr_in sa = {};
    socklen_t len = sizeof sa;
    int fd = accept(lfd, (sockaddr*)&sa, &len);
    if (fd < 0) return;
    u32 ip = ntohl(sa.sin_addr.s_addr);
    bool ok = scene ? (ip >> 24) == 127 : sr::peer_allowed(g_cfg, ip);
    char line[96];
    if (!ok || c.fd >= 0) {  // one client per channel
        snprintf(line, sizeof line, "%s peer %u.%u.%u.%u refused (%s)", scene ? "scene" : "host", ip >> 24,
                 (ip >> 16) & 255, (ip >> 8) & 255, ip & 255, ok ? "busy" : "not_private");
        g_platform->log(line);
        close(fd);
        return;
    }
    c = Conn{};
    c.fd = fd;
    c.session.scene = scene;
    snprintf(line, sizeof line, "%s peer %u.%u.%u.%u connected", scene ? "scene" : "host", ip >> 24, (ip >> 16) & 255,
             (ip >> 8) & 255, ip & 255);
    g_platform->log(line);
}

// Reads what is there and handles complete lines. False: the peer closed or broke the protocol.
bool pump(Conn& c) {
    ssize_t n = recv(c.fd, c.buf + c.len, sizeof c.buf - c.len, 0);
    if (n <= 0) return false;
    c.len += size_t(n);
    SocketSink out(c.fd);
    size_t off = 0;
    while (off < c.len) {
        if (!c.session.scene && g_service->put_pending()) {  // PUT payload bytes
            off += g_service->put_feed(c.buf + off, c.len - off, out);
            continue;
        }
        char* nl = static_cast<char*>(memchr(c.buf + off, '\n', c.len - off));
        if (!nl) break;
        size_t line_len = size_t(nl - (c.buf + off));
        if (!g_service->handle(c.session, std::string_view(c.buf + off, line_len), out)) return false;
        off += line_len + 1;
    }
    memmove(c.buf, c.buf + off, c.len - off);
    c.len -= off;
    return c.len < kLineMax || g_service->put_pending();
}

// --- r5: sleep/wake (PSC) and network changes. Re-implemented after sys-autopilot (TooTallNate),
// source/common/power.c and server.c:187-251 (studied only; no code copied; docs/PROVENANCE.md). ---
PscPmModule g_psc;
bool g_psc_ok = false;

void init_power() {
    Result rc = pscmInitialize();
    if (R_SUCCEEDED(rc)) {
        // Fs as a dependency: PSC tells us before the SD goes away.
        static const u32 deps[] = {PscPmModuleId_Fs};
        rc = pscmGetPmModule(&g_psc, PscPmModuleId(0x5352), deps, 1, true);
        g_psc_ok = R_SUCCEEDED(rc);
    }
    char line[64];
    snprintf(line, sizeof line, "init psc:m rc=0x%x", rc);
    write_log(line);
    if (R_FAILED(rc)) fail("psc", rc);
}

enum class Power : u8 { None, Sleep, Wake };

Power power_poll(PscPmState& st) {
    if (!g_psc_ok || R_FAILED(eventWait(&g_psc.event, 0))) return Power::None;
    u32 flags = 0;
    if (R_FAILED(pscPmModuleGetRequest(&g_psc, &st, &flags))) return Power::None;
    switch (st) {
        case PscPmState_ReadySleep:
        case PscPmState_ReadySleepCritical:
        case PscPmState_ReadyShutdown: return Power::Sleep;
        default: return Power::Wake;
    }
}

u32 current_ip() {
    u32 ip = 0;
    if (R_FAILED(nifmGetCurrentIpAddress(&ip))) return 0;
    return ntohl(ip);
}

// RESTART / SHUTDOWN (and nothing else) end serve(): every socket is closed and the pad detached first.
sr::Action serve() {
    int host_l = -1, scene_l = -1;
    Conn host, scene;
    u64 last_app_poll = 0, last_ip_poll = 0;
    sr::IpWatch ipw;
    auto close_all = [&]() {
        close_conn(host);
        close_conn(scene);
        if (host_l >= 0) close(host_l);
        if (scene_l >= 0) close(scene_l);
        host_l = scene_l = -1;
        g_boot.listening = false;
    };
    while (true) {
        PscPmState st;
        Power pw = power_poll(st);
        if (pw == Power::Sleep) {
            write_log("power: sleep requested; closing sockets, releasing the virtual controller");
            g_quiet = true;  // no fsp/bsd IPC until the wake notification
            g_asleep = true;
            if (host.fd >= 0) g_service->on_disconnect(host.session);
            close_all();
            g_platform->hdls_release();
            g_service->set_attached(false);
            pscPmModuleAcknowledge(&g_psc, st);
            continue;
        }
        if (pw == Power::Wake) {
            pscPmModuleAcknowledge(&g_psc, st);
            if (st == PscPmState_Awake) {
                g_quiet = false;
                g_asleep = false;
                write_log("power: awake; reopening listeners");
                ipw = sr::IpWatch{};
            }
            continue;
        }
        if (g_asleep) {
            svcSleepThread(100'000'000ull);
            continue;
        }
        if (uptime_ms() - last_ip_poll >= sr::kIpPollMs) {
            last_ip_poll = uptime_ms();
            u32 ip = current_ip();
            g_boot.ip = ip;
            if (ipw.changed(ip) && (host_l >= 0 || scene_l >= 0)) {
                char line[96];
                snprintf(line, sizeof line, "network: IP changed to %u.%u.%u.%u; rebuilding listeners", ip >> 24,
                         (ip >> 16) & 255, (ip >> 8) & 255, ip & 255);
                write_log(line);
                if (host.fd >= 0) g_service->on_disconnect(host.session);
                close_all();
                write_status();
            }
        }
        if (host_l < 0) host_l = listen_on(INADDR_ANY, g_cfg.port);
        if (scene_l < 0) scene_l = listen_on(INADDR_LOOPBACK, u16(g_cfg.port + 1));
        if (host_l < 0 || scene_l < 0) {
            static unsigned attempts = 0;
            attempts++;
            static char err[48];
            snprintf(err, sizeof err, "listen_errno_%d", errno);
            g_boot.last_error = err;
            g_boot.listening = false;
            if (sr::log_attempt(attempts)) {
                char line[112];
                snprintf(line, sizeof line, "listen failed host=%d scene=%d errno=%d (attempt %u); retrying", host_l,
                         scene_l, errno, attempts);
                g_platform->log(line);
                write_status();
            }
            svcSleepThread(sr::retry_delay_ms(attempts) * 1'000'000ull);
            continue;
        }
        if (!g_boot.listening) {
            g_boot.listening = true;
            g_boot.stage = "serving";
            write_status();
        }
        pollfd p[4];
        int n = 0, hi = -1, si = -1;
        p[n++] = {host_l, POLLIN, 0};
        p[n++] = {scene_l, POLLIN, 0};
        if (host.fd >= 0) {
            hi = n;
            p[n++] = {host.fd, POLLIN, 0};
        }
        if (scene.fd >= 0) {
            si = n;
            p[n++] = {scene.fd, POLLIN, 0};
        }
        int ready = poll(p, nfds_t(n), 100);  // PSC requests are checked every loop
        static u64 last_status = 0;
        if (uptime_ms() - last_status >= 10000) {
            write_status();
            last_status = uptime_ms();
        }
        if (armTicksToNs(armGetSystemTick() - last_app_poll) > 500'000'000ull) {
            g_service->refresh_app();
            last_app_poll = armGetSystemTick();
        }
        if (ready <= 0) continue;
        if (p[0].revents & POLLIN) accept_into(host_l, host, false);
        if (p[1].revents & POLLIN) accept_into(scene_l, scene, true);
        if (hi >= 0 && p[hi].revents && !pump(host)) {
            g_platform->log("host peer closed");
            g_service->on_disconnect(host.session);
            close_conn(host);
            sr::Action a = g_service->take_action();
            if (a != sr::Action::None) {
                close_conn(scene);
                close(host_l);
                close(scene_l);
                return a;
            }
        }
        if (si >= 0 && p[si].revents && !pump(scene)) close_conn(scene);
    }
}

bool load_config() {
    FILE* f = fopen(kConfig, "rb");
    if (!f) return false;
    static char text[2048];
    size_t n = fread(text, 1, sizeof text - 1, f);
    fclose(f);
    if (const char* e = sr::parse_config(std::string_view(text, n), g_cfg)) {
        char line[96];
        snprintf(line, sizeof line, "config invalid: %s", e);
        write_log(line);
        return false;
    }
    return true;
}

// The SD first: until it is mounted nothing can be logged, so it is retried rather than given up on.
void open_sd() {
    for (unsigned attempt = 1;; attempt++) {
        if (R_SUCCEEDED(smInitialize())) {
            if (R_SUCCEEDED(fsInitialize()) && R_SUCCEEDED(fsdevMountSdmc())) {
                g_sd = true;
                return;
            }
            fsExit();
            smExit();
        }
        svcSleepThread(sr::retry_delay_ms(attempt) * 1'000'000ull);
    }
}

void init_services() {
    if (R_SUCCEEDED(setsysInitialize())) {
        SetSysFirmwareVersion fw;
        if (R_SUCCEEDED(setsysGetFirmwareVersion(&fw))) {
            hosversionSet(MAKEHOSVERSION(fw.major, fw.minor, fw.micro));
            char line[64];
            snprintf(line, sizeof line, "firmware %u.%u.%u", fw.major, fw.minor, fw.micro);
            write_log(line);
        }
        setsysExit();
    }
    // One line before and after each call: a call that never returns leaves its "init" line last in the log.
    struct {
        const char* name;
        Result (*init)();
    } svc[] = {{"hid:dbg", hiddbgInitialize}, {"pm:dmnt", pmdmntInitialize}, {"pm:info", pminfoInitialize},
               {"ns:dev", nsdevInitialize},   {"ns:am2", nsInitialize},       {"pm:shell", pmshellInitialize},
               {"pgl", pglInitialize}};
    for (auto& v : svc) {
        char line[96];
        snprintf(line, sizeof line, "init %s ...", v.name);
        write_log(line);
        Result rc = v.init();
        snprintf(line, sizeof line, "init %s rc=0x%x", v.name, rc);
        write_log(line);
        if (R_FAILED(rc)) fail(v.name, rc);
    }
}

// Waits (bounded) for the console to get an IP, so the log and status.txt say whether the LAN is up. Serving
// starts either way: binding 0.0.0.0 does not need an address yet.
void wait_for_network() {
    Result rc = nifmInitialize(NifmServiceType_User);
    char line[96];
    snprintf(line, sizeof line, "init nifm:u rc=0x%x", rc);
    write_log(line);
    if (R_FAILED(rc)) {
        fail("nifm", rc);
        return;
    }
    const u64 until = uptime_ms() + 120000;
    for (unsigned attempt = 1; uptime_ms() < until; attempt++) {
        g_boot.nifm_attempts = attempt;
        u32 ip = 0;
        if (R_SUCCEEDED(nifmGetCurrentIpAddress(&ip)) && ip) {
            g_boot.ip = ntohl(ip);
            snprintf(line, sizeof line, "network up after %u attempt(s)", attempt);
            write_log(line);
            write_status();
            return;
        }
        if (sr::log_attempt(attempt)) write_status();
        svcSleepThread(sr::retry_delay_ms(attempt) * 1'000'000ull);
    }
    write_log("no IP after 120 s; serving anyway (status.txt keeps updating)");
}

// Socket init, retried with a backoff instead of giving up once.
void init_sockets() {
    // r5: Nintendo's default UDP sizes (0x2400/0xA500) and fixed TCP buffers, as sys-autopilot runs them on hardware
    // (source/main.c kSocketConfig; studied only). Transfer memory: 2 x 0x25000 = 296 KiB. r1-r3 sent UDP 0/0.
    static const SocketInitConfig sock = {
        .tcp_tx_buf_size = 0x8000, .tcp_rx_buf_size = 0x10000, .tcp_tx_buf_max_size = 0,
        .tcp_rx_buf_max_size = 0, .udp_tx_buf_size = 0x2400, .udp_rx_buf_size = 0xA500,
        .sb_efficiency = 2, .num_bsd_sessions = 4, .bsd_service_type = BsdServiceType_User};
    for (unsigned attempt = 1;; attempt++) {
        g_boot.socket_attempts = attempt;
        Result rc = socketInitialize(&sock);
        if (R_SUCCEEDED(rc)) {
            write_log("init sockets ok");
            return;
        }
        fail("socket", rc);
        if (sr::log_attempt(attempt)) {
            char line[96];
            snprintf(line, sizeof line, "init sockets rc=0x%x (attempt %u); retrying", rc, attempt);
            write_log(line);
            write_status();
        }
        svcSleepThread(sr::retry_delay_ms(attempt) * 1'000'000ull);
    }
}

Thread g_pad;

}  // namespace

int main(int, char**) {
    open_sd();
    {
        char line[128];
        snprintf(line, sizeof line, "boot breadcrumb: switch-remote %.*s uptime_ms=%llu boot_window=%d",
                 int(sr::kSysmoduleVersion.size()), sr::kSysmoduleVersion.data(), (unsigned long long)uptime_ms(),
                 int(sr::boot_delay_needed(uptime_ms())));
        write_log(line);
    }
    stage("sd");
    // At boot, wait before touching more services; a runtime start (ovlSysmodules, RESTART) does not
    // (core boot_delay_needed).
    if (sr::boot_delay_needed(uptime_ms())) {
        stage("boot_delay");
        svcSleepThread(kBootDelayNs);
    }
    stage("services");
    init_services();
    init_power();
    stage("network");
    wait_for_network();
    stage("sockets");
    init_sockets();
    stage("config");
    bool said = false;
    while (!load_config()) {
        if (!said) write_log("disabled: no valid config.txt (checked every 10 s)");
        said = true;
        g_boot.config_ok = false;
        write_status();
        svcSleepThread(10'000'000'000ull);
    }
    g_boot.config_ok = true;
    g_boot.port = g_cfg.port;
    stage("listen");
    static NxPlatform platform;
    static sr::Service service(platform, g_cfg);
    g_platform = &platform;
    g_service = &service;
    service.sweep_stage();  // a hard kill (ovlSysmodules) can interrupt a PUT
    if (R_SUCCEEDED(threadCreate(&g_pad, pad_thread, nullptr, nullptr, 0x8000, 0x2c, -2))) threadStart(&g_pad);
    else write_log("pad thread failed");
    char line[96];
    snprintf(line, sizeof line, "serving port=%u scene_port=%u inject=%d", g_cfg.port, g_cfg.port + 1, int(g_cfg.inject));
    write_log(line);
    sr::Action action = serve();
    g_pad_stop = true;
    threadWaitForExit(&g_pad);
    threadClose(&g_pad);
    platform.hdls_detach();  // no phantom controller after RESTART or SHUTDOWN
    if (action == sr::Action::Restart) {
        // A fresh instance of this program (pm and the loader allow a second instance of a non-application
        // program: AMS pm_process_manager.cpp:131 checks applications only; ldr PinProgram has no uniqueness
        // check, ldr_process_creation.cpp:918-921). It loads the exefs.nsp now on the card; this one then exits.
        NcmProgramLocation loc = {};
        loc.program_id = sr::kOwnProgramId;
        loc.storageID = NcmStorageId_None;
        u64 pid = 0;
        Result rc = pmshellLaunchProgram(0, &loc, &pid);
        snprintf(line, sizeof line, "restart: launched pid=%llu rc=0x%x; exiting", (unsigned long long)pid, rc);
        write_log(line);
    } else {
        write_log("shutdown: exiting");
    }
    stage(action == sr::Action::Restart ? "restarting" : "shutdown");
    socketExit();
    return 0;
}
