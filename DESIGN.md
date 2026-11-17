# switch-remote: design

2026-11-16, revision 5. Drive the physical Switch running Smash Ultimate 13.0.5 the way agents drive Eden:
inject controller input, launch and terminate the game, read crash logs, and see and record the screen.
Playbook followed: `ssbu-mod-playbook` `3332c0d`. Each fact cites a source. **ASSUMPTION** marks what
is not verified yet, together with the check that will settle it.

Revisions:
- **2** applied the first three operator rulings: the sysmodule is in v1, screenshots and GIFs come from the
  HDMI capture card, and caps:sc and NVN readback are dropped.
- **3** applies ruling A (the conditions for LAUNCH/DISMISS) and ruling B: ns2bench was evaluated, and OBS
  becomes the primary capture backend when it runs.

- **4** adopts option (d) (primary ruling 2026-11-16): sys-autopilot 1.5.0 drives the console, and the online
  gate, the launch/terminate sequences and the log reads move to the host (§12). Our sysmodule (r5) is parked as
  the fallback if sys-autopilot fails on fw 23.5.0 (§11). Sections 1, 3 and 3a describe that parked sysmodule.

- **5** (primary direction ~03:50Z): INPUT moves to a USB wired controller on the operator's ESP32-S3 board (§13).
  sys-autopilot becomes optional (logs/files). §13a evaluates a Raspberry Pi Zero 2 W gadget as the second option.

Revision 1's in-game input path is kept as the documented fallback (§8).

## The split

| Part | Does | Runs |
| --- | --- | --- |
| **sys-autopilot 1.5.0** (option d, §12; upstream release, not ours) | LAN REST server; virtual Pro Controller; file access | Atmosphere background process, id `4200000000004150`, started from ovlSysmodules |
| **sysmodule** (`sysmodule/`, C++17 + libnx; **parked**, §11) | LAN server; virtual Pro Controller (`hid:dbg` HDLS); macro player; LAUNCH/DISMISS/TERMINATE; crash and plugin logs | id `4200000000005352`; files stay on the card, boot2 flag removed |
| **in-game NRO** (`src/`, Rust Skyline plugin) | Reads the game's mode word and Online Deluxe's ONLINE bit; reports the scene 10×/s to `scene_host` (the host receiver under option d; loopback for the parked sysmodule). No hooks. | Inside Smash |
| **host** (`tools/host/`, Python stdlib + GStreamer/Pillow) | edenctl-compatible clients (`autopilot://`, `switch://`); scene receiver and gate (`scened.py`); HOME image gate (`homegate.py`); capture through OBS (primary) or `capd.py`; GIF cutting | ubuntu-surface (the card is here), wired 172.30.254.66 |

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
- **Runner backend.** The playbook runner's own client factory selects this client for `usb://`, `pi://`,
  `switch://` and `autopilot://` sockets (native since playbook f60448d; the earlier `playbook-patch/0001` is gone).
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
  - Image (r5, `1cda5f7`): 2.40 MiB including a static 2 MiB heap; 64 KiB main and 32 KiB pad stacks; 296 KiB
    of socket transfer memory (Nintendo-default sizes, §11). sys-botbase uses a 4.5 MiB heap.
  - Since r4 the SD and a breadcrumb come first, every stage and service call is logged before and after, the
    boot delay applies only within 60 s of console boot, nothing calls `fatalThrow`, and init failures retry with
    a backoff instead of idling (`docs/SMOKE-01-REVIEW.md`).
  - **Recovery:** delete `atmosphere/contents/4200000000005352/` (or its `flags/boot2.flag`) in hekate UMS.
- **Permissions.** `fsp-srv set:sys hid:dbg bsd:u pm:dmnt pm:info pm:shell ns:dev ns:am2 pgl nifm:u psc:m`; no
  debug syscalls.
