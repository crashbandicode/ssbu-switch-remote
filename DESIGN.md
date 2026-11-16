# switch-remote: design

2026-11-15, revision 3. Drive the physical Switch running Smash Ultimate 13.0.5 the way agents drive Eden:
inject controller input, launch and terminate the game, read crash logs, and see and record the screen.
Playbook followed: `ssbu-mod-playbook` `3332c0d`. Each fact cites a source. **ASSUMPTION** marks what
is not verified yet, together with the check that will settle it.

Revisions:
- **2** applied the first three operator rulings: the sysmodule is in v1, screenshots and GIFs come from the
  HDMI capture card, and caps:sc and NVN readback are dropped.
- **3** applies ruling A (the conditions for LAUNCH/DISMISS) and ruling B: ns2bench was evaluated, and OBS
  becomes the primary capture backend when it runs.

Revision 1's in-game input path is kept as the documented fallback (§8).

## The split

| Part | Does | Runs |
| --- | --- | --- |
| **sysmodule** (`sysmodule/`, C++17 + libnx) | LAN server; virtual Pro Controller (`hid:dbg` HDLS); macro player; LAUNCH/DISMISS/TERMINATE; crash and plugin logs | Atmosphere boot2 background process, id `4200000000005352`; survives game crashes |
| **in-game NRO** (`src/`, Rust Skyline plugin) | Reads the game's mode word and Online Deluxe's ONLINE bit; reports the scene to the sysmodule over loopback 10×/s. No hooks. | Inside Smash |
| **host** (`tools/host/`, Python stdlib + GStreamer/Pillow) | edenctl-compatible client; `switch://` runner backend; capture through OBS (primary) or `capd.py` (V4L2 fallback); GIF cutting | ubuntu-surface (the card is here) |

The request handling is one platform-independent class (`sysmodule/core/service.cpp`). Two platforms implement
it: libnx on the console (`source/main.cpp`) and a fake one in the host loopback build (`host_loopback/`). The
Python tests therefore drive the real request code end to end.

## 1. Input: a virtual controller from the sysmodule

- **Service.** `hid:dbg` exposes AttachHdlsWorkBuffer (cmd 324), AttachHdlsVirtualDevice (330),
  SetHdlsState (332) and DetachHdlsVirtualDevice (331), on 7.0+ (libnx `b156b477`
  `nx/source/services/hiddbg.c:466-728`). The device is a Pro Controller (`HidDeviceType_FullKey3`), as
  sys-botbase attaches it (`ae13548c` `source/commands.c:189-228`; GPL-3.0, studied only). The input is
  system-wide.
- **Buttons.** Eden macro bits 0–15 equal `HidNpadButton` bits 0–15 (Eden `src/yuzu/macro_file.cpp:323-331`).
  The sysmodule sends only bits 0–15: hid derives the stick-direction bits and masks bits 20–27 on 9.0+
  (libnx `hiddbg.h`, HdlsState). Host input can never press HOME (bit 18) or Capture (bit 19).
- **Leases and macros, as edenctl.** Per-button and per-stick leases of 1–10000 ms; a new press replaces the
  deadline; `RESET` releases everything (`CLIControl.md` "Controller input"). A macro's samples are uploaded,
  and the sysmodule plays them at 60 Hz of its own clock. A host disconnect releases held input and stops a
  macro, the same idea as ns2bench's 1 s bridge watchdog (§7).
- **Timing.** A 1 ms pad thread applies the state due at each 60 Hz boundary. **ASSUMPTION:** hid samples the
  HDLS state often enough that a state held for one frame reaches the game, at a fixed latency.
  sys-botbase's defaults (50 ms clicks, 17 ms poll; `commands.c:22-24`) suggest two frames per state is safe.
  To be measured on hardware with the capture card. If it isn't frame-accurate enough, §8 is built.
- **Player slot.** **ASSUMPTION:** the virtual pad takes the next free slot. Checked at the first smoke;
  `hiddbgApplyHdlsNpadAssignmentState` (`hiddbg.h:449`) is the tool if it must be P1.

## 2. Online gate: fail closed

Host input reaches the pad only while three things hold: the latest scene report from the NRO is under
500 ms old, the NRO's guards passed, and the report does not name an **internet (NSO) online** scene. Otherwise
the input is refused (`ERR inject_refused <reason>`), held input is released, and a macro stops with
`error=<reason>`.

