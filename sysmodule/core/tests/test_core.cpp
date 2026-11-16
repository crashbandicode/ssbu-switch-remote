// Host tests for sysmodule/core. Build and run:
//   g++ -std=c++17 -O1 -Wall -Wextra -Werror -I sysmodule/core sysmodule/core/core.cpp
//       sysmodule/core/tests/test_core.cpp -o /tmp/test_core && /tmp/test_core
#include <cstdio>
#include <cstdlib>
#include <string>

#include <algorithm>

#include "core.hpp"
#include "sha256.hpp"

using namespace sr;

static int failures = 0, checks = 0;
#define CHECK(cond)                                                          \
    do {                                                                     \
        checks++;                                                            \
        if (!(cond)) {                                                       \
            failures++;                                                      \
            std::printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);      \
        }                                                                    \
    } while (0)
static bool streq(const char* a, const char* b) { return (!a && !b) || (a && b && std::string(a) == b); }
#define STREQ(a, b) CHECK(streq((a), (b)))

static constexpr uint64_t F = 19200000;  // armGetSystemTickFreq on the console

static void button_bits_follow_eden_macro_order() {
    CHECK(button_bit("a") == 0);
    CHECK(button_bit("zr") == 9);
    CHECK(button_bit("lstick") == 4);
    CHECK(button_bit("ddown") == 15);
    CHECK(button_bit("home") == 18);
    CHECK(button_bit("nope") == -1);
    CHECK((1u << button_bit("zr")) == 512);  // open-sidebar.edenmacro.json presses 512
}

static void leases_expire_and_never_zero() {
    CHECK(lease_deadline(1000, 100, F) == 1000 + 1920000);
    CHECK(lease_deadline(0, 0, F) == 19200);
    CHECK(lease_deadline(0, 99999, F) == 192000000);
    Leases l;
    l.press(0, lease_deadline(1000, 100, F));
    l.press(9, 5000000);
    CHECK(l.state(1000).buttons == (1 | 512));
    CHECK(l.state(1000 + 1920000).buttons == 512);
    CHECK(l.state(5000000).buttons == 0);
    l.set_stick(0, -32767, 0, 5000);
    PadState p = l.state(1000);
    CHECK(p.lx == -32767 && p.ly == 0);
    CHECK((p.buttons >> 16 & 0xff) == 1);  // StickLLeft derived
    CHECK(l.state(6000).lx == 0);
    l.reset();
    CHECK(l.state(0) == PadState{});
}

static void samples_are_checked() {
    Sample a{1, 1, {0, 0, 0, 0}}, b{1, 0, {0, 0, 0, 0}}, home{2, 1u << 18, {0, 0, 0, 0}}, late{72, 0, {0, 0, 0, 0}},
        wide{3, 1u << 23, {0, 0, 0, 0}};
    STREQ(check_sample(&a, b, 72), "frames_not_ascending");
    STREQ(check_sample(&a, home, 72), "system_button");
    STREQ(check_sample(&a, late, 72), "frame_past_duration");
    STREQ(check_sample(nullptr, wide, 72), "bits_outside_mask");
    STREQ(check_sample(nullptr, a, 72), nullptr);
}

static void playback_finds_the_sample_in_force() {
    Sample s[3] = {{0, 0, {}}, {1, 1, {}}, {5, 0, {}}};  // tap-a.edenmacro.json
    CHECK(sample_at(s, 3, 0, 0) == 0);
    CHECK(sample_at(s, 3, 0, 1) == 1);
    CHECK(sample_at(s, 3, 1, 4) == 1);
    CHECK(sample_at(s, 3, 1, 5) == 2);
    CHECK(sample_at(s, 3, 2, 2) == 1);
    Sample late[1] = {{3, 1, {}}};
    CHECK(sample_at(late, 1, 0, 2) == -1);
    CHECK(sample_at(nullptr, 0, 0, 2) == -1);
}