- **Input.** Fail closed (§2). HOME/A only through the fixed LAUNCH/DISMISS sequences (§3).
- **Exposure.** Off by default; token; private peers; one client.
- **NRO.** No hooks, one thread on a static stack, one install line; the diag build logs mode changes.

## 10. Build and test

| Gate | Where | Result |
| --- | --- | --- |
| NRO 0.2.0 (`scene_host`): cargo test / clippy -D warnings / no-default-features / skyline release / diag | Yoga, `remote-gate.ps1`, `3d6e1bb` | 22/0, 0, 0, 36,864 B (sha256 `d4581396…`), 36,864 B |
| Sysmodule: devkitA64 r30 + libnx 4.12.0, `-Wall -Wextra -Werror` | Yoga, `tools/build_sysmodule.ps1`, `1cda5f7` (r5) | exefs.nsp 158,967 B (sha256 `b4f634a8…`), 0 warnings, `check_nsp` ok |
| Sysmodule core | host g++ | 240 checks, 0 failures |
| Host (autopilot driver vs a fake sys-autopilot, scene receiver over TCP, image gate, loopback Service, capture, OBS fake, GIF, runner factory) | ubuntu-surface | 94 tests OK |
| Card receipt tool (UMS and MTP via gio, incl. allowlisted removals) | ubuntu-surface | 16 tests OK |
| switch-pad firmware (ESP-IDF v5.5.6, esp_tinyusb 1.7.6~2, TinyUSB 0.21.0~6; our component `-Wall -Wextra -Werror`) | Yoga, `tools/build_pad.ps1`, `433f357` | 0 warnings; `switch_pad.bin` 234,928 B (sha256 `ab516cdb…`), bootloader `219e7ee1…`, partition table `7f00b6c0…` |
| switch-pad core | host g++ (`make -C firmware test`) | 84 checks, 0 failures |
| Host suite incl. `usb://` vs the pty simulator, flashing with a fake esptool | ubuntu-surface | 116 tests OK |
| sys-autopilot build from source (reference only; the install uses the 1.5.0 release exefs) | Yoga, `tools/build_autopilot.ps1`, source `6ca6aac` | sys-autopilot.nsp 238,539 B (sha256 `ea5dfc67…`), `check_nsp` ok |

## 11. r5: PARKED fallback (built 2026-11-16, superseded by option d the same night)

**r5 (sysmodule 0.3.0, `1cda5f7`, built, never installed).** Kept as the fallback if sys-autopilot fails on
fw 23.5.0 (it needs a new order; rebuild first, since the core source now also accepts `scene_host`). It is r4 plus three changes. The techniques were
re-implemented after studying sys-autopilot (TooTallNate) `source/common/server.c`, `power.c` and `main.c`; no
code was copied (`docs/PROVENANCE.md`).
- **Network changes.** nifm's IP is read every 2 s. On ANY change, including none→IP, the listeners and
  connections close and are rebuilt (`sr::IpWatch`, `kIpPollMs`). A listener bound before DHCP, or across a
  network teardown, can silently stop accepting; that is the probable root cause of Smoke 01.
- **Sleep and wake (psc:m).** On ReadySleep, ReadySleepCritical or ReadyShutdown:
  - sockets close;
  - held input is released;
  - the HDLS device detaches and its work buffer is released;
  - log and status writes stop (no fsp/bsd IPC);
  - then it acknowledges.
  On wake it acknowledges, and on Awake it reopens. A private PSC module id (0x5352) with Fs as a dependency.
- **Socket sizes.** Nintendo's default UDP buffers (0x2400/0xA500) and fixed TCP buffers (0x8000/0x10000), 4
  sessions, the 2 MiB heap.

**Re-activation, if ever ordered.** Stop sys-autopilot (A in ovlSysmodules: it also deletes its flag), add back our
`flags/boot2.flag` and set `scene_host=127.0.0.1` (or remove the key) in `config.txt` through a receipted session,
then reboot. One virtual controller at a time: `autopilot.py` refuses input (`two_drivers`) while port 47130 answers.