**Local wireless (LDN) is allowed** (operator correction, 2026-11-15 ~23:14Z). It uses no internet service, and
the game shares its lobby and match paths with NSO online, so TR's room features and Deluxe's local-wireless paths
are driven on hardware through a local-wireless lobby (the Switch plus one Eden LDN peer).

- **Signal.** The game's mode word is at `main+0x53050f0`. The game's own reader at `main+0x1360928` is
  `adrp; ldr w8,[x8,#0xf0]; and w8,w8,#0x7fff0000; cmp …` (stock 13.0.5 `text.bin`, `tools/find_refs.py`).
- **The two families can be told apart.** Deluxe classifies the local-wireless room as `0x02010000` and the
  NSO family as `0x0701xxxx`, ignoring the top bit: Quickplay `0x07010101` in Eden, Elite `0x07010400` on the
  console (`deluxe-0108/src/auto_policy.rs:171-196`; TR `lobby_flag.rs:59-63` agrees).
  - The gate refuses `mode & 0x7f000000 == 0x07000000` (`mode_is_online`).
  - It allows `mode & 0x7fff0000 == 0x02010000` (`mode_is_local_wireless`).
- **Online Deluxe's ONLINE bit** (`ssbu_online_deluxe_online_context_v1`) is set for local wireless too
  (deluxe-plugin-sdk `abi/src/lib.rs:112-114`). It refuses only when the mode word is *not* local wireless.
- **Where the two can't be told apart, the gate fails closed.** Two known cases:
  - Deluxe says online but the word is in neither family.
  - The word is stale: Deluxe notes an online word can outlive its session (`auto_policy.rs:189-191`), so
    local play right after an online session may be refused until the word changes. That costs a false
    refusal, never an online input, and is reported if it shows up.
- **One path for both.** The NRO's read, the SCENE line, the Service's gate and the refusal are the same code
  for local wireless and NSO; only the family test differs. A local-wireless run therefore exercises the whole
  report path end to end.
- **Tests.** `local_wireless_allowed_nso_refused` (core) and `test_local_wireless_lobby_allows_input_nso_refuses`
  (host, against the real Service).
- **NRO guards.** The version must be `13.0.5`, and the 64 bytes at `main+0x1360928` must be the 13.0.5 bytes.
  Otherwise the report carries `guards=0` and the gate stays closed.
- **Channel.** The NRO sends `AUTH <token>`, then `SCENE <seq> <mode> <deluxe> <guards>`, to
  `127.0.0.1:<port+1>`. **ASSUMPTION:** loopback TCP between two processes works on the console; settled at the
  first smoke.

## 3. Launch, dismiss, terminate (ruling A, 2026-11-15)

- **No clean launch API.** LaunchApplicationForDevelop is blocked on 10.0+ and its storage-id variant on 18.0+
  (libnx `ns.c:2422,2444`). The am routes need an Application/LibraryApplet or the SystemApplet
  (`applet.h:1451,1895`). pm/pgl LaunchProgram creates a process am does not know about.
- **LAUNCH = HOME, 1.5 s, A on the first tile.** It runs without a scene report, but only while
  `pm:dmnt GetApplicationProcessId` finds no application, and only after the sysmodule confirms SSBU is the
  first tile. Confirmation reads `ns:am2` IApplicationManagerInterface cmd 0 ListApplicationRecord (libnx
  `nsListApplicationRecord`; switchbrew NS_services). switchbrew documents neither the record order nor how
  the HOME menu orders its tiles, so two signals must agree: SSBU is record 0, **and** no other record has a
  `LastUpdated` as new. Otherwise the reply is `records_unavailable` / `first_tile_not_target` /
  `first_tile_ambiguous` (core `first_tile_check`). `RECORDS` returns what the sysmodule saw, for the receipt;
  a capture-card screenshot of the HOME menu is the second, advisory, check.
- **DISMISS = A** (`HidNpadButton_A`, bit 0, 120 ms), under the same no-application rule. It closes the error
  dialog after a crash.
