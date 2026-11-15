// switch-remote sysmodule: LAN server, virtual Pro Controller (hid:dbg HDLS), launch/terminate of
// Smash Ultimate, and read-only access to crash and plugin logs. DESIGN.md §1-§4, §8.
//
// Boot safety (DESIGN.md §8): a static 1 MiB heap, no service is touched for 10 s after start, and
// nothing calls fatalThrow: every failure is logged to sd:/ultimate/switch-remote/sysmodule.log
// and the module idles. Recovery: delete atmosphere/contents/4200000000005352/ in hekate UMS.
#include <arpa/inet.h>
#include <dirent.h>
#include <netinet/in.h>
#include <poll.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <switch.h>
#include <unistd.h>

#include "core.hpp"

using namespace sr;

// --- libnx process setup ------------------------------------------------------------------------
extern "C" {
u32 __nx_applet_type = AppletType_None;
u32 __nx_fs_num_sessions = 1;

#define INNER_HEAP_SIZE 0x100000
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

constexpr u64 kSsbu = 0x01006A800016E000ull;
constexpr const char* kDir = "sdmc:/ultimate/switch-remote";
constexpr const char* kLog = "sdmc:/ultimate/switch-remote/sysmodule.log";
constexpr const char* kConfig = "sdmc:/ultimate/switch-remote/config.txt";
constexpr long kLogMax = 256 * 1024;
constexpr u64 kBootDelayNs = 10'000'000'000ull;
constexpr const char* kVersion = "0.1.0";

bool g_sd = false;

void logf(const char* fmt, ...) {
    if (!g_sd) return;
    mkdir("sdmc:/ultimate", 0777);
    mkdir(kDir, 0777);
    struct stat st;
    const char* mode = (stat(kLog, &st) == 0 && st.st_size > kLogMax) ? "w" : "a";
    FILE* f = fopen(kLog, mode);
    if (!f) return;
    fprintf(f, "%lu ", (unsigned long)armTicksToNs(armGetSystemTick()) / 1000000);
    va_list ap;
    va_start(ap, fmt);
    vfprintf(f, fmt, ap);
    va_end(ap);
    fputc('\n', f);
    fclose(f);
}

// --- shared state (network thread <-> pad thread) ------------------------------------------------
Mutex g_lock;
Leases g_leases;
Macro g_macro;
SceneReport g_scene;
bool g_inject_enabled = true;

// A fixed system sequence (LAUNCH: HOME then A; DISMISS: A). Steps run in the pad thread.
struct Step {
    u64 buttons;
    u64 ms;
};
Step g_seq[4];
int g_seq_len = 0, g_seq_pos = 0;
u64 g_seq_next = 0;
const char* g_seq_name = "";

// Application state, refreshed by the network thread.
u64 g_app_pid = 0, g_app_program = 0;
unsigned g_app_exits = 0, g_app_launches = 0;
bool g_terminate_requested = false;

// Virtual controller.
bool g_hdls_ready = false, g_hdls_attached = false;
HiddbgHdlsSessionId g_hdls_session;
HiddbgHdlsHandle g_hdls_handle;
alignas(0x1000) u8 g_hdls_work[0x1000];
PadState g_last_sent;
u64 g_last_sent_tick = 0;
const char* g_last_refusal = "";

bool app_running() { return g_app_pid != 0; }

void refresh_app() {
    u64 pid = 0, program = 0;
    if (R_FAILED(pmdmntGetApplicationProcessId(&pid))) pid = 0;
    if (pid && R_FAILED(pminfoGetProgramId(&program, pid))) program = 0;
    mutexLock(&g_lock);
    if (g_app_pid && !pid) {
        g_app_exits++;
        logf("app exit pid=%lu program=%016lx requested=%d", (unsigned long)g_app_pid, (unsigned long)g_app_program,
             g_terminate_requested);
        g_terminate_requested = false;
        g_scene = SceneReport{};
    }
    if (!g_app_pid && pid) logf("app start pid=%lu program=%016lx", (unsigned long)pid, (unsigned long)program);
    g_app_pid = pid;
    g_app_program = program;
    mutexUnlock(&g_lock);
}

bool hdls_attach() {
    if (g_hdls_attached) return true;
    if (!g_hdls_ready) {
        Result rc = hiddbgAttachHdlsWorkBuffer(&g_hdls_session, g_hdls_work, sizeof g_hdls_work);
        if (R_FAILED(rc)) {
            logf("hdls work buffer failed rc=0x%x", rc);
            return false;
        }
        g_hdls_ready = true;
    }
    HiddbgHdlsDeviceInfo info = {};
    info.deviceType = HidDeviceType_FullKey3;
    info.npadInterfaceType = HidNpadInterfaceType_Bluetooth;
    info.singleColorBody = RGBA8_MAXALPHA(0x30, 0x30, 0x30);
    info.singleColorButtons = RGBA8_MAXALPHA(0xff, 0xff, 0xff);
    info.colorLeftGrip = info.colorRightGrip = RGBA8_MAXALPHA(0x30, 0x30, 0x30);
    Result rc = hiddbgAttachHdlsVirtualDevice(&g_hdls_handle, &info);
    if (R_FAILED(rc)) {
        logf("hdls attach failed rc=0x%x", rc);
        return false;
    }
    g_hdls_attached = true;
    logf("hdls attached");
    return true;
}

void hdls_detach() {
    if (!g_hdls_attached) return;
    hiddbgDetachHdlsVirtualDevice(g_hdls_handle);
    g_hdls_attached = false;
    g_last_sent = PadState{};
    logf("hdls detached");
}

void hdls_send(const PadState& p) {
    HiddbgHdlsState s = {};
    s.battery_level = 4;
    s.flags = 1;  // powered
    // Bits 0..15 only: hid derives the stick-direction bits, and masks bits 20..27 on 9.0+ (hiddbg.h).
    s.buttons = p.buttons & kInjectableMask;
    s.analog_stick_l.x = p.lx;
    s.analog_stick_l.y = p.ly;
    s.analog_stick_r.x = p.rx;
    s.analog_stick_r.y = p.ry;
    hiddbgSetHdlsState(g_hdls_handle, &s);
}

// The pad thread: every millisecond, the state due now goes to the virtual controller.
void pad_thread(void*) {
    const u64 freq = armGetSystemTickFreq();
    while (true) {
        svcSleepThread(1'000'000);
        u64 now = armGetSystemTick();
        PadState want;
        bool active = false;
        mutexLock(&g_lock);
        if (g_seq_len) {
            // System sequence: allowed only while no application runs (core: system_sequence_allowed).
            if (g_seq_pos == 0 && g_seq_next == 0 && app_running()) {
                logf("sequence %s refused: application_running", g_seq_name);
                g_seq_len = 0;
            } else {
                if (now >= g_seq_next) {
                    if (g_seq_pos >= g_seq_len) {
                        g_seq_len = 0;
                        g_seq_pos = 0;
                        g_seq_next = 0;
                    } else {
                        g_seq_next = now + armNsToTicks(g_seq[g_seq_pos].ms * 1'000'000ull);
                        g_seq_pos++;
                    }
                }
                if (g_seq_len && g_seq_pos > 0) want.buttons = g_seq[g_seq_pos - 1].buttons;
                active = g_seq_len != 0;
            }
        } else {
            Gate g = gate(g_scene, now, freq);
            bool wants_input = g_macro.phase == MacroPhase::Playing || !(g_leases.state(now) == PadState{});
            if (wants_input && (g != Gate::Allowed || !g_inject_enabled)) {
                const char* why = g_inject_enabled ? gate_name(g) : "inject_off";
                if (g_macro.phase == MacroPhase::Playing) g_macro.stop(why);
                g_leases.reset();
                if (strcmp(g_last_refusal, why) != 0) logf("input refused: %s", why);
                g_last_refusal = why;
            } else if (wants_input) {
                want = g_macro.phase == MacroPhase::Playing ? g_macro.tick(now, freq) : g_leases.state(now);
                active = true;
            }
        }
        bool attached = g_hdls_attached;
        mutexUnlock(&g_lock);
        if (active && !attached) {
            mutexLock(&g_lock);
            hdls_attach();
            attached = g_hdls_attached;
            mutexUnlock(&g_lock);
        }
        if (!attached) continue;
        // Re-send unchanged states every 50 ms so the controller never looks idle to hid.
        if (!(want == g_last_sent) || now - g_last_sent_tick > armNsToTicks(50'000'000)) {
            hdls_send(want);
            g_last_sent = want;
            g_last_sent_tick = now;
        }
    }
}

// --- network ---------------------------------------------------------------------------------
struct Conn {
    int fd = -1;
    bool authed = false;
    char buf[kLineMax];
    size_t len = 0;
    bool scene = false;
};

Config g_cfg;
char g_file_buf[kFileChunkMax];

void close_conn(Conn& c) {
    if (c.fd >= 0) close(c.fd);
    c = Conn{};
}

bool send_all(int fd, const void* data, size_t n) {
    const char* p = static_cast<const char*>(data);
    while (n) {
        ssize_t w = send(fd, p, n, 0);
        if (w <= 0) return false;
        p += w;
        n -= size_t(w);
    }
    return true;
}

bool reply(Conn& c, std::string_view s) {
    return send_all(c.fd, s.data(), s.size()) && send_all(c.fd, "\n", 1);
}

bool reply_err(Conn& c, const char* code, const char* detail = nullptr) {
    LineBuf<192> b;
    b.put("ERR ");
    b.put(code);
    if (detail) {
        b.put(" ");
        b.put(detail);
    }
    return reply(c, b.view());
}

void root_path(Root r, std::string_view rel, char* out, size_t cap) {
    snprintf(out, cap, "sdmc:%.*s%s%.*s", int(kRootPaths[size_t(r)].size()), kRootPaths[size_t(r)].data(),
             rel.empty() ? "" : "/", int(rel.size()), rel.data());
}

bool do_list(Conn& c, const Request& r) {
    char path[256];
    root_path(r.root, r.text, path, sizeof path);
    DIR* d = opendir(path);
    if (!d) return reply_err(c, "not_found");
    // Two passes keep memory fixed: count, then stream.
    unsigned n = 0;
    while (readdir(d)) n++;
    rewinddir(d);
    LineBuf<64> head;
    head.put("OK list ");
    head.put_u64(n);
    if (!reply(c, head.view())) {
        closedir(d);
        return false;
    }
    unsigned sent = 0;
    while (struct dirent* e = readdir(d)) {
        if (sent++ >= n) break;
        char full[512];
        snprintf(full, sizeof full, "%s/%s", path, e->d_name);
        struct stat st = {};
        stat(full, &st);
        LineBuf<320> line;
        line.put(S_ISDIR(st.st_mode) ? "d " : "f ");
        line.put_u64(u64(st.st_size));
        line.put(" ");
        line.put_u64(u64(st.st_mtime));
        line.put(" ");
        line.put(e->d_name);
        if (!reply(c, line.view())) {
            closedir(d);
            return false;
        }
    }
    while (sent++ < n) reply(c, "- 0 0 ?");  // the directory shrank between the passes
    closedir(d);
    return true;
}

bool do_file(Conn& c, const Request& r) {
    char path[256];
    root_path(r.root, r.text, path, sizeof path);
    FILE* f = fopen(path, "rb");
    if (!f) return reply_err(c, "not_found");
    fseek(f, 0, SEEK_END);
    long size = ftell(f);
    size_t n = 0;
    if (r.offset < u64(size)) {
        fseek(f, long(r.offset), SEEK_SET);
        n = fread(g_file_buf, 1, r.max, f);
    }
    fclose(f);
    LineBuf<64> head;
    head.put("OK file ");
    head.put_u64(u64(size));
    head.put(" ");
    head.put_u64(n);
    return reply(c, head.view()) && send_all(c.fd, g_file_buf, n);
}

void start_sequence(const char* name, const Step* steps, int n) {
    for (int i = 0; i < n; i++) g_seq[i] = steps[i];
    g_seq_len = n;
    g_seq_pos = 0;
    g_seq_next = 0;
    g_seq_name = name;
}

bool status_line(Conn& c) {
    u64 now = armGetSystemTick(), freq = armGetSystemTickFreq();
    mutexLock(&g_lock);
    Gate g = gate(g_scene, now, freq);
    LineBuf<384> b;
    b.put("OK status version=");
    b.put(kVersion);
    b.put(" app=");
    b.put(g_app_pid ? "running" : "none");
    b.put(" pid=");
    b.put_u64(g_app_pid);
    b.put(" program=");
    b.put_hex(g_app_program);
    b.put(" exits=");
    b.put_u64(g_app_exits);
    b.put(" launches=");
    b.put_u64(g_app_launches);
    b.put(" scene_mode=");
    b.put_hex(g_scene.mode);
    b.put(" scene_deluxe=");
    b.put(g_scene.deluxe_online < 0 ? "-1" : g_scene.deluxe_online ? "1" : "0");
    b.put(" scene_age_ms=");
    b.put_u64(g_scene.valid ? (now - g_scene.received_tick) * 1000 / freq : 0);
    b.put(" gate=");
    b.put(g_inject_enabled ? gate_name(g) : "inject_off");
    b.put(" hdls=");
    b.put(g_hdls_attached ? "attached" : "detached");
    b.put(" macro=");
    b.put(phase_name(g_macro.phase));
    b.put(" sequence=");
    b.put(g_seq_len ? g_seq_name : "none");
    mutexUnlock(&g_lock);
    return reply(c, b.view());
}

// Handles one request line; false closes the connection.
bool handle(Conn& c, std::string_view line) {
    Request r;
    if (const char* e = parse_request(line, r)) return reply_err(c, e);
    if (c.scene) {
        if (!c.authed) {
            if (r.method != Method::Auth || !token_eq(r.text, g_cfg.token_view())) return false;
            c.authed = true;
            return true;  // the scene channel gets no replies
        }
        if (r.method != Method::Scene) return false;
        mutexLock(&g_lock);
        r.scene.received_tick = armGetSystemTick();
        g_scene = r.scene;
        mutexUnlock(&g_lock);
        return true;
    }
    if (!c.authed) {
        if (r.method != Method::Auth || !token_eq(r.text, g_cfg.token_view())) {
            reply_err(c, "auth");
            return false;
        }
        c.authed = true;
        return reply(c, "OK auth switch-remote sysmodule");
    }
    const u64 now = armGetSystemTick(), freq = armGetSystemTickFreq();
    switch (r.method) {
        case Method::Auth: return reply(c, "OK auth");
        case Method::Scene: return reply_err(c, "unknown_method");
        case Method::Ping: {
            LineBuf<64> b;
            b.put("OK pong ");
            b.put_u64(armTicksToNs(now) / 1000000);
            return reply(c, b.view());
        }
        case Method::Status: return status_line(c);
        case Method::Button:
        case Method::Stick: {
            mutexLock(&g_lock);
            Gate g = gate(g_scene, now, freq);
            const char* refusal = !g_inject_enabled ? "inject_off"
                                  : g != Gate::Allowed ? gate_name(g)
                                  : g_macro.phase == MacroPhase::Playing ? "input_busy"
                                  : g_seq_len ? "sequence_busy" : nullptr;
            if (!refusal) {
                if (r.method == Method::Stick) {
                    g_leases.set_stick(r.stick, r.x, r.y, lease_deadline(now, r.ms, freq));
                } else if (r.state == BtnState::Release) {
                    g_leases.release(int(r.bit));
                } else {
                    g_leases.press(int(r.bit), lease_deadline(now, r.ms, freq));
                }
            }
            mutexUnlock(&g_lock);
            return refusal ? reply_err(c, "inject_refused", refusal) : reply(c, "OK");
        }
        case Method::Reset: {
            mutexLock(&g_lock);
            g_leases.reset();
            g_macro.stop(nullptr);
            mutexUnlock(&g_lock);
            return reply(c, "OK");
        }
        case Method::MacroBegin:
        case Method::MacroEvent:
        case Method::MacroPlay: {
            mutexLock(&g_lock);
            const char* e = nullptr;
            if (r.method == Method::MacroBegin) e = g_macro.begin(r.n, r.duration);
            else if (r.method == Method::MacroEvent) e = g_macro.add(r.sample);
            else {
                Gate g = gate(g_scene, now, freq);
                e = !g_inject_enabled ? "inject_off" : g != Gate::Allowed ? gate_name(g) : g_seq_len ? "sequence_busy" : nullptr;
                if (!e) {
                    g_leases.reset();
                    e = g_macro.play(now);
                }
            }
            mutexUnlock(&g_lock);
            if (r.method == Method::MacroEvent && !e) return true;  // samples are not acknowledged one by one
            return e ? reply_err(c, r.method == Method::MacroPlay ? "inject_refused" : e, e) : reply(c, "OK");
        }
        case Method::MacroStatus: {
            mutexLock(&g_lock);
            LineBuf<160> b;
            b.put("OK macro state=");
            b.put(phase_name(g_macro.phase));
            b.put(" frame=");
            b.put_u64(g_macro.phase == MacroPhase::Playing ? g_macro.frame_at(now, freq) : 0);
            b.put(" duration=");
            b.put_u64(g_macro.duration);
            b.put(" loaded=");
            b.put_u64(g_macro.loaded);
            b.put(" error=");
            b.put(g_macro.error ? g_macro.error : "");
            mutexUnlock(&g_lock);
            return reply(c, b.view());
        }
        case Method::MacroStop: {
            mutexLock(&g_lock);
            g_macro.stop(nullptr);
            mutexUnlock(&g_lock);
            return reply(c, "OK");
        }
        case Method::Launch:
        case Method::Dismiss: {
            refresh_app();
            mutexLock(&g_lock);
            const char* e = system_sequence_allowed(app_running(), g_seq_len != 0);
            if (!e) {
                static const Step launch[] = {{HiddbgNpadButton_Home, 120}, {0, 1500}, {HidNpadButton_A, 120}, {0, 200}};
                static const Step dismiss[] = {{HidNpadButton_A, 120}, {0, 200}};
                g_leases.reset();
                g_macro.stop(nullptr);
                if (r.method == Method::Launch) {
                    start_sequence("launch", launch, 4);
                    g_app_launches++;
                } else {
                    start_sequence("dismiss", dismiss, 2);
                }
            }
            mutexUnlock(&g_lock);
            logf("%s %s", r.method == Method::Launch ? "launch" : "dismiss", e ? e : "started");
            return e ? reply_err(c, "refused", e) : reply(c, "OK started");
        }
        case Method::Terminate:
        case Method::Relaunch: {
            refresh_app();
            if (!app_running()) {
                if (r.method == Method::Terminate) return reply(c, "OK not_running");
            } else {
                mutexLock(&g_lock);
                g_terminate_requested = true;
                g_leases.reset();
                g_macro.stop(nullptr);
                u64 pid = g_app_pid;
                mutexUnlock(&g_lock);
                Result rc = nsdevTerminateApplication();
                if (R_FAILED(rc)) {
                    logf("nsdev terminate rc=0x%x, trying pgl", rc);
                    rc = pglTerminateProcess(pid);
                }
                logf("terminate pid=%lu rc=0x%x", (unsigned long)pid, rc);
                if (R_FAILED(rc)) return reply_err(c, "terminate_failed");
                for (int i = 0; i < 100 && app_running(); i++) {  // up to 10 s for the process to go
                    svcSleepThread(100'000'000);
                    refresh_app();
                }
                if (app_running()) return reply_err(c, "terminate_timeout");
                if (r.method == Method::Terminate) return reply(c, "OK terminated");
                svcSleepThread(1'000'000'000);  // let the HOME menu settle
            }
            return handle(c, "LAUNCH");
        }
        case Method::Detach: {
            mutexLock(&g_lock);
            g_leases.reset();
            g_macro.stop(nullptr);
            hdls_detach();
            mutexUnlock(&g_lock);
            return reply(c, "OK");
        }
        case Method::List: return do_list(c, r);
        case Method::File: return do_file(c, r);
        case Method::Bye: reply(c, "OK bye"); return false;
    }
    return reply_err(c, "unknown_method");
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
    bool ok = scene ? (ip >> 24) == 127 : peer_allowed(g_cfg, ip);
    if (!ok || c.fd >= 0) {  // one client per channel; others are closed at once
        logf("%s peer %u.%u.%u.%u refused (%s)", scene ? "scene" : "host", ip >> 24, (ip >> 16) & 255, (ip >> 8) & 255,
             ip & 255, ok ? "busy" : "not_private");
        close(fd);
        return;
    }
    c = Conn{};
    c.fd = fd;
    c.scene = scene;
    if (!scene) logf("host peer %u.%u.%u.%u connected", ip >> 24, (ip >> 16) & 255, (ip >> 8) & 255, ip & 255);
}

// Reads what is available; handles complete lines. False: the peer closed or broke the protocol.
bool pump(Conn& c) {
    ssize_t n = recv(c.fd, c.buf + c.len, sizeof c.buf - c.len, 0);
    if (n <= 0) return false;
    c.len += size_t(n);
    while (true) {
        char* nl = static_cast<char*>(memchr(c.buf, '\n', c.len));
        if (!nl) break;
        size_t line_len = size_t(nl - c.buf);
        if (!handle(c, std::string_view(c.buf, line_len))) return false;
        if (c.fd < 0) return false;
        memmove(c.buf, nl + 1, c.len - line_len - 1);
        c.len -= line_len + 1;
    }
    if (c.len == sizeof c.buf) {
        reply_err(c, "too_long");
        return false;
    }
    return true;
}

bool load_config() {
    FILE* f = fopen(kConfig, "rb");
    if (!f) return false;
    static char text[2048];
    size_t n = fread(text, 1, sizeof text - 1, f);
    fclose(f);
    const char* e = parse_config(std::string_view(text, n), g_cfg);
    if (e) {
        logf("config invalid: %s", e);
        return false;
    }
    return true;
}

void serve() {
    int host_l = -1, scene_l = -1;
    Conn host, scene;
    u64 last_app_poll = 0;
    while (true) {
        if (host_l < 0) host_l = listen_on(INADDR_ANY, g_cfg.port);
        if (scene_l < 0) scene_l = listen_on(INADDR_LOOPBACK, u16(g_cfg.port + 1));
        if (host_l < 0 || scene_l < 0) {
            logf("listen failed host=%d scene=%d errno=%d; retrying", host_l, scene_l, errno);
            svcSleepThread(5'000'000'000ull);
            continue;
        }
        pollfd p[4];
        int n = 0;
        p[n++] = {host_l, POLLIN, 0};
        p[n++] = {scene_l, POLLIN, 0};
        int hi = -1, si = -1;
        if (host.fd >= 0) {
            hi = n;
            p[n++] = {host.fd, POLLIN, 0};
        }
        if (scene.fd >= 0) {
            si = n;
            p[n++] = {scene.fd, POLLIN, 0};
        }
        int ready = poll(p, nfds_t(n), 250);
        if (armTicksToNs(armGetSystemTick() - last_app_poll) > 500'000'000ull) {
            refresh_app();
            last_app_poll = armGetSystemTick();
        }
        if (ready <= 0) continue;
        if (p[0].revents & POLLIN) accept_into(host_l, host, false);
        if (p[1].revents & POLLIN) accept_into(scene_l, scene, true);
        if (hi >= 0 && p[hi].revents && !pump(host)) {
            logf("host peer closed");
            close_conn(host);
        }
        if (si >= 0 && p[si].revents && !pump(scene)) close_conn(scene);
    }
}

Thread g_pad;

bool init_services() {
    Result rc;
    if (R_FAILED(rc = smInitialize())) return false;  // nothing to log to yet
    if (R_SUCCEEDED(fsInitialize()) && R_SUCCEEDED(fsdevMountSdmc())) g_sd = true;
    logf("switch-remote sysmodule %s starting", kVersion);
    if (R_SUCCEEDED(rc = setsysInitialize())) {
        SetSysFirmwareVersion fw;
        if (R_SUCCEEDED(setsysGetFirmwareVersion(&fw))) hosversionSet(MAKEHOSVERSION(fw.major, fw.minor, fw.micro));
        setsysExit();
    }
    struct {
        const char* name;
        Result rc;
    } svc[] = {{"hid:dbg", hiddbgInitialize()},
               {"pm:dmnt", pmdmntInitialize()},
               {"pm:info", pminfoInitialize()},
               {"ns:dev", nsdevInitialize()},
               {"pgl", pglInitialize()}};
    for (auto& s : svc)
        if (R_FAILED(s.rc)) logf("service %s failed rc=0x%x", s.name, s.rc);
    static const SocketInitConfig sock = {
        .tcp_tx_buf_size = 0x4000, .tcp_rx_buf_size = 0x2000, .tcp_tx_buf_max_size = 0x10000,
        .tcp_rx_buf_max_size = 0x4000, .udp_tx_buf_size = 0, .udp_rx_buf_size = 0,
        .sb_efficiency = 2, .num_bsd_sessions = 2, .bsd_service_type = BsdServiceType_User};
    if (R_FAILED(rc = socketInitialize(&sock))) {
        logf("socket init failed rc=0x%x", rc);
        return false;
    }
    return true;
}

}  // namespace

int main(int, char**) {
    svcSleepThread(kBootDelayNs);
    if (!init_services()) {
        logf("init failed; idling");
        while (true) svcSleepThread(60'000'000'000ull);
    }
    while (!load_config()) {
        static bool said = false;
        if (!said) logf("disabled: no valid %s (checked every 10 s)", kConfig);
        said = true;
        svcSleepThread(10'000'000'000ull);
    }
    g_inject_enabled = g_cfg.inject;
    mutexInit(&g_lock);
    if (R_SUCCEEDED(threadCreate(&g_pad, pad_thread, nullptr, nullptr, 0x4000, 0x2c, -2))) threadStart(&g_pad);
    else logf("pad thread failed");
    logf("serving port=%u scene_port=%u inject=%d", g_cfg.port, g_cfg.port + 1, g_cfg.inject);
    serve();
    return 0;
}
