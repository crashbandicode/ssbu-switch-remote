// switch-remote sysmodule: request handler and pad schedule. See service.hpp and docs/PROTOCOL.md.
#include "service.hpp"

#include <cstdarg>
#include <cstdio>
#include <cstring>
#include <string>

namespace sr {

namespace {
struct Guard {
    Platform& p;
    explicit Guard(Platform& pp) : p(pp) { p.lock(); }
    ~Guard() { p.unlock(); }
};
}  // namespace

void Service::logf(const char* fmt, ...) {
    char line[256];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(line, sizeof line, fmt, ap);
    va_end(ap);
    p_.log(line);
}

bool Service::reply(Sink& out, std::string_view s) { return out.write(s.data(), s.size()) && out.write("\n", 1); }

bool Service::reply_err(Sink& out, const char* code, const char* detail) {
    LineBuf<192> b;
    b.put("ERR ");
    b.put(code);
    if (detail) {
        b.put(" ");
        b.put(detail);
    }
    return reply(out, b.view());
}

void Service::refresh_app() {
    uint64_t pid = 0, program = 0;
    p_.app(pid, program);
    Guard g(p_);
    if (app_pid_ && !pid) {
        exits_++;
        logf("app exit pid=%llu program=%016llx requested=%d", (unsigned long long)app_pid_,
             (unsigned long long)app_program_, int(terminate_requested_));
        terminate_requested_ = false;
        scene_ = SceneReport{};
    }
    if (!app_pid_ && pid) logf("app start pid=%llu program=%016llx", (unsigned long long)pid, (unsigned long long)program);
    app_pid_ = pid;
    app_program_ = program;
}

void Service::start_sequence(const char* name, const Step* steps, int n) {
    for (int i = 0; i < n; i++) seq_[i] = steps[i];
    seq_len_ = n;
    seq_pos_ = 0;
    seq_next_ = 0;
    seq_name_ = name;
}

// Called with the lock held.
const char* Service::input_refusal(uint64_t now) {
    if (!cfg_.inject) return "inject_off";
    Gate g = gate(scene_, now, p_.freq());
    if (g != Gate::Allowed) return gate_name(g);
    if (seq_len_) return "sequence_busy";
    return nullptr;
}

bool Service::pad_step(uint64_t now, PadState& want) {
    Guard g(p_);
    want = PadState{};
    if (seq_len_) {
        // A fixed system sequence: only while no application runs (core: system_sequence_allowed).
        last_active_ = now * 1000 / p_.freq();
        if (seq_pos_ == 0 && seq_next_ == 0 && app_pid_) {
            logf("sequence %s refused: application_running", seq_name_);
            seq_len_ = 0;
            return false;
        }
        if (now >= seq_next_) {
            if (seq_pos_ >= seq_len_) {
                seq_len_ = seq_pos_ = 0;
                seq_next_ = 0;
                return attached_;
            }
            seq_next_ = now + seq_[seq_pos_].ms * p_.freq() / 1000;
            seq_pos_++;
        }
        want.buttons = seq_[seq_pos_ - 1].buttons;
        return true;
    }
    bool wants = macro_.phase == MacroPhase::Playing || !(leases_.state(now) == PadState{});
    const uint64_t now_ms = now * 1000 / p_.freq();
    if (!wants) return attached_ && !detach_due(attached_, false, now_ms, last_active_);
    last_active_ = now_ms;
    if (const char* why = input_refusal(now)) {
        if (macro_.phase == MacroPhase::Playing) macro_.stop(why);
        leases_.reset();
        if (std::string_view(last_refusal_) != why) logf("input refused: %s", why);
        last_refusal_ = why;
        return attached_;
    }
    last_refusal_ = "";
    want = macro_.phase == MacroPhase::Playing ? macro_.tick(now, p_.freq()) : leases_.state(now);
    return true;
}

void Service::root_path(Root r, std::string_view rel, char* out, size_t cap) {
    std::string_view base = kRootPaths[size_t(r)];
    snprintf(out, cap, "%.*s%s%.*s", int(base.size()), base.data(), rel.empty() ? "" : "/", int(rel.size()),
             rel.data());
}

bool Service::status(Sink& out) {
    uint64_t now = p_.now(), freq = p_.freq();
    LineBuf<384> b;
    {
        Guard g(p_);
        b.put("OK status version=");
        b.put(kSysmoduleVersion);
        b.put(" app=");
        b.put(app_pid_ ? "running" : "none");
        b.put(" pid=");
        b.put_u64(app_pid_);
        b.put(" program=");
        b.put_hex(app_program_);
        b.put(" exits=");
        b.put_u64(exits_);
        b.put(" launches=");
        b.put_u64(launches_);
        b.put(" scene_mode=");
        b.put_hex(scene_.mode);
        b.put(" scene_deluxe=");
        b.put(scene_.deluxe_online < 0 ? "-1" : scene_.deluxe_online ? "1" : "0");
        b.put(" scene_age_ms=");
        b.put_u64(scene_.valid && now >= scene_.received_tick ? (now - scene_.received_tick) * 1000 / freq : 0);
        b.put(" gate=");
        b.put(cfg_.inject ? gate_name(gate(scene_, now, freq)) : "inject_off");
        b.put(" hdls=");
        b.put(attached_ ? "attached" : "detached");
        b.put(" macro=");
        b.put(phase_name(macro_.phase));
        b.put(" sequence=");
        b.put(seq_len_ ? seq_name_ : "none");
        b.put(" uptime_ms=");
        b.put_u64(now * 1000 / freq);
        b.put(" last_put=");
        b.put(last_put_);
    }
    return reply(out, b.view());
}

namespace {
struct ListCtx {
    Sink* out;
    unsigned n;
    bool ok;
};
void list_entry(void* ctx, const Platform::Entry& e) {
    auto* c = static_cast<ListCtx*>(ctx);
    if (!c->ok) return;
    LineBuf<320> line;
    line.put(e.dir ? "d " : "f ");
    line.put_u64(e.size);
    line.put(" ");
    line.put_u64(e.mtime);
    line.put(" ");
    line.put(e.name);
    line.put("\n");
    c->ok = c->out->write(line.buf, line.len);
    c->n++;
}
void count_entry(void* ctx, const Platform::Entry&) { (*static_cast<unsigned*>(ctx))++; }
}  // namespace

bool Service::do_list(const Request& r, Sink& out) {
    char path[256];
    root_path(r.root, r.text, path, sizeof path);
    unsigned n = 0;
    if (!p_.list(path, count_entry, &n)) return reply_err(out, "not_found");
    LineBuf<64> head;
    head.put("OK list ");
    head.put_u64(n);
    if (!reply(out, head.view())) return false;
    // The listing that follows has exactly n lines; a directory that changed in between is padded or cut.
    struct Bounded {
        ListCtx ctx;
        unsigned limit;
    } b{{&out, 0, true}, n};
    p_.list(path, [](void* ctx, const Platform::Entry& e) {
        auto* bb = static_cast<Bounded*>(ctx);
        if (bb->ctx.n < bb->limit) list_entry(&bb->ctx, e);
    }, &b);
    while (b.ctx.ok && b.ctx.n < n) {
        b.ctx.ok = out.write("- 0 0 ?\n", 8);
        b.ctx.n++;
    }
    return b.ctx.ok;
}

bool Service::do_file(const Request& r, Sink& out) {
    char path[256];
    root_path(r.root, r.text, path, sizeof path);
    uint64_t size = 0;
    long n = p_.read(path, r.offset, file_buf_, r.max, size);
    if (n < 0) return reply_err(out, "not_found");
    LineBuf<64> head;
    head.put("OK file ");
    head.put_u64(size);
    head.put(" ");
    head.put_u64(uint64_t(n));
    return reply(out, head.view()) && out.write(file_buf_, size_t(n));
}

void Service::on_disconnect(const Session& s) {
    if (s.scene) return;
    Guard g(p_);
    bool held = !(leases_.state(p_.now()) == PadState{}) || macro_.phase == MacroPhase::Playing;
    leases_.reset();
    if (macro_.phase == MacroPhase::Playing) macro_.stop("client_disconnected");
    if (held) logf("host disconnected: input released");
}

bool Service::do_records(Sink& out) {
    AppRecord rec[kRecords];
    long n = p_.app_records(rec, kRecords);
    if (n < 0) return reply_err(out, "records_unavailable");
    LineBuf<64> head;
    head.put("OK records ");
    head.put_u64(uint64_t(n));
    const char* tile = first_tile_check(rec, size_t(n), kSsbuProgramId);
    head.put(" first_tile=");
    head.put(tile ? tile : "ssbu");
    if (!reply(out, head.view())) return false;
    for (long i = 0; i < n; i++) {
        LineBuf<96> line;
        line.put_hex(rec[i].id);
        line.put(" ");
        line.put_u64(rec[i].last_event);
        line.put(" ");
        line.put_u64(rec[i].last_updated);
        if (!reply(out, line.view())) return false;
    }
    return true;
}

bool Service::hash_file(const char* path, char hex[65], uint64_t& size) {
    Sha256 h;
    uint64_t off = 0;
    size = 0;
    while (true) {
        long n = p_.read(path, off, file_buf_, kFileChunkMax, size);
        if (n < 0) return false;
        if (n == 0) break;
        h.update(file_buf_, size_t(n));
        off += uint64_t(n);
        if (off >= size) break;
    }
    h.finish_hex(hex);
    return true;
}

void Service::sweep_stage() {
    struct Ctx {
        Platform* p;
        const char* dir;
    };
    auto sweep = [](void* ctx, const Platform::Entry& e) {
        auto* c = static_cast<Ctx*>(ctx);
        if (!e.dir && is_stage_file(e.name)) {
            char path[320];
            snprintf(path, sizeof path, "%s/%s", c->dir, e.name);
            c->p->remove(path);
        }
    };
    char dirs[kUpdateTargets.size() + 1][256];
    size_t n = 0;
    for (const auto& t : kUpdateTargets) {
        size_t slash = t.path.rfind('/');
        snprintf(dirs[n++], 256, "%.*s", int(slash), t.path.data());
    }
    snprintf(dirs[n++], 256, "%.*s", int(kDataDir.size()), kDataDir.data());
    for (size_t i = 0; i < n; i++) {
        Ctx c{&p_, dirs[i]};
        p_.list(dirs[i], sweep, &c);
    }
}

size_t Service::put_feed(const char* data, size_t n, Sink& out) {
    if (!put_.active) return 0;
    uint64_t want = put_.size - put_.received;
    size_t take = n < want ? n : size_t(want);
    if (take && !p_.write_at(put_.stage, put_.received, data, take)) {
        put_.active = false;
        p_.remove(put_.stage);
        reply_err(out, "put_failed", "stage_write");
        return n;  // the rest of this payload is discarded with the connection
    }
    put_.hash.update(data, take);
    put_.received += take;
    if (put_.received == put_.size) put_finish(out);
    return take;
}

bool Service::put_finish(Sink& out) {
    put_.active = false;
    char got[65];
    put_.hash.finish_hex(got);
    if (std::string_view(got) != put_.expect) {
        p_.remove(put_.stage);
        logf("put %s refused: sha %s, expected %s", put_.target, got, put_.expect);
        snprintf(last_put_, sizeof last_put_, "failed_sha");
        return reply_err(out, "put_failed", "sha_mismatch");
    }
    // The stage reads back as sent before anything is replaced.
    char staged[65];
    uint64_t sz = 0;
    if (!hash_file(put_.stage, staged, sz) || std::string_view(staged) != put_.expect || sz != put_.size) {
        p_.remove(put_.stage);
        snprintf(last_put_, sizeof last_put_, "failed_stage_readback");
        return reply_err(out, "put_failed", "stage_readback");
    }
    char old[65] = "none", backup[300] = "none";
    uint64_t old_size = 0;
    bool had_old = hash_file(put_.target, old, old_size);
    if (had_old) {
        if (std::string_view(old) == put_.expect) {
            p_.remove(put_.stage);
            snprintf(last_put_, sizeof last_put_, "unchanged %.16s", old);
            return reply(out, "OK put unchanged");
        }
        backup_path(put_.target, put_.expect, backup, sizeof backup);
        p_.make_dirs(std::string(kBackupDir).c_str());
        uint64_t off = 0, total = 0;
        bool ok = true;
        while (ok) {
            long n = p_.read(put_.target, off, file_buf_, kFileChunkMax, total);
            if (n <= 0) break;
            ok = p_.write_at(backup, off, file_buf_, size_t(n));
            off += uint64_t(n);
            if (off >= total) break;
        }
        char bk[65];
        uint64_t bsz = 0;
        if (!ok || !hash_file(backup, bk, bsz) || std::string_view(bk) != old) {
            p_.remove(put_.stage);
            snprintf(last_put_, sizeof last_put_, "failed_backup");
            return reply_err(out, "put_failed", "backup");
        }
    }
    if (!p_.replace(put_.stage, put_.target)) {
        p_.remove(put_.stage);
        snprintf(last_put_, sizeof last_put_, "failed_swap");
        return reply_err(out, "put_failed", "swap");
    }
    char after[65];
    uint64_t asz = 0;
    bool readback = hash_file(put_.target, after, asz) && std::string_view(after) == put_.expect;
    if (!readback && had_old) {
        // Put the old file back from its backup and say so.
        char tmp[300];
        snprintf(tmp, sizeof tmp, "%s.sr-stage", put_.target);
        uint64_t off = 0, total = 0;
        while (true) {
            long n = p_.read(backup, off, file_buf_, kFileChunkMax, total);
            if (n <= 0) break;
            p_.write_at(tmp, off, file_buf_, size_t(n));
            off += uint64_t(n);
            if (off >= total) break;
        }
        p_.replace(tmp, put_.target);
    }
    logf("put %s old=%s new=%s backup=%s readback=%d", put_.target, old, put_.expect, backup, int(readback));
    snprintf(last_put_, sizeof last_put_, "%s_%.16s", readback ? "ok" : "failed_readback", put_.expect);
    if (!readback) return reply_err(out, "put_failed", "readback_restored");
    LineBuf<512> b;
    b.put("OK put old=");
    b.put(old);
    b.put(" new=");
    b.put(put_.expect);
    b.put(" backup=");
    b.put(backup);
    b.put(" readback=ok");
    return reply(out, b.view());
}

bool Service::handle(Session& s, std::string_view line, Sink& out) {
    Request r;
    if (const char* e = parse_request(line, r)) {
        if (s.scene) return false;
        return reply_err(out, e);
    }
    if (s.scene) {
        if (!s.authed) {
            if (r.method != Method::Auth || !token_eq(r.text, cfg_.token_view())) return false;
            s.authed = true;
            return true;  // the scene channel gets no replies
        }
        if (r.method != Method::Scene) return false;
        Guard g(p_);
        r.scene.received_tick = p_.now();
        scene_ = r.scene;
        return true;
    }
    if (!s.authed) {
        if (r.method != Method::Auth || !token_eq(r.text, cfg_.token_view())) {
            reply_err(out, "auth");
            return false;
        }
        s.authed = true;
        return reply(out, "OK auth switch-remote");
    }
    const uint64_t now = p_.now(), freq = p_.freq();
    switch (r.method) {
        case Method::Auth: return reply(out, "OK auth");
        case Method::Scene: return reply_err(out, "unknown_method");
        case Method::Ping: {
            LineBuf<64> b;
            b.put("OK pong ");
            b.put_u64(now * 1000 / freq);
            return reply(out, b.view());
        }
        case Method::Status: return status(out);
        case Method::Button:
        case Method::Stick: {
            const char* refusal;
            {
                Guard g(p_);
                refusal = input_refusal(now);
                if (!refusal && macro_.phase == MacroPhase::Playing) refusal = "input_busy";
                if (!refusal) {
                    if (r.method == Method::Stick) leases_.set_stick(r.stick, r.x, r.y, lease_deadline(now, r.ms, freq));
                    else if (r.state == BtnState::Release) leases_.release(int(r.bit));
                    else leases_.press(int(r.bit), lease_deadline(now, r.ms, freq));
                }
            }
            return refusal ? reply_err(out, "inject_refused", refusal) : reply(out, "OK");
        }
        case Method::Reset: {
            Guard g(p_);
            leases_.reset();
            macro_.stop(nullptr);
            return reply(out, "OK");
        }
        case Method::MacroBegin: {
            const char* e;
            {
                Guard g(p_);
                e = macro_.begin(r.n, r.duration);
            }
            return e ? reply_err(out, e) : reply(out, "OK");
        }
        case Method::MacroEvent: {
            const char* e;
            {
                Guard g(p_);
                e = macro_.add(r.sample);
            }
            return e ? reply_err(out, e) : true;  // samples are acknowledged only on error
        }
        case Method::MacroPlay: {
            const char* e;
            {
                Guard g(p_);
                e = input_refusal(now);
                if (!e) {
                    leases_.reset();
                    if (const char* pe = macro_.play(now)) return reply_err(out, pe);
                }
            }
            return e ? reply_err(out, "inject_refused", e) : reply(out, "OK");
        }
        case Method::MacroStatus: {
            LineBuf<192> b;
            Guard g(p_);
            if (macro_.phase == MacroPhase::Playing) macro_.tick(now, freq);  // reach Finished on time
            b.put("OK macro state=");
            b.put(phase_name(macro_.phase));
            b.put(" frame=");
            b.put_u64(macro_.phase == MacroPhase::Playing ? macro_.frame_at(now, freq) : 0);
            b.put(" duration=");
            b.put_u64(macro_.duration);
            b.put(" loaded=");
            b.put_u64(macro_.loaded);
            b.put(" error=");
            b.put(macro_.error ? macro_.error : "");
            return reply(out, b.view());
        }
        case Method::MacroStop: {
            Guard g(p_);
            macro_.stop(nullptr);
            return reply(out, "OK");
        }
        case Method::Records: return do_records(out);
        case Method::Put: {
            put_ = PutState{};
            update_target(r.key, put_.target, sizeof put_.target);
            snprintf(put_.stage, sizeof put_.stage, "%s.sr-stage", put_.target);
            std::memcpy(put_.expect, r.sha, sizeof put_.expect);
            put_.size = r.size;
            std::string_view tgt(put_.target);
            p_.make_dirs(std::string(tgt.substr(0, tgt.rfind('/'))).c_str());
            p_.remove(put_.stage);
            put_.active = true;
            return true;  // the reply follows the payload (put_feed)
        }
        case Method::Restart:
        case Method::Shutdown: {
            {
                Guard g(p_);
                leases_.reset();
                macro_.stop(nullptr);
            }
            action_ = r.method == Method::Restart ? Action::Restart : Action::Shutdown;
            logf("%s requested", r.method == Method::Restart ? "restart" : "shutdown");
            reply(out, r.method == Method::Restart ? "OK restarting" : "OK shutting_down");
            return false;  // the platform loop closes everything and acts
        }
        case Method::Launch:
        case Method::Dismiss: {
            refresh_app();
            // LAUNCH presses A on the first tile: it must be SSBU, confirmed by ns (operator ruling A.1).
            const char* tile = nullptr;
            if (r.method == Method::Launch) {
                AppRecord rec[kRecords];
                long n = p_.app_records(rec, kRecords);
                tile = first_tile_check(rec, n < 0 ? 0 : size_t(n), kSsbuProgramId);
            }
            const char* e;
            {
                Guard g(p_);
                e = system_sequence_allowed(app_pid_ != 0, seq_len_ != 0);
                if (!e && tile) e = tile;
                if (!e) {
                    static const Step launch[] = {{kButtonHome, 120}, {0, 1500}, {kButtonA, 120}, {0, 200}};
                    static const Step dismiss[] = {{kButtonA, 120}, {0, 200}};
                    leases_.reset();
                    macro_.stop(nullptr);
                    if (r.method == Method::Launch) {
                        start_sequence("launch", launch, 4);
                        launches_++;
                    } else {
                        start_sequence("dismiss", dismiss, 2);
                    }
                }
            }
            logf("%s %s", r.method == Method::Launch ? "launch" : "dismiss", e ? e : "started");
            return e ? reply_err(out, "refused", e) : reply(out, "OK started");
        }
        case Method::Terminate:
        case Method::Relaunch: {
            refresh_app();
            uint64_t pid;
            {
                Guard g(p_);
                pid = app_pid_;
                if (pid) {
                    terminate_requested_ = true;
                    leases_.reset();
                    macro_.stop(nullptr);
                }
            }
            if (pid) {
                uint32_t rc = p_.terminate(pid);
                logf("terminate pid=%llu rc=0x%x", (unsigned long long)pid, rc);
                if (rc) return reply_err(out, "terminate_failed");
                for (int i = 0; i < 100; i++) {  // up to 10 s for the process to go
                    refresh_app();
                    Guard g(p_);
                    if (!app_pid_) break;
                    p_.unlock();
                    p_.sleep_ms(100);
                    p_.lock();
                }
                {
                    Guard g(p_);
                    if (app_pid_) return reply_err(out, "terminate_timeout");
                }
                if (r.method == Method::Terminate) return reply(out, "OK terminated");
                p_.sleep_ms(1000);  // let the HOME menu settle
            } else if (r.method == Method::Terminate) {
                return reply(out, "OK not_running");
            }
            return handle(s, "LAUNCH", out);
        }
        case Method::Detach: {
            {
                Guard g(p_);
                leases_.reset();
                macro_.stop(nullptr);
            }
            p_.hdls_detach();
            attached_ = false;
            return reply(out, "OK");
        }
        case Method::List: return do_list(r, out);
        case Method::File: return do_file(r, out);
        case Method::Bye: reply(out, "OK bye"); return false;
    }
    return reply_err(out, "unknown_method");
}

}  // namespace sr