- Both are token-gated (host channel) and logged with timestamps in `sd:/ultimate/switch-remote/sysmodule.log`.
- **TERMINATE** uses `ns:dev` TerminateApplication (cmd 6; `ns.c:2407`), with `pgl` TerminateProcess(pid) as
  the fallback (`pgl.h:56`, Atmosphere creport's way). It waits up to 10 s. **RELAUNCH** = terminate, 1 s,
  LAUNCH.
- **Recovery order with receipts:** STATUS (`app=none exits=n`) → LIST/FILE `crash_reports` (and the new
  `.jpg`) → DISMISS → RECORDS → LAUNCH → STATUS until `gate=allowed`.

## 3a. Start, stop and update without a reboot (operator requirement 2026-11-15 ~01:05Z)

- **ovlSysmodules.** The card has the Tesla overlay `switch/.overlays/ovlSysmodules.ovl`. It lists any module whose
  `toolbox.json` has `"requires_reboot": false` (ours now does). Toggling uses two calls:
  - stop: `pmshellTerminateProgram`, a hard kill with no notice, which also deletes `flags/boot2.flag`;
  - start: `pmshellLaunchProgram`, which also creates the flag.

  Source: ovl-sysmodules `9b114b3` `source/gui_main.cpp:72-101` (GPL-2.0, studied only).

  So nothing may rely on cleanup code at stop:
  - sockets and service sessions close with the process (kernel);
  - logs are opened and closed per line;
  - a PUT that a kill interrupts leaves only `*.sr-stage`, swept at the next start (`sweep_stage`);
  - the virtual controller detaches after 60 s without input (core `detach_due`), so a kill normally finds
    none attached.

  **ASSUMPTION:** hid releases a virtual controller whose hid:dbg session dies (otherwise a kill during input
  could leave a phantom controller until reboot). Settled at the smoke: toggle off while a controller is
  attached, then check the controllers screen with the capture card.
- **Start at runtime.** The 10 s delay applies only within 60 s of boot (core `boot_delay_needed`). Everything
  is initialized from scratch, and the listener retries until the port is free.
  - Starting twice is harmless: a second instance can't take the port, never gets a client, and so never
    attaches a controller.
  - Stopping twice is a no-op in pm.
- **PUT (updates).** `PUT <key> <size> <sha256>` followed by the bytes. The key must be in `kUpdateTargets`
  (core.hpp):
  - `sysmodule.exefs`, `sysmodule.toolbox`, `nro`, `config`, and `data/<name>` under `ultimate/switch-remote/`;
  - adding a file is ONE explicit row there;
  - any other key is `ERR not_updatable`.

  The sysmodule handles the file in this order:
  1. writes it to `<target>.sr-stage`, hashing it as it arrives;
  2. refuses on a sha256 mismatch, and reads the stage back;
  3. copies the old file to `ultimate/switch-remote/backups/pre-<new16>.<name>` and reads that back;
  4. swaps the stage into place;
  5. reads the target back; if that fails, it restores the backup.

  The reply carries `old= new= backup= readback=ok`, and the client writes it to a receipt
  (`switch.update`). At most 2 MiB per file.
- **Taking effect.**
  - `sysmodule.exefs`: after `RESTART`, the sysmodule launches a fresh instance of its own program id through
    `pm:shell`, then detaches the controller, closes its sockets and exits. The new instance loads the
    `exefs.nsp` now on the card, and the client reconnects (`switch.restart`). AMS pm refuses a second
    instance only for applications (`pm_process_manager.cpp:131`), and ldr `PinProgram` has no uniqueness
    check (`ldr_process_creation.cpp:918-921`); **ASSUMPTION** until the smoke.
  - `nro`: at the next Smash launch (`RELAUNCH` = terminate + LAUNCH).
  - `config`: at the next start.
  - `SHUTDOWN` exits cleanly; starting again is then ovlSysmodules' or a reboot's job.
- **Tests.** Core: `sha256_known_vectors`, `update_allowlist_is_own_files_only`, `lifecycle_rules`. Host
  (loopback): `test_put_updates_own_file_with_backup_and_receipt`, `test_put_bad_sha_or_other_files_change_nothing`,
  `test_restart_reconnects_to_a_fresh_instance`, `test_shutdown_detaches_and_exits`,
  `test_start_sweeps_interrupted_put_stage`.

## 4. Logs

`LIST <root> [dir]` and `FILE <root> <offset> <max> <path>` read only these roots: `crash_reports`,
`fatal_reports`, `fatal_errors` and `erpt_reports` under `/atmosphere`
(AMS `1869018e` `creport_crash_report.cpp:310,356-374`, `fatal_task_error_report.cpp:82,144`,
`fusee_fatal.cpp:33`, `erpt_srv_reporter.cpp:27`), plus `ultimate` and `skyline`. The card had 706 crash
reports and 557 erpt reports (read-only listing, 2026-11-15). Paths reject `..`, absolute paths and
backslashes. Reads come in 64 KiB chunks. Nothing is written.

## 5. Screenshots and recordings (ruling B.2)

- **Device.** `/dev/video4`, AVerMedia Live Gamer Ultra 2.1 (UVC). The user has an ACL `rw`. It offers MJPEG
  1920x1080@60/120, plus YUY2/NV12 up to 3840x2160 (`evidence/capture-probe/formats-video4.txt`).
- **OBS (primary when it runs).** `tools/host/obsws.py` speaks obs-websocket v5 (stdlib WebSocket, Hello/Identify
  auth) and finds the card's V4L2 input by `device_id`:
  - stills: `GetSourceScreenshot`;
  - clips: `StartRecord`/`StopRecord`;
  - "the last N seconds": `SaveReplayBuffer` and `GetLastReplayBufferReplay`.

  The URL and password come from the environment or from obs-mcp's entry in `~/.claude.json`, read at run
  time, and are never printed. OBS 32.2.0 (snap) is installed here but not running, so only the host tests
  against a fake obs-websocket server prove this path today.
- **capd (fallback).** `tools/host/capd.py` keeps one GStreamer pipeline (v4l2src MJPEG) and the latest frame.
  When OBS holds the card, v4l2src fails at start, and capd reports `device_busy`.
  - **Choice:** the client tries OBS first (it has the card as a source) and otherwise uses capd, retrying OBS
    every 30 s.
  - **Measured on 2026-11-15:** a warm screenshot took 90–131 ms per call and a cold grab 730 ms. A 5 s
    stream-copy recording had 303 frames and 0 dropped. A 10 s copy used 1% of one CPU and 28 MB RSS.
  - **Signal:** none yet. The console is in hekate UMS, so the card sends black and then its own
    "No Signal" screen, which capd recognises.
- **Recommendation: record clips with capd's stream copy, and use OBS for stills and replay-buffer clips when
  it is open.**
  - capd is exact: its sidecar gives every frame a host wall time, the same clock as the input log, so a clip
    is cut at the observed event and drops are counted.
  - capd is cheap: 1% CPU, no encoder.
  - OBS: a hardware or x264 encode on this loaded host. It gives no per-frame wall times (its start time is
    the StartRecord reply, `approximate_start`). Its replay buffer is the right tool for "the seconds before a
    crash" while an operator watches. Only one opener can hold the card at a time, so with OBS open
    everything goes through OBS.
  - A live OBS comparison of CPU cost and drops is still to come (the operator runs OBS).
- **GIFs.** `tools/host/gif.py` decodes any file (`decodebin`). It samples a fixed real-time grid, repeating the
  last frame across drops, and writes playbook presets: docs = 1280 wide, ≤10 s, ≤15 fps; evidence = 640 wide,
  ≤15 fps, ≤10 MB. Heavy encodes run at nice 19 under the guard, or on the Yoga/XPS.

## 6. Network and host side

- **Sysmodule server.**
  - `bsd:u` with a small socket config.
  - One host client at a time on `0.0.0.0:<port>` (47130); the scene channel is `127.0.0.1:<port+1>`.
  - Opt-in: nothing listens without `sd:/ultimate/switch-remote/config.txt` holding `token=` (16–64 chars).
  - Peers outside private IPv4 ranges are closed.
- **Protocol.** `docs/PROTOCOL.md`.
- **Runner backend.** `playbook-patch/0001` adds `switch://HOST:PORT` to the runner's client factory: 4 code
  lines and a docstring, not applied to the playbook.
- **Wrapper.** `tools/host/switch_test.py run ROWS --host IP` runs the unchanged runner in-process with one
  shared client, because the sysmodule serves one client at a time. It mirrors SD log files to local files
  for `log` gates (`--mirror diag_log=ultimate:training_rooms/events.log`).

## 6a. LAN only (operator note relayed 2026-11-15 ~23:10Z)

The operator plans to block the Switch from the external internet. Nothing here needs it:
- **Sysmodule:** its services are `fsp-srv set:sys hid:dbg bsd:u pm:dmnt pm:info ns:dev ns:am2 pgl`, with no
  `nifm`, `ssl`, `time` or update service. It binds `0.0.0.0` on the console's LAN address (DHCP from the local
  router).
