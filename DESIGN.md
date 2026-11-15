# switch-remote: design (Phase 0, revised)

2026-11-15. Drive the physical Switch running Smash Ultimate 13.0.5 as agents drive Eden:
inject controller input, launch and terminate the game, read crash logs, and see the screen.
Playbook followed: `ssbu-mod-playbook` `3332c0d`. Facts carry a source; **ASSUMPTION**
marks what is not verified yet, with the check that will settle it.

Revision 2 (21:4xZ) applies three operator rulings received after revision 1: a sysmodule is
in v1; screenshots and GIFs come from the HDMI capture card on the host; caps:sc and NVN
readback are dropped. Revision 1's in-game input path is kept as the documented fallback (§7).

## The split

| Part | Does | Runs |
| --- | --- | --- |
| **sysmodule** (`sysmodule/`, C++17 + libnx) | LAN server; virtual Pro Controller (`hid:dbg` HDLS); macro player; launch/terminate; crash and plugin logs | Atmosphere boot2 background process, survives game crashes |
| **in-game NRO** (`src/`, Rust Skyline plugin) | Reads the game's mode word and Online Deluxe's ONLINE bit; reports the scene to the sysmodule over loopback 10×/s. No hooks. | Inside Smash |
| **host** (`host/`, Python) | Capture-card screenshots and recordings (`capd.py`); edenctl-compatible client; `switch://` runner backend; GIF cutting | ubuntu-surface (the card is here) |

## 1. Input: a virtual controller from the sysmodule

- **Service.** `hid:dbg`: AttachHdlsWorkBuffer (cmd 324), AttachHdlsVirtualDevice (330),
  SetHdlsState (332), DetachHdlsVirtualDevice (331); 7.0+ (libnx `b156b477`
  `nx/source/services/hiddbg.c:466-728`). `HiddbgHdlsState` carries 64-bit buttons and both
  sticks (`hiddbg.h:109-120`). A Pro Controller is `HidDeviceType_FullKey3` (sys-botbase
  `ae13548c` `source/commands.c:13,189-228`, studied only: GPL-3.0). The input is system-wide,
  so it reaches the HOME menu as well as the game.
- **Buttons.** Eden macro bits 0–15 equal `HidNpadButton` bits 0–15 (Eden
  `src/yuzu/macro_file.cpp:323-331`); stick-direction bits 16–23 are derived from the sticks.
  Host input may not press HOME (bit 18) or Capture (bit 19): the core refuses them.
- **Leases and macros, as edenctl.** Per-button and per-stick leases of 1–10000 ms; a new press
  replaces the deadline; `RESET` releases all (`CLIControl.md` "Controller input"). A macro's
  samples are uploaded and played by the sysmodule at 60 Hz of its own clock; playback owns the pad.
- **Timing.** The sysmodule cannot see game frames. A 1 kHz pad thread applies the state due at each
  60 Hz boundary. **ASSUMPTION:** hid samples the HDLS state often enough that every state held for
  at least one frame reaches the game, with a fixed latency. sys-botbase's defaults (50 ms clicks,
  17 ms poll) suggest at least two frames per state is safe (`commands.c:22-24`). **Measured on
  hardware** with the capture card: a recorded macro against frame timestamps of its effects. If it
  is not frame-accurate enough for our macros, the in-game input path (§7) is built.
- **Player slot.** **ASSUMPTION:** the virtual pad takes the next free slot (P2 when a real Pro
  Controller is P1). Checked at the first smoke; `hiddbgApplyHdlsNpadAssignmentState`
  (`hiddbg.h:449`) is the tool if it must be P1.

## 2. Online gate: fail closed

Host input reaches the virtual pad only while the latest scene report from the in-game NRO is
under 500 ms old, the NRO's version and site guards passed, and the report says offline.
Anything else (no report, stale report, game not running, guards failed, online) refuses the input,
releases every held input, stops a macro with `error=<reason>` and answers `ERR inject_refused <reason>`.