## 12. Option (d): sys-autopilot drives the console (primary ruling 2026-11-16, ADOPTED)

The operator: "if it works we could just use it". sys-autopilot 1.5.0 (TooTallNate) owns the virtual controller.
Upstream release exefs `b132156a…` (233,732 B, zip `a409ee7d…`), installed as an internal tool and not
redistributed. Its license situation is recorded in `docs/PROVENANCE.md`. We add what it lacks, on the host:

| Need | Where | How |
| --- | --- | --- |
| Online gate (fail closed) | `tools/host/scened.py` + `autopilot.py` | The NRO (`scene_host=172.30.254.66`) sends `AUTH` + `SCENE` lines to the host receiver on TCP 47131 (private peers, token, max 4 connections, no replies). `scened.gate` is the core's verdict, ported exactly: no report / stale > 500 ms / guards failed / NSO family / Deluxe-online-not-local-wireless refuse. Every injected input asks the daemon; an unreachable daemon refuses (`no_scene_receiver`). Release and reset are never gated. A watchdog clears all input within ~100 ms when the gate closes while anything is held or a macro plays. |
| Input | `autopilot.py` | tap → `/input/tap`; press/stick → hold/stick with a HOST-side lease; HOME/CAPTURE refused as plain buttons. |
| Macros | `autopilot.py` | `.edenmacro.json` played host-side: the state of each sample goes out as hold/release/stick calls at frame/60 s. **HTTP-timed (LAN jitter), NOT frame-exact**: fine for menus; `macro.status` reports `late_ms_max`. Frame-precise hardware rows need a separate plan later. |
| LAUNCH (re-ruling 2026-11-16) | `autopilot.py` + `homegate.py` | Only when NO fresh scene report exists (none for 5 s, so the game isn't running; an unreachable receiver refuses). Then HOME, a capture still, the **reviewed image gate** (SSBU art in slot 1, the cyan selection ring on slot 1, the selected-title label "Super Smash Bros. Ultimate"), re-check that no report arrived, and A. A failed still stops after HOME (harmless). |
| TERMINATE | same | HOME, still gate (SSBU suspended at slot 1), X, still (the close dialog must have changed the screen, else stop: A would launch), A, then wait for the reports to stop. |
| Logs | `autopilot.py` | `switch.list/file/fetch` over `/files`, limited to the sysmodule's six roots; no `..`. |
| Updates | `tools/card/own_install.py` | Card sessions only (receipts). No over-the-air writes through sys-autopilot in v1. |

**Image gate (`homegate.py`).** The references were cut from the Smoke 01 still `000-now.png`: HOME, SSBU first
and selected. They're stored in `tools/host/refs/home-first-tile-ssbu/` (`review.json`: source sha256, boxes,
reviewer).
- The ring and label compare binarized cyan masks, so brightness doesn't move them. The art compares RGB.
- Calibration (tests): positives (JPEG q60 double pass, 6 % brighter, 1080p) score ≤ 0.6× each threshold.
  Negatives (cursor on slot 2, another game in slot 1, another title, black/grey/mirrored frames) are refused.
- The gate refuses if `reviewed_by` is empty.

**Residual risk (ACCEPTED 2026-11-16).** Anyone holding sys-autopilot's token can drive input directly and bypass
the host gate. Mitigations:
- The token is host-only: `~/.config/switch-remote/token`, 0600. It is never printed or committed; on the card it
  sits in `config/sys-autopilot/config.ini`.
- The console is internet-blocked (§6a).
- The gate protects every input the agents send.

**Boot safety.** The FIRST install adds no `boot2.flag` for sys-autopilot: its `__appInit` diagAborts (a fatal at
boot) if any service init fails on fw 23.5.0 / AMS 1.12.2p1, which is unverified.
- The first start is by hand in ovlSysmodules (our `toolbox.json`, `requires_reboot: false`): **A, then Y once**.
  ovlSysmodules' A also CREATES the flag (`gui_main.cpp:96-100`); Y deletes it (`:105-112`).