- **NRO:** it talks only to `127.0.0.1`.
- **Host:** capture is the local card or OBS on `localhost`. Discovery (`tools/host/discover.py`) probes the
  local /24 once every 2 min. No NTP, update or telemetry is used anywhere.

Traffic stays light: the client sends requests only when the runner or an agent acts (no keep-alive loop), and
screenshots never come from the Switch.

**While the console is blocked, NSO online can't be reached** (Eden showed the same with an offline profile:
error 2306-0520, `eden/sr-nro-01/RESULTS.md`). **Online-path testing still happens over a local-wireless lobby**,
which shares its code paths with NSO (operator correction, ~23:14Z). The smoke's online step becomes:
1. The Switch joins or hosts a local-wireless lobby with one Eden LDN peer (Yoga or XPS, per the ledger), under
   the playbook's two-PC rules (HARNESS-LESSONS "Two-PC local-wireless rooms"; the host ACCEPTS the guest's
   request).
2. STATUS shows a `0x0201xxxx` scene with `gate=allowed`, and an input acts in the lobby.
3. NSO refusal is shown by code and test, not on the console: `mode_is_online`;
   `local_wireless_allowed_nso_refused`; `test_local_wireless_lobby_allows_input_nso_refuses`;
   `test_online_scene_refuses`.