- **Online signal.** The game's mode word at `main+0x53050f0`. The game itself masks it with
  `0x7fff0000` and compares families: `main+0x1360928` is `adrp; ldr w8,[x8,#0xf0];
  and w8,w8,#0x7fff0000; cmp …` (stock 13.0.5 `text.bin`, `tools/find_refs.py`). TrainingRooms
  calls `0x0701xxxx` the online family (`rc15-integration/src/model/lobby_flag.rs:59-63`).
  The gate refuses the wider `mode & 0x7f000000 == 0x07000000`. Online Deluxe's
  `ssbu_online_deluxe_online_context_v1` ONLINE bit also refuses
  (`third_party/deluxe-plugin-sdk/abi/src/lib.rs:105-121`). Restriction `SR-ONLINE-01`.
- **NRO guards.** Version `13.0.5`, and the 64 bytes at `main+0x1360928` (the game's own reader of
  the mode word) equal the 13.0.5 bytes, else the report says `guards=0` and the gate stays closed.
- **Channel.** The NRO connects to `127.0.0.1:<port+1>` and sends
  `SCENE <seq> <mode> <deluxe> <guards>` after `AUTH <token>` (same config file). **ASSUMPTION:**
  loopback TCP between two processes works on the console's bsd; settled at the first smoke.
- **The launch exception** (needs the operator's confirmation, see §3): the sysmodule's own
  fixed launch sequence presses HOME/A when no application process exists. No online play is
  possible then; host input stays refused.

## 3. Launch and terminate

- **No clean launch API.** `ns:dev` LaunchApplicationForDevelop is blocked on 10.0+ and the
  storage-id variant on 18.0+ (libnx `ns.c:2422,2444`); `appletRequestLaunchApplication` needs an
  Application or LibraryApplet caller, `appletCreateApplication` the SystemApplet (`applet.h:1451,1895`);
  `pm:shell`/`pgl` LaunchProgram creates a process am doesn't know (`pm.h:129`, `pgl.h:55`;
  **ASSUMPTION** headless). SysBot.NET (AGPL, read only) starts games by pressing A on the HOME menu.
- **Pick: HOME-menu sequence.** `LAUNCH`, only when `pm:dmnt` GetApplicationProcessId finds no
  application: HOME, wait, A on the first tile (the last-played title). Then the sysmodule checks
  `pm:info` GetProgramId equals `01006A800016E000` within 20 s, else answers `ERR launch_failed`
  and terminates what started. **ASSUMPTION:** SSBU is the first tile; the host checks with a
  capture-card screenshot before `LAUNCH` and the receipt keeps it.
- **Terminate.** `ns:dev` TerminateApplication (cmd 6, no version gate, `ns.c:2407-2408`);
  fallback `pgl` TerminateProcess(pid) (`pgl.h:56`; Atmosphere creport kills crashed processes
  this way on 10.0+). `RELAUNCH` = terminate, wait for exit, `LAUNCH`.
- **Detection.** `pm:dmnt` GetApplicationProcessId + `pm:info` GetProgramId (sys-botbase
  `commands.c:34,82`); a `pgl` event observer reports exits and crashes (`pgl.h:67-71`,
  `PmProcessEvent_Crash=3` `pm.h:42`). `STATUS` shows `app=running|none pid= crashes=`.
- **The error screen after a crash.** **ASSUMPTION:** only dismissed by A. The host may press A
  (system-wide input) only through `DISMISS`, allowed only when no application runs, same rule as launch.

## 4. Logs

`LIST <root> [dir]` and `FILE <root> <offset> <max> <path>` read files under fixed roots only:

| Root | SD path | Source |
| --- | --- | --- |
| `crash_reports` | `/atmosphere/crash_reports` | creport `%011lu_%016lx.log`, plus `.jpg` for application crashes (AMS `1869018e` `creport_crash_report.cpp:310,356-374`) |
| `fatal_reports` | `/atmosphere/fatal_reports` | fatal `fatal_task_error_report.cpp:82,144` |
| `fatal_errors` | `/atmosphere/fatal_errors` | `fusee_fatal.cpp:33` |
| `erpt_reports` | `/atmosphere/erpt_reports` | `erpt_srv_reporter.cpp:27` |
| `ultimate` | `/ultimate` | plugin `events.log` files |
| `skyline` | `/atmosphere/contents/01006A800016E000/romfs/skyline` | Skyline's own files |

The card had 706 crash reports (newest 2026-11-10, an SSBU crash) and 557 erpt reports
(read-only listing 2026-11-15). Paths reject `..`, absolute paths and backslashes; reads are 64 KiB
chunks. Nothing is written or deleted.