- Once it has served `/status` on this console, a later receipted session adds the flag (job
  `card-jobs/d2-autopilot-boot2`).
- Our sysmodule's flag is removed in the same first session, so it no longer autostarts: one virtual controller.

**The card session** (one, through `own_install.py`, job `card-jobs/d1-autopilot-1.5.0-nro-3d6e1bb`):
1. Read r3's logs, read-only.
2. ADD the sys-autopilot exefs, our toolbox.json and config.ini (token, `log = true`). No boot2.flag.
3. REMOVE `4200000000005352/flags/boot2.flag`.
4. UPDATE the NRO (0.2.0) and `config.txt` (`scene_host`).
5. The operator reboots, does A then Y in ovlSysmodules, and leaves the HOME menu with SSBU first.
6. `resume_smoke.ps1 -Phase smoke` starts `scened.py` (user unit `sr-scened`), finds sys-autopilot on 4150, and
   runs preflight, `driver-check` (a tap on HOME must be REFUSED), launch, and `lease-check`.

## 13. USB wired controller on an ESP32-S3 (primary direction 2026-11-16 ~03:50Z; the PRIMARY input route)

The operator: "use [the Freenove ESP32-S3 board] to control the switch via USB so you don't send over network …
then you wouldn't need the sysmodule". The board is plugged into the Switch DOCK as a wired controller. The host PC
commands it over the board's second USB port (a UART bridge). There is no network and no sysmodule for input.