The gate still fails closed whenever the NRO's report is missing or stale. No further network hardening is
planned beyond the token, private peers and that gate.

## 7. ns2bench (ruling B.1, evaluated 2026-11-15)

`github.com/phantomptr/ns2bench` was read at `a105ca7` in `/home/user/ref/ns2bench`; nothing was run or
built.

- **License.** GPL-3.0-or-later, with section 7 terms (attribution and renaming). Parts are MIT, from
  GP2040-CE.
- **What it is.** Built for the **Switch 2**. It drives the console from external hardware: a USB-gadget
  wired controller on a Linux board or a Pico. Video comes from a capture card through ffmpeg, plus a 30 s
  DVR, OCR, and an HTTP and MCP API.
- **What it lacks.** No launch, no SD logs, no game-aware online gate.
- **Decision: keep our own; copy nothing.** Two ideas were re-implemented: input goes neutral when the client
  leaves, and a rolling clip of the seconds before a failure (OBS replay buffer).

## 8. Fallback (documented, not built): in-game input

Replace hooks on the SDK exports `nn::hid::GetNpadState` (FullKey, Handheld, JoyDual) inside the NRO. 13.0.5
main imports only these singular overloads. Online Deluxe and `smash_gamepad_overlay` call them but don't
hook them. This is built only if §1's measurement fails. Its model (`src/model/input.rs`, `protocol.rs`)
stays host-tested.

## 9. Safety

- **Boot.**
  - boot2 aborts boot on OutOfResource/OutOfMemory/LimitReached for SD sysmodules (AMS
    `boot2_api.board.nintendo_nx.cpp:152-155`).
  - Image: 1.38 MiB including a static 1 MiB heap (ELF LOAD segments of build `39ebc14`); two 16 KiB stacks;
    about 160 KiB of socket transfer memory. sys-botbase uses a 4.5 MiB heap.
  - No service is touched for 10 s after start, nothing calls `fatalThrow`, and failures are logged while the
    module idles.
  - **Recovery:** delete `atmosphere/contents/4200000000005352/` (or its `flags/boot2.flag`) in hekate UMS.
- **Permissions.** `fsp-srv set:sys hid:dbg bsd:u pm:dmnt pm:info pm:shell ns:dev ns:am2 pgl`; no debug syscalls.
- **Input.** Fail closed (§2). HOME/A only through the fixed LAUNCH/DISMISS sequences (§3).
- **Exposure.** Off by default; token; private peers; one client.
- **NRO.** No hooks, one thread on a static stack, one install line; the diag build logs mode changes.

