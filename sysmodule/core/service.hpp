// switch-remote sysmodule: the request handler and the pad schedule, independent of libnx.
// The console build supplies a libnx Platform (source/main.cpp); the host loopback server supplies
// a fake one (host_loopback/loopback.cpp), so the same code is tested end to end on the host.
#pragma once
#include "core.hpp"
#include "sha256.hpp"

namespace sr {

// What the handler needs from the system. All calls come from the network thread except
// lock/unlock/now/freq/hdls_*/log, which the pad thread also uses.
struct Platform {
    virtual ~Platform() = default;
    virtual uint64_t now() = 0;
    virtual uint64_t freq() = 0;
    virtual void lock() = 0;
    virtual void unlock() = 0;
    virtual void log(const char* line) = 0;
    // The running application, if any (pid 0 = none).
    virtual void app(uint64_t& pid, uint64_t& program) = 0;
    // The application records, newest first as ns returns them (ns:am2 ListApplicationRecord); -1 on failure.
    virtual long app_records(AppRecord* out, size_t max) = 0;
    // Terminates the application; 0 on success.
    virtual uint32_t terminate(uint64_t pid) = 0;
    virtual void sleep_ms(uint64_t ms) = 0;
    // Virtual controller.
    virtual bool hdls_attach() = 0;
    virtual void hdls_detach() = 0;
    virtual void hdls_send(const PadState& p) = 0;  // bits 0..15 + sticks
    // Files: `path` is an absolute SD path built from kRootPaths.
    // list: calls `entry` per item; false when the directory can't be opened.
    struct Entry {
        bool dir;
        uint64_t size, mtime;
        const char* name;
    };
    virtual bool list(const char* path, void (*entry)(void* ctx, const Entry& e), void* ctx) = 0;
    // read: up to `max` bytes at `offset` into buf; total size in `size`; -1 when it can't be opened.
    virtual long read(const char* path, uint64_t offset, char* buf, uint32_t max, uint64_t& size) = 0;
    // Writes `n` bytes at `offset` (offset 0 creates or truncates). Used only for allowlisted update targets.
    virtual bool write_at(const char* path, uint64_t offset, const void* data, size_t n) = 0;
    // Renames `from` over `to` (replacing it), and removes a file; both only on allowlisted paths.
    virtual bool replace(const char* from, const char* to) = 0;
    virtual bool remove(const char* path) = 0;
    virtual bool make_dirs(const char* dir) = 0;
};

// Where replies go (a socket on the console, a buffer in tests).
struct Sink {
    virtual ~Sink() = default;
    virtual bool write(const void* data, size_t n) = 0;
};

struct Session {
    bool authed = false;
    bool scene = false;  // the loopback scene channel from the in-game NRO
};

// What the platform loop must do after a reply (RESTART: launch a fresh instance of this program, then exit;
// SHUTDOWN: exit). Both detach the virtual controller and close every socket first.
enum class Action : uint8_t { None, Restart, Shutdown };

class Service {
public:
    Service(Platform& p, const Config& c) : p_(p), cfg_(c) {}

    // One request line (without LF). False: close the connection.
    bool handle(Session& s, std::string_view line, Sink& out);
    // PUT payload bytes that follow a PUT line; returns how many were consumed. The reply goes out when the last
    // byte arrives (verify, back up, swap, read back). put_pending() is true between the line and the last byte.
    size_t put_feed(const char* data, size_t n, Sink& out);
    bool put_pending() const { return put_.active; }
    // Removes staging files an interrupted PUT left (called at start: a hard kill can interrupt one).
    void sweep_stage();
    Action take_action() {
        Action a = action_;
        action_ = Action::None;
        return a;
    }
    // The host client went away: held input is released and a macro stops, so nothing stays pressed
    // without a client (the same idea as a controller-side watchdog).
    void on_disconnect(const Session& s);
    // One pad-thread step at `now`: returns the state to send and whether the device should be attached.
    bool pad_step(uint64_t now, PadState& want);
    // Polls the application (the network thread calls it every 500 ms and around launch/terminate).
    void refresh_app();

    // Test and status access.
    const SceneReport& scene() const { return scene_; }
    const Macro& macro() const { return macro_; }
    bool attached() const { return attached_; }
    unsigned exits() const { return exits_; }

    // Called by the pad thread with the result of an attach attempt.
    void set_attached(bool a) { attached_ = a; }

    static constexpr uint64_t kSequenceMaxSteps = 4;

private:
    bool reply(Sink& out, std::string_view s);
    bool reply_err(Sink& out, const char* code, const char* detail = nullptr);
    bool status(Sink& out);
    bool do_list(const Request& r, Sink& out);
    bool do_file(const Request& r, Sink& out);
    bool do_records(Sink& out);
    bool put_finish(Sink& out);
    bool hash_file(const char* path, char hex[65], uint64_t& size);
    struct PutState {
        bool active = false;
        char target[256] = {}, stage[272] = {}, expect[65] = {};
        uint64_t size = 0, received = 0;
        Sha256 hash;
    } put_;
    Action action_ = Action::None;
    uint64_t last_active_ = 0;
    char last_put_[200] = "none";
    static constexpr size_t kRecords = 16;
    const char* input_refusal(uint64_t now);
    void root_path(Root r, std::string_view rel, char* out, size_t cap);
    void logf(const char* fmt, ...);

    struct Step {
        uint64_t buttons, ms;
    };
    void start_sequence(const char* name, const Step* steps, int n);

    Platform& p_;
    Config cfg_;
    Leases leases_;
    Macro macro_;
    SceneReport scene_;
    Step seq_[kSequenceMaxSteps] = {};
    int seq_len_ = 0, seq_pos_ = 0;
    uint64_t seq_next_ = 0;
    const char* seq_name_ = "";
    uint64_t app_pid_ = 0, app_program_ = 0;
    unsigned exits_ = 0, launches_ = 0;
    bool terminate_requested_ = false;
    bool attached_ = false;
    const char* last_refusal_ = "";
    char file_buf_[kFileChunkMax];
};

// HidNpadButton / HiddbgNpadButton values the fixed sequences press.
constexpr uint64_t kButtonA = 1u << 0;
constexpr uint64_t kButtonHome = 1u << 18;

}  // namespace sr