static void macro_plays_at_60hz_and_finishes() {
    static Macro m;
    STREQ(m.play(0), "no_macro");
    STREQ(m.begin(3, 72), nullptr);
    STREQ(m.add({0, 0, {}}), nullptr);
    STREQ(m.play(0), "macro_incomplete");
    STREQ(m.add({1, 1, {0, 32767, 0, 0}}), nullptr);
    STREQ(m.add({5, 0, {}}), nullptr);
    CHECK(m.phase == MacroPhase::Ready);
    STREQ(m.add({6, 0, {}}), "not_loading");
    const uint64_t t0 = 1000;
    STREQ(m.play(t0), nullptr);
    CHECK(m.tick(t0, F).buttons == 0);
    PadState p = m.tick(t0 + F / 60, F);  // frame 1
    CHECK((p.buttons & 0xffff) == 1);
    CHECK(p.ly == 32767);
    CHECK((p.buttons >> 16 & 0xff) == 2);  // StickLUp
    CHECK((m.tick(t0 + 4 * F / 60 + 1, F).buttons & 0xffff) == 1);
    CHECK(m.tick(t0 + 5 * F / 60 + 1, F).buttons == 0);
    CHECK(m.phase == MacroPhase::Playing);
    m.tick(t0 + 72 * F / 60 + 1, F);
    CHECK(m.phase == MacroPhase::Finished);
    CHECK(m.tick(t0 + 80 * F / 60, F) == PadState{});
}

static void macro_rejects_bad_samples_and_stops() {
    static Macro m;
    STREQ(m.begin(2, 10), nullptr);
    STREQ(m.add({0, 1u << 19, {}}), "system_button");
    CHECK(m.phase == MacroPhase::Error);
    STREQ(m.error, "system_button");
    STREQ(m.begin(0, 10), "out_of_range");
    STREQ(m.begin(1, 10), nullptr);
    STREQ(m.add({0, 1, {}}), nullptr);
    STREQ(m.play(5), nullptr);
    STREQ(m.begin(1, 10), "macro_busy");
    m.stop("online_mode");
    CHECK(m.phase == MacroPhase::Error);
    STREQ(m.error, "online_mode");
}

static void gate_fails_closed() {
    SceneReport r;
    CHECK(gate(r, 1000, F) == Gate::NoReport);
    r.valid = true;
    r.received_tick = 1000;
    r.guards_ok = true;
    r.mode = 0x01020000;  // an offline mode word
    CHECK(gate(r, 1000 + F / 10, F) == Gate::Allowed);
    CHECK(gate(r, 1000 + F, F) == Gate::Stale);
    CHECK(gate(r, 500, F) == Gate::Stale);  // a report from the future is not trusted
    r.guards_ok = false;
    CHECK(gate(r, 1000, F) == Gate::Unguarded);
    r.guards_ok = true;
    r.mode = 0x07010000;  // Quickplay family (TR lobby_flag.rs)
    CHECK(gate(r, 1000, F) == Gate::OnlineMode);
    r.mode = 0x07020400;
    CHECK(gate(r, 1000, F) == Gate::OnlineMode);
    r.mode = 0x87010000;  // the high bit is ignored, as in TR's mask
    CHECK(gate(r, 1000, F) == Gate::OnlineMode);
    r.mode = 0x02010000;  // local-wireless family: Deluxe's ONLINE bit covers it too, so it no longer refuses
    r.deluxe_online = 1;
    CHECK(gate(r, 1000, F) == Gate::Allowed);
    r.mode = 0x01020000;  // a non-local word with Deluxe ONLINE: can't be told apart, refused
    CHECK(gate(r, 1000, F) == Gate::OnlineDeluxe);
    r.deluxe_online = 0;
    CHECK(gate(r, 1000, F) == Gate::Allowed);
    STREQ(gate_name(Gate::OnlineMode), "online_mode");
}

static void config_requires_token() {
    Config c;
    STREQ(parse_config("", c), "no_token");
    STREQ(parse_config("token=short", c), "bad_token");
    STREQ(parse_config("token=abcdefghijklmnop!", c), "bad_token");
    STREQ(parse_config("# c\ntoken = abcdefghijklmnop\r\nport=47131\npeers=172.30.254.0/24\ninject=off\nnpad=auto\n", c),
          nullptr);
    CHECK(c.token_view() == "abcdefghijklmnop");
    CHECK(c.port == 47131);
    CHECK(c.peers == Peers::Net && c.net == 0xac1efe00 && c.net_len == 24);
    CHECK(!c.inject);
    STREQ(parse_config("token=abcdefghijklmnop\nport=80", c), "bad_port");
    STREQ(parse_config("token=abcdefghijklmnop\npeers=8.8.8.0/24", c), "bad_peers");
    STREQ(parse_config("token=abcdefghijklmnop\ncolour=red", c), "unknown_key");
}