## 10. Build and test

| Gate | Where | Result |
| --- | --- | --- |
| NRO: cargo test / clippy -D warnings / no-default-features / skyline release / diag | Yoga, `remote-gate.ps1`, `e2e802a` (the NRO source is unchanged since) | 21/0, 0, 0, 36,864 B, 36,864 B |
| Sysmodule: devkitA64 r30 + libnx 4.12.0, `-Wall -Wextra -Werror` | Yoga, `tools/build_sysmodule.ps1`, `39ebc14` | exefs.nsp 142,484 B, 0 warnings |
| Sysmodule core | host g++ | 159 checks, 0 failures |
| Host (client vs loopback Service, capture, OBS fake, GIF, runner patch) | ubuntu-surface | 40 tests OK |

## Requirement trace

| Operator clause | Design | Test |
| --- | --- | --- |
| "write an nro in its own repo" | own repo; NRO scene reporter, cdylib, model/runtime split | `scene_line_format`, `guard_bytes_are_64` |
| "lets you remote control the switch like you do in eden" | HDLS virtual pad; edenctl leases and macros; edenctl-compatible client | `test_input_button_tap_maps_to_btn`, `test_macro_events_parity_with_eden_file`, `test_disconnect_releases_held_input` |
| "a native screenshot feature that sends the screenshot over the wire" | capture card through OBS or capd behind `ui.screenshot` | `test_write_png_scales_to_width`, `test_screenshot_goes_to_capd`, `test_switch_client_routes_screenshots_to_obs` |
| "so you can see where you are and iterate" | runner backend; status from both sides | `test_runner_factory_switch_scheme`, `test_status_merges_sysmodule_and_capture` |
| "it would adapt eden automation" | `switch://` client in the unchanged runner, same rows and segments | `test_macro_events_parity_with_eden_file`, `test_runner_factory_switch_scheme` |
| "this way you can do hardware testing too" | LAN server, token, private peers, fail-closed gate | `peers_are_private_only`, `config_requires_token`, `test_online_scene_refuses`, `test_stale_scene_closes_the_gate_and_releases_input` |
| "reconnecting via dbi as needed if there's a crash" | crash detection, logs, relaunch, playbook installers | `test_dropped_connection_reports_not_running`, `test_list_and_file_roots` |
| "it should have a sysmmodule s that you can launch the game yourself and get the logs if it crashes" | sysmodule LAUNCH/TERMINATE/RELAUNCH with the first-tile check; LIST/FILE over crash and plugin logs | `test_launch_only_without_application`, `test_launch_refused_unless_ssbu_is_the_first_tile`, `test_terminate_and_exit_count`, `test_list_and_file_roots` |
| "for the screenshot part I mean and hardware gifs" | capd recordings with a wall-clock sidecar; GIF cut and encode | `test_gap_counts_missing_frames`, `test_card_no_signal_screen_is_detected`, `test_cut_window_from_sidecar` |
| "OBS may still be better for the photo/gif part" | OBS primary when it runs (stills, record, replay buffer); capd fallback | `test_finds_the_card_source_and_screenshots`, `test_record_and_replay`, `test_pick_backend_prefers_obs_then_falls_back` |
| "we want it to be compatible with the sysmmodule plugin so we don't have to reboot forchanges" | requires_reboot false; hard-kill-safe lifecycle; PUT with receipts for own files; RESTART self-relaunch | `test_restart_reconnects_to_a_fresh_instance`, `test_put_updates_own_file_with_backup_and_receipt`, `test_shutdown_detaches_and_exits`, `test_start_sweeps_interrupted_put_stage` |
| "online testing should still be possible over local wireless lobby. Those paths should be shared with nso path" | gate allows the local-wireless family, refuses NSO, fails closed when ambiguous; one report path for both; smoke over a local-wireless lobby | `test_local_wireless_lobby_allows_input_nso_refuses`, `test_online_scene_refuses` |

## Open points

Each is settled at the first hardware smoke:
- HDLS frame timing and player slot.
- Loopback TCP.
- The first-tile rule: the ns record order against a capture of the HOME menu.
- The card's latency.
- `ns:dev` TerminateApplication on fw 23.5.0.
- A live OBS comparison of CPU cost and drops.