**Hardware facts (verified).** Source: the Freenove repo `Freenove/Freenove_ESP32_S3_WROOM_Board` (FNK0085,
the kit with camera and SD card): `C/C_Tutorial.pdf` p.145 "Difference between USB-OTG and USB-UART Interfaces",
`ESP32S3_Pinout.png`, `Board.jpg`.
- **Two USB-C connectors.**
  - **OTG** is the one beside the EN/RST button. It is wired straight to the ESP32-S3's native USB (D− GPIO19,
    D+ GPIO20); the tutorial says it can "simulate … gamepad".
  - **UART** is the one beside the BOOT/IO0 button. It goes through the on-board CH343 USB-serial bridge to UART0
    (TX GPIO43, RX GPIO44) and is "primarily used for code upload and serial communication".
  - With the antenna up, OTG is left and UART is right. (The pinout PNG swaps the D+/D− labels; the tutorial matches
    Espressif's GPIO19 = D−.)
- **Native USB.** Full-speed device (ESP32-S3 USB-OTG, TinyUSB in ESP-IDF).
- **Wiring:**
  - the OTG port → a USB-A port on the Switch dock (a USB-A to USB-C data cable);
  - the UART port → the host PC (ubuntu-surface, where the capture card and OBS are). It appears as a CH343
    `ttyACM` device (VID 1a86).
- **UNVERIFIED: power with both ports connected.** The repo publishes no board schematic, so whether VBUS from the
  dock and the PC are isolated (diodes) is unknown. Both ports powered at once is the board's documented
  "USB-OTG ↔ USB-UART" tutorial setup (p.146: "Select the COM port numbers corresponding to USB-UART and USB-OTG
  respectively"), so it is a normal use. No change is planned.

**The controller descriptor (evidence).**
- **Device.** A HORI POKKEN CONTROLLER: VID 0x0F0D, PID 0x0092, HID, one interface with an interrupt IN (0x81) and
  OUT (0x02) endpoint of 64 bytes, `bInterval 1`, an 86-byte report descriptor.
- **Report.** 8 bytes: 16 buttons (Y B A X L R ZL ZR − + L3 R3 HOME CAPTURE), a 4-bit hat (8 = neutral), LX LY RX
  RY as 0-255 (0x80 centre; 0 = up/left), and a vendor byte.
- **Sources:**
  - GP2040-CE `headers/drivers/switch/SwitchDescriptors.h` at `0195793` (MIT, © 2021 Jason Skuby). The descriptor
    bytes are reused with that notice (`firmware/THIRD_PARTY.md`).
  - progmem/Switch-Fightstick README at `af49d546` (MIT): since system update 3.0.0 the Switch treats the Pokken pad
    as a Pro Controller, and the 16-button variant exposes HOME and Capture.
  - ns2bench's Pico firmware (`a105ca7`, GPL-3.0, studied only) uses the same 0f0d:0092 identity, a UART host link
    at 921,600 baud, and a 1 s neutral watchdog.
- **Other firmware, licences checked.** sorasen2020/SwitchControllerESP32 (MIT, an Arduino ESP32-S3 library) and
  celclow/SwitchControlLibrary (MIT). Nothing from them is copied.
- **ASSUMPTION: the poll rate.** The console polls USB pads at about 8 ms (125 Hz), according to third-party pages
  (ProGCC latency notes; a TDC Mate note), not Nintendo. Settled at the smoke by counting capture frames.
- **ASSUMPTION: no setting needed.** The pad needs no "Pro Controller Wired Communication" setting. If the Switch
  ignores it, the operator turns that setting on (System Settings → Controllers and Sensors).
- **Identity.** We present HORI's IDs because the console accepts only known controllers. Internal use on the
  operator's own console.

**Firmware (`firmware/`, ESP-IDF v5.5.6 + `espressif/esp_tinyusb`, built on the Yoga).**
- **Protocol.** The same line protocol as our sysmodule's host channel (docs/PROTOCOL.md), over UART0 at 921,600
  baud: `AUTH` (accepted: the link is physical), `PING`, `STATUS`, `VERSION`, `BTN <bit> press|release|tap <ms>`,
  `STICK l|r <x> <y> <ms>`, `RESET`, `MBEGIN`/`MEV`/`MPLAY`/`MSTATUS`/`MSTOP`, `BYE`. `BTN` also takes HOME (18)
  and CAPTURE (19), for the host's fixed sequences.
- **Frame-exact sequences.** Macro samples (the sysmodule core's `sr::Macro`, reused) play on the ESP32's own clock
  at exactly 60 Hz: frame n starts at n × 1/60 s, in esp_timer µs. The report task refreshes the HID report every
  1 ms. A change scheduled for frame n is therefore visible to the console at its next poll after n/60 s. There is
  no host jitter; the remaining skew is the console's poll interval (≤ ~8 ms, measured at the smoke). The game's own
  frames are not synchronized to this clock; nothing on a wired pad can see them.
- **Watchdog.** If no line arrives for 1 s, everything goes neutral, including a playing macro. The host client
  sends `PING` every 300 ms while it is connected. A crashed host therefore releases the pad within 1 s.
- **Status.** `VERSION` gives the firmware name and version and the USB identity. `STATUS` gives `usb_mounted`,
  `reports` sent, `watchdog_trips`, the macro state, `last_rx_ms` and uptime.
- **Portable core.** `firmware/core/` is host-tested with g++: report bytes, hat, sticks, leases, watchdog, macro
  timing and the protocol. A host simulator (`firmware/host_sim/`) serves the same core on a pseudo-terminal for the
  Python client tests.

**Host backend `usb://<serial device>`** (`tools/host/usbpad.py`, `make_client`, runner factory, `switch_test.py`,
`smoke.py --backend usb`).
- **Interface.** The same edenctl surface as the other backends: `input.*` maps to BTN/STICK/RESET, and
  `macro.play` uploads the samples and plays them on the device timer (unchanged runner macros).
  Screenshots and GIFs stay on the capture card / OBS.
- **LAUNCH / TERMINATE / DISMISS.** Controller button sequences with capture-card still checks (`homegate.py`;
  §12's sequences, shared code):
  - LAUNCH: HOME → the HOME first-tile gate → A.
  - TERMINATE: HOME → gate → X → a still that shows the dialog → A.
  - DISMISS: A only when the still is NOT the HOME first tile, because on HOME, A would launch.
- **No online gate (primary direction).** A plain wired controller needs none. The NRO's scene report is kept as
  INFORMATION (status shows `scene` when `scened.py` runs).
- **Logs and file updates.** Through the OPTIONAL sys-autopilot (`files=autopilot://IP`), else card sessions.
- **Serial access.** The host user is not in `dialout`. The operator authorizes one of two fixes:
  - a udev rule giving the CH343 (1a86) `plugdev`, which the user is in;
  - or `usermod -aG dialout`.

**Built and host-tested 2026-11-16** (details in §10): the firmware at `433f357`, 0 warnings; core 84 checks;
the `usb://` client against the pty simulator, including device-clock macro timing (within ±3 ms when the host is idle, ±10 ms asserted under load: the simulator is not real-time) and the 1 s
watchdog; flashing against a fake esptool.

**Flashing.** `tools/host/flash_pad.py` (via `resume_smoke.ps1 -Phase flash`) runs esptool 4.13.0 (a pinned venv on the host the board is plugged into) over the
UART port: `write_flash` of bootloader, partition table and app, with esptool's own hash verification. The receipt
holds the firmware sha256s, the esptool output and the chip MAC.

**GC mode (0.2.0, 2026-11-17, docs/GC-MODE.md).** The operator: "Just add a gc mode to esp32 you can switch to on a
dime". `MODE gc` makes the board a GameCube Controller Adapter (057E:0337, our pad on port 1); `MODE pro` returns to the
HORI pad. The switch is a USB soft-detach, a descriptor swap and a re-attach. Pro mode is the power-up default, with
the same bytes as 0.1.2. It exists for catalog rows that need a GameCube controller (ON-F006, CH-F004).

**Consequences for options (d)/(c).**
- sys-autopilot and our sysmodule are no longer needed for INPUT.
- The card session: REMOVE our sysmodule's `boot2.flag` and UPDATE the NRO and config. sys-autopilot becomes an
  OPTIONAL extra job, for remote log reads and file updates without UMS/DBI.
- The host gate (`scened.py`) still guards the `autopilot://` route if that is ever used for input.

## 13a. The second option: Raspberry Pi Zero 2 W in USB gadget mode (operator note ~03:53Z; evaluated)

**Facts.**
- **Ports.** A Zero has micro-USB ports; "one … is labelled PWR IN and is reserved for power input only, but you can
  use any of those labelled USB" (raspberrypi/documentation `computers/getting-started/setting-up.adoc`). On the
  Zero 2 W that leaves ONE data port.
- **Gadget mode.** `dtoverlay=dwc2,dr_mode=peripheral` (raspberrypi/firmware `boot/overlays/README` @`896fc67d`),
  plus configfs `libcomposite` with `usb_f_hid` → `/dev/hidg0`.
- **ns2bench** (`a105ca7`, GPL, studied only) presents the same HORI identity this way. It lists the Zero 2 W as
  "same daemon, gadget and FunctionFS code; **untested**" (`docs/REFERENCE.md`); only a Luckfox board is tested.
  Its setup notes: "A board powered by the console's USB loses power when the console sleeps, and boots again when
  it wakes."

**Comparison.**

| | ESP32-S3 (Freenove FNK0085), §13 | Pi Zero 2 W gadget |
| --- | --- | --- |
| Wiring to the console | OTG port → dock | the one data port → dock |
| Command link to the PC | the board's second USB port (CH343 UART): **wired, no network** | Wi-Fi (a **network**, which the operator wanted to avoid), or GPIO14/15 UART through a 3.3 V USB-TTL adapter. Zero 2 W boards usually ship without headers, so that means soldering plus an adapter |
| Timing | Macro frames on a µs device clock; the report refreshes every 1 ms. Deterministic, no OS | Linux userspace: SCHED_FIFO + `clock_nanosleep(TIMER_ABSTIME)` + `mlockall`. Typical wake-up latency is well under 1 ms; tail spikes of several ms under SD/Wi-Fi interrupts are possible on the stock non-RT kernel (unmeasured) |
| What dominates either way | the console's own poll (~8 ms, ASSUMPTION §13) | the same, plus the tail spikes |
| Console sleep (powered from the dock) | reboots in well under a second | reboots in tens of seconds, and a power cut on a written SD card risks corruption (mitigation: power it from PWR IN, or a read-only root) |
| Build effort left | **built and host-tested** (`433f357`); remaining: the operator's serial-access fix, then flash and plug | microSD + Raspberry Pi OS Lite; overlay + configfs gadget script; Wi-Fi/SSH or UART soldering; the daemon cross-built for aarch64. The daemon is small, because the core is reused (below) |
| Risk | Freenove publishes no schematic (VBUS isolation unverified); TinyUSB/descriptor acceptance unverified until plugged in | an untested board per ns2bench; a network link; SD and boot-time issues |

**One API for both.**
- The Pi daemon would be the TCP mode of `firmware/host_sim/padsim.cpp`, which is already built and tested: the
  same `swpad::Pad` core and line protocol, the first line `AUTH <token>`, one client, the 1 s watchdog. It writes
  each report to `/dev/hidg0` instead of the log.
- `pi://HOST[:47140]` (`usbpad.PiClient`, tested against padsim's TCP mode: same API, token refused,
  dropped-link→neutral) and `usb://TTY` share everything else, so the runner, macros and sequences are unchanged.
- With a GPIO UART adapter instead of Wi-Fi, the Pi would simply be `usb://<adapter tty>`.

**Recommendation: straight to the ESP32-S3; the Pi is the fallback.**
- The ESP32 path is already built and tested. Its remaining steps (one sudo serial fix, two cables, `-Phase flash`)
  are fewer than the Pi's (an SD image, gadget config, a network or soldering, an aarch64 daemon build). So the Pi
  would not give us a working controller sooner.
- The ESP32 path is the one the operator asked for: wired, no network, deterministic timing.
- Build the Pi route only if the ESP32-S3 can't present the controller, e.g. the console rejects TinyUSB's
  enumeration, or the board is the single-port "Lite" variant. Linux's dwc2 is an independent USB stack, which makes
  it a meaningful second try. Its Wi-Fi link stays LAN-only, with the token.

## Requirement trace

| Operator clause | Design | Test |
| --- | --- | --- |
| "write an nro in its own repo" | own repo; NRO scene reporter, cdylib, model/runtime split | `scene_line_format`, `guard_bytes_are_64` |
| "lets you remote control the switch like you do in eden" | HDLS virtual pad; edenctl leases and macros; edenctl-compatible client | `test_input_button_tap_maps_to_btn`, `test_macro_events_parity_with_eden_file`, `test_disconnect_releases_held_input` |
| "a native screenshot feature that sends the screenshot over the wire" | capture card through OBS or capd behind `ui.screenshot` | `test_write_png_scales_to_width`, `test_screenshot_goes_to_capd`, `test_switch_client_routes_screenshots_to_obs` |
| "so you can see where you are and iterate" | runner backend; status from both sides | `test_runner_factory_switch_scheme`, `test_status_merges_sysmodule_and_capture` |
| "it would adapt eden automation" | `switch://` client in the unchanged runner (native factory), same rows and segments | `test_macro_events_parity_with_eden_file`, `test_runner_factory_switch_scheme` |
| "this way you can do hardware testing too" | LAN server, token, private peers, fail-closed gate | `peers_are_private_only`, `config_requires_token`, `test_online_scene_refuses`, `test_stale_scene_closes_the_gate_and_releases_input` |
| "reconnecting via dbi as needed if there's a crash" | crash detection, logs, relaunch, playbook installers | `test_dropped_connection_reports_not_running`, `test_list_and_file_roots` |
| "it should have a sysmmodule s that you can launch the game yourself and get the logs if it crashes" | sysmodule LAUNCH/TERMINATE/RELAUNCH with the first-tile check; LIST/FILE over crash and plugin logs | `test_launch_only_without_application`, `test_launch_refused_unless_ssbu_is_the_first_tile`, `test_terminate_and_exit_count`, `test_list_and_file_roots` |
| "for the screenshot part I mean and hardware gifs" | capd recordings with a wall-clock sidecar; GIF cut and encode | `test_gap_counts_missing_frames`, `test_card_no_signal_screen_is_detected`, `test_cut_window_from_sidecar` |
| "OBS may still be better for the photo/gif part" | OBS primary when it runs (stills, record, replay buffer); capd fallback | `test_finds_the_card_source_and_screenshots`, `test_record_and_replay`, `test_pick_backend_prefers_obs_then_falls_back` |
| "we want it to be compatible with the sysmmodule plugin so we don't have to reboot forchanges" | requires_reboot false; hard-kill-safe lifecycle; PUT with receipts for own files; RESTART self-relaunch | `test_restart_reconnects_to_a_fresh_instance`, `test_put_updates_own_file_with_backup_and_receipt`, `test_shutdown_detaches_and_exits`, `test_start_sweeps_interrupted_put_stage` |
| "online testing should still be possible over local wireless lobby. Those paths should be shared with nso path" | gate allows the local-wireless family, refuses NSO, fails closed when ambiguous; one report path for both; smoke over a local-wireless lobby | `test_local_wireless_lobby_allows_input_nso_refuses`, `test_online_scene_refuses` |
| "use [the ESP32-S3 board] to control the switch via USB so you don't send over network … then you wouldn't need the sysmmodule" | §13: HORI-identity wired pad on the OTG port, UART line protocol, device-timed macros, 1 s watchdog, `usb://` backend, esptool flashing with receipts | `report_layout_matches_the_hori_descriptor`, `macro_frames_follow_the_device_clock`, `watchdog_neutralizes_after_one_second_of_silence`, `test_device_timed_macro`, `test_keepalive_holds_a_long_press_and_silence_releases_it`, `test_launch_presses_home_then_a`, `test_flash_receipt` |
| "if it works we could just use it instead to drive the hardware tests" (sys-autopilot; ADOPTED as option d) | sys-autopilot drives; host gate from the NRO's reports (`scened.py`); image-gated LAUNCH/TERMINATE; host-timed macros; logs over `/files`; r5 parked | `test_every_closed_verdict_refuses_input_without_a_request`, `test_watchdog_clears_held_input_when_the_gate_closes`, `test_verdicts_match_the_sysmodule_core`, `test_launch_when_no_report_and_home_first_tile`, `test_terminate_without_a_dialog_never_presses_a`, `test_cursor_on_slot_two_is_refused`, `test_option_d_install_then_a_later_boot2_session`, `scene_host_is_private_only` |

## Open points

Each is settled at the first hardware smoke:
- HDLS frame timing and player slot.
- Loopback TCP.
- The first-tile rule: the ns record order against a capture of the HOME menu.
- The card's latency.
- `ns:dev` TerminateApplication on fw 23.5.0.
- A live OBS comparison of CPU cost and drops.
- Option (d):
  - Does sys-autopilot 1.5.0 start and serve `/status` on fw 23.5.0 / AMS 1.12.2p1? Its newest evidence is fw 19.0.1.
  - Do the NRO's scene reports reach 172.30.254.66:47131 over the LAN within the 500 ms freshness window?
  - Does the image gate pass on a fresh HOME capture after the reboot? A suspended-game tile may differ.
  - What do the X dialog stills look like (keep them as references)?
  - The HTTP input jitter (`late_ms_max`) on menus.