## 5. Screenshots and recordings: the capture card

- **Device.** `/dev/video4`, AVerMedia Live Gamer Ultra 2.1 (UVC), on ubuntu-surface; the user has
  an ACL `rw` on it. Formats include MJPEG 1920x1080@60/120 and YUY2/NV12 up to 3840x2160
  (`evidence/capture-probe/formats-video4.txt`). No ffmpeg or v4l2-ctl on the host; GStreamer 1.28.2
  with Python bindings is used.
- **Daemon.** `host/capd.py serve` keeps one pipeline open (MJPEG 1920x1080@60) and holds the last
  frame. `ui.screenshot` writes a PNG (1280 wide by default, the Eden reference size).
  Measured 2026-11-15 21:39Z: 90–131 ms per call including a fresh client process; a cold one-shot
  grab took 730 ms. Signal: none yet (the console is in hekate UMS); the card sends black, then its
  own "No Signal" screen after about 1.3 s, which `capd` recognises (`no_signal: true`).
- **Recordings.** `recording.start/stop` add a stream-copy branch (MJPEG into Matroska) with a
  per-frame sidecar of host wall times. Measured: 0.68 s of daemon CPU over the whole probe
  (screens + 5 s recording), 303 frames in 5 s, 0 dropped; a standalone 10 s stream copy used
  0.12 s CPU (1%) and 28 MB RSS. The input timeline (the client's `actions.jsonl`) and the sidecar
  share the host wall clock, so a clip is cut at the observed event. GIFs are encoded from the MKV
  (`host/gif.py`) under the guard at nice 19, or on the Yoga/XPS: README 1280 wide ≤10 s ≤15 fps;
  evidence 640 wide ≤15 fps ≤10 MB (playbook GIFS.md).
- **Latency of the card.** **ASSUMPTION:** a few frames HDMI→USB. Measured at the first smoke
  (an input's send time against the first frame that shows it); recorded per run, not hidden.

## 6. Network and host side

- **Sysmodule server.** `bsd:u` with a small socket config (ldn_mitm's ~128 KiB transfer memory,
  `ldnmitm_main.cpp:51-63`, studied only). One host client at a time on `0.0.0.0:<port>` (default
  47130), scene channel on `127.0.0.1:<port+1>`. Opt-in: nothing starts unless
  `sd:/ultimate/switch-remote/config.txt` holds `token=` (16–64 chars); peers outside private IPv4
  ranges are closed before they send a byte.
- **Protocol** (`docs/PROTOCOL.md`): ASCII lines, `OK …`/`ERR <code> …`, binary payload after
  `OK file`. Methods: `AUTH PING STATUS BTN STICK RESET MBEGIN MEV MPLAY MSTATUS MSTOP LAUNCH
  TERMINATE RELAUNCH DISMISS LIST FILE BYE`.
- **Host client.** `host/switch_remote.py` answers the edenctl methods the playbook runner calls
  (`runner.py:1019-1219`): `input.*` and `macro.*` go to the sysmodule, `ui.screenshot` and
  `recording.*` go to `capd`, `status` merges both. The runner picks a client from the instance's
  `socket` string (`runner.py:953-961`); `playbook-patch/` adds `switch://host:port` there (6 lines,
  not applied to the playbook). `host/switch_test.py run --host <ip>` writes `instances.json` and
  calls the unchanged `runner.py run`.

## 7. Fallback (documented, not built): in-game input

Replace hooks on the SDK exports `nn::hid::GetNpadState` (FullKey, Handheld, JoyDual) inside the
NRO. 13.0.5 main imports only these singular overloads (`rodata.bin`); Online Deluxe and
`smash_gamepad_overlay` call but don't hook them (`deluxe-0108/src/runtime/input.rs:15-40`; the
overlay's card log, "using raw HID"). Built only if §1's measurement shows HDLS can't hold frame
timing; the Rust model for it (`src/model/input.rs`, `protocol.rs`) stays host-tested.

## 8. Safety

- **Boot.** boot2 starts SD sysmodules last and aborts boot on OutOfResource/OutOfMemory/LimitReached
  (AMS `boot2_api.board.nintendo_nx.cpp:152-155,457`). So: a small static heap (1 MiB), lazy service
  init after a 10 s delay, no `fatalThrow` anywhere, every failure logged to
  `sd:/ultimate/switch-remote/sysmodule.log` and the module idles. Title id `4200000000005352`
  (free on the card; the ids there were listed read-only). **Recovery:** delete
  `atmosphere/contents/4200000000005352/` (or its `flags/boot2.flag`) in hekate UMS.
- **Permissions.** Service access: `hid:dbg bsd:u nifm:u fsp-srv ns:dev pgl pm:dmnt pm:info set:sys`
  (+ `sm:` by port). No debug syscalls.
- **Input.** Fail closed (§2). HOME/Capture never from the host.
- **Exposure.** Off by default; token; private peers; one client.
- **NRO.** No hooks, no heap after install, one thread on a static stack; one install line in
  `sd:/ultimate/switch-remote/events.log`; the diag build logs every mode change.

## 9. Crash and recovery workflow

1. The client pings every 2 s; the sysmodule's `STATUS` reports `app=none` and a `crashes=` count
   when the game dies (pgl event). The runner sees `running: false` and stops (`runner.py:1807-1820`).
2. `LIST crash_reports` / `FILE …` pull the new report (and its `.jpg`), plus `ultimate/*/events.log`.
3. `DISMISS` clears the error screen, `LAUNCH` starts the game, the client waits for `scene=offline`.
4. A broken plugin is reinstalled with the playbook installers (DBI MTP `upgrade2-mtp.py`, storage
   `1%3A%20SD%20Card`, or UMS `upgrade2-ums.py`): backup, device readback sha256, before/after table,
   only our files. The operator's hands: rebooting, DBI/UMS, a hung console, the first install.

## Requirement trace

| Operator clause | Design | Test |
| --- | --- | --- |
| "write an nro in its own repo" | own repo; NRO scene reporter, cdylib, model/runtime split | `scene_line_format`, `guard_bytes_are_64` |
| "lets you remote control the switch like you do in eden" | HDLS virtual pad; edenctl leases and macros; edenctl-compatible client | `leases_expire_and_never_zero`, `macro_plays_at_60hz_and_finishes`, `test_input_button_tap_maps_to_btn`, `test_macro_events_parity_with_eden_file` |
| "a native screenshot feature that sends the screenshot over the wire" | capture-card screenshots behind `ui.screenshot` | `test_write_png_scales_to_width`, `test_screenshot_goes_to_capd` |
| "so you can see where you are and iterate" | runner backend; status from both sides | `test_runner_factory_switch_scheme`, `test_status_merges_sysmodule_and_capture` |
| "it would adapt eden automation" | `switch://` client in the unchanged runner, same rows/segments | `test_macro_events_parity_with_eden_file`, `test_runner_factory_switch_scheme` |
| "this way you can do hardware testing too" | LAN server, token, private peers, fail-closed gate | `peers_are_private_only`, `config_requires_token`, `gate_fails_closed` |
| "reconnecting via dbi as needed if there's a crash" | crash detection, logs, relaunch, playbook installers | `test_dropped_connection_reports_not_running`, `launch_only_without_application` |
| "it should have a sysmmodule s that you can launch the game yourself and get the logs if it crashes" | sysmodule: LAUNCH/TERMINATE/RELAUNCH, LIST/FILE over crash and plugin log roots | `launch_only_without_application`, `parses_every_method`, `test_list_and_file_roots` |
| "for the screenshot part I mean and hardware gifs" | `capd` recordings with a wall-clock sidecar; GIF cut and encode | `test_gap_counts_missing_frames`, `test_card_no_signal_screen_is_detected`, `test_cut_window_from_sidecar` |

## Open points

- Needs the operator's confirmation (not blocking the build): the launch/dismiss exception to the
  fail-closed rule (§2, §3).
- Settled at the first hardware smoke: HDLS frame timing, the virtual pad's player slot, loopback TCP,
  SSBU as the first HOME tile, the card's latency, `ns:dev` TerminateApplication on this firmware.
- devkitPro: not installed on any host; an unpacked devkitA64 r30 / libnx 4.12.0 is on the Yoga
  under `smash-replay-export-campaign/grok-nro/private/devkitpro/` (research report). The
  sysmodule builds there.