static void peers_are_private_only() {
    Config c;
    parse_config("token=abcdefghijklmnop", c);
    uint32_t ip;
    for (const char* s : {"172.30.254.66", "10.1.2.3", "172.16.0.1", "172.31.255.255", "127.0.0.1", "169.254.3.4"}) {
        CHECK(parse_ipv4(s, ip) && peer_allowed(c, ip));
    }
    for (const char* s : {"8.8.8.8", "172.32.0.1", "192.169.0.1", "100.64.0.1"}) {
        CHECK(parse_ipv4(s, ip) && !peer_allowed(c, ip));
    }
    CHECK(!parse_ipv4("256.0.0.1", ip));
    CHECK(!parse_ipv4("1.2.3", ip));
    c.peers = Peers::Loopback;
    CHECK(peer_allowed(c, 0x7f000001) && !peer_allowed(c, 0xac1efebb));
}

static void tokens_compare_whole() {
    CHECK(token_eq("abcdef", "abcdef"));
    CHECK(!token_eq("abcde", "abcdef"));
    CHECK(!token_eq("abcdefg", "abcdef"));
    CHECK(!token_eq("", ""));
}

static void parses_every_method() {
    Request r;
    STREQ(parse_request("AUTH abc", r), nullptr);
    CHECK(r.method == Method::Auth && r.text == "abc");
    STREQ(parse_request("PING\r", r), nullptr);
    CHECK(r.method == Method::Ping);
    STREQ(parse_request("BTN 0 tap 100", r), nullptr);
    CHECK(r.method == Method::Button && r.bit == 0 && r.state == BtnState::Tap && r.ms == 100);
    STREQ(parse_request("STICK l -32767 32767 250", r), nullptr);
    CHECK(r.method == Method::Stick && r.stick == 0 && r.x == -32767 && r.y == 32767 && r.ms == 250);
    STREQ(parse_request("MBEGIN 3 72", r), nullptr);
    CHECK(r.method == Method::MacroBegin && r.n == 3 && r.duration == 72);
    STREQ(parse_request("MEV 1 1 0 0 0 -5", r), nullptr);
    CHECK(r.method == Method::MacroEvent && r.sample.frame == 1 && r.sample.sticks[3] == -5);
    for (const char* m : {"STATUS", "RESET", "MPLAY", "MSTATUS", "MSTOP", "LAUNCH", "TERMINATE", "RELAUNCH", "DISMISS", "DETACH", "BYE"})
        STREQ(parse_request(m, r), nullptr);
    STREQ(parse_request("LIST crash_reports", r), nullptr);
    CHECK(r.method == Method::List && r.root == Root::CrashReports && r.text.empty());
    STREQ(parse_request("LIST ultimate training_rooms", r), nullptr);
    STREQ(parse_request("FILE ultimate 0 4096 training_rooms/events.log", r), nullptr);
    CHECK(r.method == Method::File && r.root == Root::Ultimate && r.max == 4096 && r.text == "training_rooms/events.log");
    STREQ(parse_request("SCENE 7 0x1020000 -1 1", r), nullptr);
    CHECK(r.method == Method::Scene && r.scene.seq == 7 && r.scene.mode == 0x1020000 && r.scene.deluxe_online == -1 &&
          r.scene.guards_ok && r.scene.valid);
}

static void refuses_bad_requests() {
    Request r;
    STREQ(parse_request("", r), "empty");
    STREQ(parse_request("FOO", r), "unknown_method");
    STREQ(parse_request("PING x", r), "bad_arguments");
    STREQ(parse_request("BTN 16 tap 100", r), "out_of_range");
    STREQ(parse_request("BTN 0 hold 100", r), "bad_arguments");
    STREQ(parse_request("BTN 0 tap 0", r), "out_of_range");
    STREQ(parse_request("BTN 0 tap 10001", r), "out_of_range");
    STREQ(parse_request("STICK l 40000 0 100", r), "out_of_range");
    STREQ(parse_request("MBEGIN 4097 72", r), "out_of_range");
    STREQ(parse_request("LIST nand", r), "bad_root");
    STREQ(parse_request("FILE ultimate 0 4096 ../x", r), "bad_path");
    STREQ(parse_request("FILE ultimate 0 4096 /abs", r), "bad_path");
    STREQ(parse_request("FILE ultimate 0 65537 a", r), "out_of_range");
    STREQ(parse_request("SCENE 1 0xZZ 0 1", r), "bad_arguments");
    STREQ(parse_request("SCENE 1 0x1 2 1", r), "bad_arguments");
    std::string longline(kLineMax, 'A');
    STREQ(parse_request(longline, r), "too_long");
}

static void launch_only_without_application() {
    STREQ(system_sequence_allowed(false, false), nullptr);
    STREQ(system_sequence_allowed(true, false), "application_running");
    STREQ(system_sequence_allowed(false, true), "sequence_busy");
    Request r;
    STREQ(parse_request("LIST fatal_reports", r), nullptr);
    CHECK(r.root == Root::FatalReports);
    CHECK(kRootPaths[size_t(Root::Skyline)] == "/atmosphere/contents/01006A800016E000/romfs/skyline");
}

static void first_tile_requires_both_signals() {
    const uint64_t S = kSsbuProgramId, O = 0x0100000000010000ull;
    AppRecord ok[3] = {{S, 0, 900}, {O, 0, 800}, {0x0100000000020000ull, 1, 100}};
    STREQ(first_tile_check(ok, 3, S), nullptr);
    STREQ(first_tile_check(ok, 0, S), "records_unavailable");
    STREQ(first_tile_check(nullptr, 3, S), "records_unavailable");
    AppRecord other_first[2] = {{O, 0, 900}, {S, 0, 800}};
    STREQ(first_tile_check(other_first, 2, S), "first_tile_not_target");
    AppRecord newer_elsewhere[2] = {{S, 0, 800}, {O, 0, 900}};
    STREQ(first_tile_check(newer_elsewhere, 2, S), "first_tile_ambiguous");
    AppRecord tie[2] = {{S, 0, 900}, {O, 0, 900}};
    STREQ(first_tile_check(tie, 2, S), "first_tile_ambiguous");
    AppRecord only[1] = {{S, 0, 1}};
    STREQ(first_tile_check(only, 1, S), nullptr);
    Request r;
    STREQ(parse_request("RECORDS", r), nullptr);
    CHECK(r.method == Method::Records);
}

static void local_wireless_allowed_nso_refused() {
    SceneReport r;
    r.valid = true;
    r.received_tick = 1000;
    r.guards_ok = true;
    // Local-wireless room and battle words, with and without Deluxe's ONLINE bit: allowed.
    for (uint32_t mode : {0x02010000u, 0x02010001u, 0x82010000u}) {
        r.mode = mode;
        for (int dx : {-1, 0, 1}) {
            r.deluxe_online = dx;
            CHECK(gate(r, 1000, F) == Gate::Allowed);
        }
        CHECK(mode_is_local_wireless(mode) && !mode_is_online(mode));
    }
    // Every NSO family value refuses, whatever Deluxe says (Quickplay, Elite, arena-like 0x0702, top bit set).
    for (uint32_t mode : {0x07010101u, 0x07010400u, 0x07010000u, 0x07020000u, 0x87010101u}) {
        r.mode = mode;
        for (int dx : {-1, 0, 1}) {
            r.deluxe_online = dx;
            CHECK(gate(r, 1000, F) == Gate::OnlineMode);
        }
    }
    // Ambiguous: Deluxe says online but the word is neither family (e.g. an unknown or stale word): refused.
    r.mode = 0x01000000u;
    r.deluxe_online = 1;
    CHECK(gate(r, 1000, F) == Gate::OnlineDeluxe);
    r.deluxe_online = 0;
    CHECK(gate(r, 1000, F) == Gate::Allowed);
}

static std::string sha_hex(const std::string& s) {
    Sha256 h;
    h.update(s.data(), s.size());
    char out[65];
    h.finish_hex(out);
    return out;
}

static void sha256_known_vectors() {
    CHECK(sha_hex("") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
    CHECK(sha_hex("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
    CHECK(sha_hex("abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq") ==
          "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1");
    std::string million(1000000, 'a');
    CHECK(sha_hex(million) == "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0");
    // Streaming in odd chunks gives the same digest.
    Sha256 h;
    for (size_t i = 0; i < million.size(); i += 777) h.update(million.data() + i, std::min<size_t>(777, million.size() - i));
    char out[65];
    h.finish_hex(out);
    CHECK(std::string(out) == "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0");
}

static void update_allowlist_is_own_files_only() {
    char p[256];
    CHECK(update_target("sysmodule.exefs", p, sizeof p) &&
          std::string(p) == "/atmosphere/contents/4200000000005352/exefs.nsp");
    CHECK(update_target("nro", p, sizeof p) &&
          std::string(p) == "/atmosphere/contents/01006A800016E000/romfs/skyline/plugins/libssbu_switch_remote.nro");
    CHECK(update_target("config", p, sizeof p) && std::string(p) == "/ultimate/switch-remote/config.txt");
    CHECK(update_target("data/notes.txt", p, sizeof p) && std::string(p) == "/ultimate/switch-remote/notes.txt");
    for (const char* bad : {"data/", "data/backups", "data/../x", "data/a/b", "data/.hidden", "libtraining_rooms.nro",
                            "plugins/libtraining_rooms.nro", "/atmosphere/contents/x", "exefs", ""})
        CHECK(!update_target(bad, p, sizeof p));
    backup_path("/atmosphere/contents/4200000000005352/exefs.nsp",
                "f3fe636a1cd7bd0f58599b2080cc4735caeb67684dbdccf5eccb6ce14b75d6c5", p, sizeof p);
    CHECK(std::string(p) == "/ultimate/switch-remote/backups/pre-f3fe636a1cd7bd0f.exefs.nsp");
    Request r;
    const std::string sha(64, 'a');
    const std::string put_line = "PUT nro 37000 " + sha;  // Request views point into the line
    STREQ(parse_request(put_line, r), nullptr);
    CHECK(r.method == Method::Put && r.key == "nro" && r.size == 37000 && std::string(r.sha) == sha);
    STREQ(parse_request("PUT libtraining_rooms.nro 10 " + sha, r), "not_updatable");
    STREQ(parse_request("PUT nro 0 " + sha, r), "out_of_range");
    STREQ(parse_request("PUT nro 3000000 " + sha, r), "out_of_range");
    STREQ(parse_request("PUT nro 10 " + std::string(64, 'G'), r), "bad_arguments");
    STREQ(parse_request("PUT nro 10 abc", r), "bad_arguments");
    STREQ(parse_request("RESTART", r), nullptr);
    CHECK(r.method == Method::Restart);
    STREQ(parse_request("SHUTDOWN", r), nullptr);
    CHECK(r.method == Method::Shutdown);
}

static void lifecycle_rules() {
    CHECK(boot_delay_needed(5000));       // at boot
    CHECK(!boot_delay_needed(3600000));   // ovlSysmodules start, RESTART
    CHECK(!detach_due(false, false, 999999, 0));
    CHECK(!detach_due(true, true, 999999, 0));
    CHECK(!detach_due(true, false, 59999, 0));
    CHECK(detach_due(true, false, 60000, 0));
    CHECK(!detach_due(true, false, 10, 20));  // clock below the last activity: never detach early
    CHECK(is_stage_file("exefs.nsp.sr-stage") && !is_stage_file("exefs.nsp") && !is_stage_file(".sr-stage"));
}

static void self_diagnosis_helpers() {
    CHECK(retry_delay_ms(0) == 0 && retry_delay_ms(1) == 1000 && retry_delay_ms(3) == 5000 && retry_delay_ms(4) == 10000 &&
          retry_delay_ms(1000) == 10000);
    CHECK(log_attempt(1) && log_attempt(5) && !log_attempt(7) && log_attempt(12));
    BootStatus s;
    char line[256];
    format_status(s, line, sizeof line);
    CHECK(std::string(line) == "version=0.2.0 stage=start ip=none listening=0 port=0 uptime_ms=0 socket_attempts=0 "
                               "nifm_attempts=0 config=missing last_error=none");
    s.stage = "serving";
    s.ip = 0xac1efe35;
    s.listening = true;
    s.port = 47130;
    s.uptime_ms = 61000;
    s.socket_attempts = 1;
    s.nifm_attempts = 3;
    s.config_ok = true;
    format_status(s, line, sizeof line);
    CHECK(std::string(line) == "version=0.2.0 stage=serving ip=172.30.254.53 listening=1 port=47130 uptime_ms=61000 "
                               "socket_attempts=1 nifm_attempts=3 config=ok last_error=none");
}

static void line_buffer() {
    LineBuf<16> b;
    b.put("OK ");
    b.put_u64(1234);
    b.put(" ");
    b.put_hex(0x7010000);
    CHECK(b.view() == "OK 1234 0x701000");  // truncated at 16
}

int main() {
    button_bits_follow_eden_macro_order();
    leases_expire_and_never_zero();
    samples_are_checked();
    playback_finds_the_sample_in_force();
    macro_plays_at_60hz_and_finishes();
    macro_rejects_bad_samples_and_stops();
    gate_fails_closed();
    config_requires_token();
    peers_are_private_only();
    tokens_compare_whole();
    parses_every_method();
    refuses_bad_requests();
    launch_only_without_application();
    first_tile_requires_both_signals();
    local_wireless_allowed_nso_refused();
    sha256_known_vectors();
    update_allowlist_is_own_files_only();
    lifecycle_rules();
    self_diagnosis_helpers();
    line_buffer();
    std::printf("%d checks, %d failures\n", checks, failures);
    return failures ? 1 : 0;
}
