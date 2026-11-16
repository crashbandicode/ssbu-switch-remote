# HANDOFF: switch-remote (restart-safe)

Written 2026-11-16 ~05:40Z for a fresh session; updated ~05:50Z (Skyline TCP log capture + the heap candidate).
- **Worker:** switch-remote (Claude Opus 5.5, xhigh), spawned by the rc6-integration primary.
- **History:** the older, longer log is `docs/HANDOFF-history-2026-11-16.md`.
- **Repo:** `/home/user/ssbu-switch-remote`, branch `main`. No remote; **53 local commits, none pushed**.
- **Work dir:** `/home/user/ssbu-switch-remote-work`.
- **Evidence root:** `/home/user/smash-training-lobby/artifacts/switch-remote-card/`.
- **INBOX (where every post goes):** `/home/user/smash-training-lobby/artifacts/phase23/INBOX-opus-rc6-integration.md`.

## 00. switch-remote-2 progress (2026-11-16, newest first; supersedes §0/§3/§4 where they differ)

- **12:05Z NEW JOB: HARDWARE FEATURE LEDGER** (operator: "test that the features work on switch like we did with eden").
  Card as is (TR rc.15 `de563644` + Deluxe 0.10.8 `30cb25dd`). **No card writes; FTP log reads OK; no pushes.**
  - Out dir `/home/user/smash-training-lobby/artifacts/switch-hw-ledger-rc15/`: `PLAN.md` (a research worker is
    writing it from the Eden rc.15 final pass + TR `docs/menu/*` + ROUTES), then `LEDGER.md` (| feature | Eden
    evidence | Switch result PASS/FAIL/N/A | GIF |). FAIL = still + TR log excerpt (FTP). Milestone post every ~10.
  - Runner: `tools/host/hwstep.py --name Fnn-x --out <dir>/Fnn -- "press a 100" sleep:0.5 still:gate …` (OBS burst
    10 fps → GIF 640w ≤10 MB via Pillow; no ffmpeg on the Surface). Self-test OK (`selftest/`).
  - Run the Skyline TCP log during the session (`skyline_tcplog.py --label hwledger-1 --seconds 7200`).
- **12:00Z DONE, summary posted (INBOX line 38547):** g1 Deluxe 0.10.8 installed (`card-jobs/g1b-deluxe-0.10.8`, ok, 20
  others unchanged, rollback `ultimate/switch-remote/backups/pre-30cb25ddea78b2f1.…`); Smoke B PASS (main menu, 19 mains
  `(0x0)`, Deluxe `0.10.8-e7720fd64c92`). Console on HOME, SSBU selected. Waiting for the primary's next order.
- **11:55Z Smoke A PASS** (launch-1): card = TR rc.15 + Deluxe 0.10.7 + 17 others (19 NROs; the primary REMOVED our r1 NRO
  over Hekate UMS at 11:46Z, `artifacts/switch-remote-card/remove-r1-nro-ums/INSTALL.md`). Boot: splash → SaltyNX → movie
  (~t+60 s) → title (~t+200 s) → Start×2 → main menu (`ssbu-switch-remote-work/launch-1/shots/08-to-main.png`) →
  B, HOME, X → Close dialog (`09`) → A `--close-dialog` → HOME, SSBU selected (`11`).
- **Skyline TCP log** `artifacts/switch-remote-card/skyline-tcp-launch-1.log`: all 19 `Loaded`, every `main` `Finished
  (0x0)`, TR prints `plugin=training_rooms/0.2.0-rc.15 …` (line ~652) and `cpu_common_load … cpu_common=installed`.
- **HOME works** (11:49Z: HOME from hbmenu → HOME menu; `home-test-1/shots/14`). Wire check 18/18 PASS (INBOX 11:39Z).
  The 05:15Z hang was NOT a pad fault and did not recur after the reboot with r1 removed.
- **Operator rule (11:5xZ): drive at Eden speed.** Chain presses in one UART session (`/tmp/sr2-pad-probe.py STILL SETTLE
  "press x 100" sleep:0.6 …`, recreate if /tmp cleared), stills only at gates; skip movies with Start (ROUTES `to_main`).
  The image-gated SSBU A (`pad_nav.py --launch-ssbu`) and the `--close-dialog` A stay. `pad_nav.py` now allows `x`.
- **Next (primary's order):** FTP read TR events.log (rc.15 install line) → g1 Deluxe 0.10.8 under its conditions with **19
  plugins expected** (new job dir, e.g. `card-jobs/g1b-deluxe-0.10.8`; re-preflight must match the 19-plugin table) →
  Smoke B → ONE summary post.

## 0. Where things stand (read first)

- **The input route is an ESP32-S3 wired pad** (firmware switch-pad 0.1.2) in the Switch dock, driven from the Surface
  over its serial port. Control was confirmed on hardware.
- **Card:** **f1 done** (our sysmodule's `boot2.flag` removed). **g1 (Deluxe 0.10.8) is ON HOLD.**
- **Smoke A (current card) HUNG** at the Nintendo launch splash (2026-11-16 05:16Z). HOME is unresponsive. **The
  console needs the operator's power cycle** before anything else.
- **R1 load-path audit:** no definite hang in our NRO (INBOX 05:31Z).
- **Next:** the morning sequence (§4) after the power cycle.

## 1. Hardware facts

**Console**
- **Network:** wired LAN, **IP 172.30.254.79**, **LAN MAC <device-mac-1>** (read from System Settings →
  Internet, `ssbu-switch-remote-work/ftp-r1/shots/18-S12-internet.png`). Gateway/DNS <switch-ip>.
- **172.30.254.53 (`<device-mac-3>`) is a different Nintendo device.**
- **WAN blocked** by the operator (04:58Z); the LAN works.
- FW 23.5.0, AMS, SSBU 13.0.5. The HOME menu dims after ~5 min idle; a dimmed screen fails the image gates, so press
  **B** once first (harmless on HOME).
- **A wired pad cannot wake a sleeping console:** 10 HOME presses did nothing. The operator wakes it.

**Pad**
- **Board:** Freenove ESP32-S3 WROOM (FNK0085). Chip MAC <device-mac-2>, 16 MB flash, 8 MB PSRAM.
- **Ports** (C_Tutorial.pdf p.145; with the antenna up):
  - **UART** = right port, beside BOOT/IO0 → the Surface: `/dev/serial/by-id/usb-device-1`
    = `/dev/ttyACM0`, group plugdev via `/etc/udev/rules.d/99-switch-pad.rules`.
  - **OTG** = left port, beside EN/RST → the dock.
- **Firmware switch-pad 0.1.2**, commit `f9208f2`:
  - built on the Yoga by `tools/build_pad.ps1`, output `ssbu-switch-remote-work/pad/f9208f2/`;
  - `switch_pad.bin` 246,160 B, sha256 `4f07e1ca6cd8d816ac3399793773afa6c5c7659098af950d0f673f4232b1c6a4`;
  - offsets: bootloader 0x0 (`ed5486e4…`), partition table 0x8000 (`7f00b6c0…`), app 0x10000.
- **Flash receipt:** `artifacts/switch-remote-card/flash-f9208f2/flash-receipt.json`. All 3 regions hash-verified;
  `VERSION` = `OK version switch-pad 0.1.2 usb=hori-0f0d:0092`.
- **esptool:** 4.13.0 in `/home/user/venvs/esptool` (`PINNED.txt`).
- **USB:** it enumerates as a HORI POKKEN CONTROLLER 0F0D:0092; `usb_mounted=1`.
- **Console poll rate (measured):** 124.5 reports/s = 8.03 ms.
- **End-to-end latency:** press → visible in OBS, 127–190 ms.
- **Line protocol over UART at 921600:** the SR verbs (`BTN/STICK/RESET/MBEGIN/MEV/MPLAY/MSTATUS/STATUS/VERSION/PING`)
  plus the v0 verbs `press a+b <ms>`, `stick <x> <y> <ms>`, `clear`, `status`. HOME = `press home 120`.
- **Watchdog:** 1 s of silence → neutral.

**Capture:** OBS 32 is running; source "Switch" = `/dev/video4` (v4l2). Stills go through the client (OBS
websocket; password from the obs-mcp env, never printed).

**Pad tools** (`tools/host/`, run with `/usr/bin/python3 -I`)
- `pad_nav.py still LABEL [--out DIR]`: one OBS still plus the HOME/SSBU image-gate verdict.
- `pad_nav.py press BUTTON LABEL [--out DIR] [--settle-s S]`: **one press, then a still**. Buttons: up down left
  right b plus home a. Receipts go to `DIR/receipts.jsonl` (with `press_wall`).
  - **A guards:** A needs `--confirm-a "<what the last still showed>"`.
  - A is refused if the pre-press still is HOME with a game tile selected (ring detector + grey-background check,
    dimmed or not).
  - `--launch-ssbu`: A allowed only when the SSBU image gate passes (that is the launch).
  - `--close-dialog`: A allowed only when the still is NOT plain HOME (the "Close software" dialog).
- `pad_route.py ROUTE.json --out DIR`: replays a verified route. Each step is one press + a still compared to the
  r1 reference still (region diff ≤ 0.7; DBI rows exact via `dbi_menu.py`). **It stops at the first mismatch.**
- `pad_watch.py --t0 <press_wall> --seconds N --every S --label L`: a timeline of stills (no input) + `contact.png`.
- `pad_wake.py wake|poll|step`: HOME-wake attempts; the console poll rate; latency grabs.
- `switch_remote.py --backend usb --host /dev/ttyACM0 shot OUT.png`: one still.
- `homegate.py`: HOME, SSBU-first-tile image gate (refs `tools/host/refs/home-first-tile-ssbu/`).
- `skyline_tcplog.py --label L`: Skyline's TCP log (172.30.254.79:6969) captured to the evidence root, read-only.

**DBI FTP route** (verified twice: r1 by hand, r2 by replay)
- **To FTP:** `pad_route.py /home/user/ssbu-switch-remote-work/ftp-r2/route-to-ftp.json`. It starts from HOME with
  SSBU selected (bright).
  - HOME, Down → Right ×3 to **Album** → A → **hbmenu** v3.6.1.
  - Right ×7 past Goldleaf, NX-Shell, nxdumptool, 90dns setter, Benchmark, Checkpoint and **Daybreak (firmware
    updater: never A)** → **"DBI - English" v912** → A.
  - DBI main menu → Down ×9 → **"Start FTP server..."** → A → submenu → Down ×2 → **"Browse SD card"** → A.
  - Result: `ftp://<ftp-host>:<ftp-port>/`.
  - Reference stills: `ssbu-switch-remote-work/ftp-r1/shots/` (`03`…`50`).
- **Exit:** `pad_route.py …/ftp-r2/route-exit-dbi.json` (8 steps, from DBI's FTP screen).
  - B stops the server → Down ×4 to DBI's **Exit** → A goes straight to HOME (Album selected).
  - Up lands on Mario Wonder → Left → **SSBU selected**.
- **FTP rules:**
  - ftp-guest login (the "Switch SD (FTP)" bookmark: user `ftp-guest`, no password, never printed);
  - **one session at a time**, one fresh connection per operation, `TYPE I`;
  - **NLST returns absolute paths** (handled);
  - for reads, every file is RETR'd twice and the sha256s must match.

## 2. Card state (last verified 2026-11-16 05:12Z, g1 preflight over FTP)

Plugins: `atmosphere/contents/01006A800016E000/romfs/skyline/plugins/`.

| file | sha256 | note |
| --- | --- | --- |
| libarcropolis.nro | ac3e05a38944af6c62a21321b66ca2f0d13468cf5fd0c57697b7fa8ed04dc220 | |
| libcss_preserve.nro | 82862d00139bfeaf67cc922c90b285fbe71378a78fedec7e6eeb6d4a7ef8d4f9 | |
| libelite_rematch.nro | 4f13fd146a0b87a288df7a63823966a49d1f08afa6d69fd6a37dbe0bf62618db | |
| libelite_stages_counterpick.nro | 584a3699185e984324281f4d8518ea3a03830606a135889cea2daf6ba74f0db2 | |
| libgsp_visualizer.nro | 879ef69bb186f07b1c7be0b93fb570a92b81522fcfd9b5ee301984c82b64d348 | |
| libinstant_back.nro | 5f8d090b549533b7af3f8d2e202a525b957582ce0538c457029a47c08233d176 | |
| libmemory_guard.nro | 203ffd6f0b137bb117028ad20ef00dd5e2ce931f840bcdb4af25daefee0ca119 | |
| libnro_hook.nro | ca7aec03aee49fea539dcb92569fea5bc5389f77377ba2a30e4d0b7ebd0255e6 | |
| libparam_hook.nro | 3ed7a2945084176cbb7742d99878f1e8d3a0992feb0fef74be2cfbc68ac8fb88 | |
| libquickplay_cancel_fix.nro | d69f11cf17c618a11b86ee7373c5b786af0d53222dce0a8cb998da6bbe1da9e8 | |
| libsmash_gamepad_overlay.nro | 5ab98bdf12a5f1cb1c7a1bb6694fc9e5c98a8eb758c7b6b5193f72b8d184767a | |
| libsmash_replay_to_mp4.nro | c3873f120e4c760df3bea051652daa20bd1d0cd3a9b319865f7e6ba507f429d0 | |
| libssbu_cpu_pilot.nro | abd305e61d54179f030ec8813bcb95295e7c18a1c4beb79962a4bee4e12085c0 | |
| libssbu_desync_detector.nro | 36027bb7690114a91a1358133e503a58c73ce059139a33b60011b72e6e71aa6c | |
| libssbu_online_deluxe_rewrite.nro | cf16228f0591592fbc47c3e5a360a2b0c6f1c27a5c406b184f09bc6075f2d34c | **Deluxe 0.10.7** (build 0.10.7-426184eaa77d-1794631186) |
| libssbu_quiet_elite_switch.nro | c65d9cc26d4d31c96dfb9022eee11ea4986c2cd6905bd34ba04e25d33a0954fd | |
| libssbu_refresh_clock.nro | 1246f448fcd42092423ff9df51b7eaade806f95ec3da460ba51cf8f2576ff3c0 | |
| libssbu_stall_press_carry.nro | 749ed165c6b941dd6cd99a797bd1b987ec2b3dfd8ab7732afe6b53773d44c656 | |
| **libssbu_switch_remote.nro** | 1b5391a3b17318d01b6ef1fe32f5ec5683566447804727afa031e9ede3806ace | **OURS, r1 (`e2e802a`); added 11-15 22:19Z; first run = the hung launch** |
| **libtraining_rooms.nro** | de563644bc3963f6186a9814b08082f54038b0442c1f9671134acf0b2920c176 | **TR rc.15 (`35268287`); never ran on hardware** (TR events.log ends at rc.6) |
| atmosphere/contents/4200000000005352/exefs.nsp | 7ac69bc449303b9ac7cd4c573525454cc9a107b92f92afd7062255783f3d4837 | our sysmodule r3, parked |
| atmosphere/contents/4200000000005352/toolbox.json | 1b4da43df7f1833491a14a273874c6bf8fbbcae10e0d38264eed8501efabc2a7 | |
| atmosphere/contents/4200000000005352/flags/boot2.flag | — | **REMOVED by f1** (05:03Z, `artifacts/switch-remote-card/install-f1-ftp-retire-sysmodule-boot2/`) |

- **Changes since card-rc13 (11-14 08:11Z), only two:** r1 added; TR `99257e85` → rc.14 `6402e9a7` → rc.15. The other 18
  are identical.
- **Rollbacks available:**
  - TR rc.14 (`6402e9a7…`, verified) at
    `artifacts/rc15-integration/final-35268287/card/rollback-libtraining_rooms-rc14-6402e9a7.nro`. rc.14 also never
    ran on hardware.
  - Deluxe 0.10.7: local copy in `card-jobs/g1-deluxe-0.10.8/before/…`.
- **g1 (Deluxe 0.10.7 → 0.10.8 `30cb25ddea78b2f14ec4da0c4a0306fb314eed7a4cafc3a29d17beca72770c33`, 1,286,144 B):** job
  `ssbu-switch-remote-work/card-jobs/g1-deluxe-0.10.8/` (`sd/`, `authorized.json`, `order.txt`; preflight 05:12Z;
  **no `go.txt`**). Package: `artifacts/deluxe-gamespeed/final-build-prep/packages-e7720fd/30cb25ddea78b2f1/`.
  - **Conditions (primary):** a clean Smoke A (main menu, no crash/error, no new crash report) **and** a fresh
    re-preflight that matches the 05:12Z table EXACTLY.
  - own_install refuses a second preflight in a job dir, so copy `sd/`, `authorized.json` and `order.txt` to a new
    dir (e.g. `g1b-deluxe-0.10.8`) and preflight that.
  - **If bisection disabled any plugin, the table won't match**: post it and get a new OK.
  - Then write `go.txt` (quoting the OK) and `own_install.py install`.

## 3. Smoke A: the hang

- **Sequence:**
  - 05:15:03Z: A on SSBU (`--launch-ssbu`) → "Who is using the software?" → A on the pre-selected **thed0ctor1**.
  - 05:16:13Z: the Nintendo / Nintendo Switch splash at t+1.2 s, **unchanged to t+322 s** (only the auto-dim).
  - Two HOME presses: no effect (**HOME unresponsive**).
- **Stills:** `ssbu-switch-remote-work/smoke-ab/` (`watch/smokeA-boot*/`, `shots/`).
- **Console at the end:** it pings; TCP 47130 is refused; DBI is not running.
- **R1 audit (INBOX 05:31Z):** no definite hang.
  - Skyline (2023 `972cdefc` / 2026 `2eb226fa`) initializes sockets before any plugin `main`.
  - Mains run on Skyline's task thread (priority 20, core 3) while the game's main thread waits.
  - r1's main is bounded; its thread (priority 20, below the game main's 16) sleeps 1 s per failed connect.
- **grok-unstick-2's address validation** (HANG-SUSPECTS-TR.md §Address validation, relayed by the primary ~05:45Z):
  every TR load-time address is mapped and every guard byte matches, so **the hang is not a bad offset**.
- **Leading candidate:** the module heap/reservation. TR rc.15 needs **+307 KB file+bss over rc.13**, plus our r1 NRO
  is new.
- **Alternative:** something before TR's install line.
- **Skyline's TCP log** (each LoadModule result with its ro error, each "Running/Finished running `main`") decides
  between them. It's now captured at every launch (§4a step 0).

## 4. Morning sequence (after the operator's power cycle; console on HOME)

0. **Model check + watcher:** model `claude-opus-5-5` / effort `xhigh`. Watcher:
   `/usr/bin/python3 -I /tmp/claude-1000/sr-model-watch-81d86844.py <transcript> <claude pid> xhigh`
   (recreate it if `/tmp` was cleared). Then `pad_nav.py still m0`; press B if dimmed. The image gate must pass.
1. **Read-only FTP diagnosis** (into new dirs under `artifacts/switch-remote-card/ftp-fetch-r3/`):
   - Enter DBI with `pad_route.py …/ftp-r2/route-to-ftp.json --out /home/user/ssbu-switch-remote-work/ftp-r3`.
   - Then `tools/card/own_install.py fetch <dir> --transport ftp --root ftp://<ftp-host>:<ftp-port>` with:
     - `--path ultimate/switch-remote/events.log` (**did r1's main finish during the hang?** It didn't exist before);
     - `--path ultimate/TrainingRooms/events.log` (a new `plugin=training_rooms/0.2.0-rc.15` line?) and
       `ultimate/TrainingRooms/*.log`;
     - `--list ultimate/ssbu_online_deluxe_rewrite` (+ its logs);
     - `--list atmosphere/crash_reports` (706 entries at 05:0x; diff it), `--list atmosphere/fatal_reports`,
       `--list atmosphere/erpt_reports`;
     - `--list atmosphere/contents/01006A800016E000/exefs` + `--path …/exefs/subsdk9` (**Skyline build**);
     - `--list ultimate/arcropolis` (logs/config);
     - an SD-root listing for any skyline log;
     - **the plugin heap size, if a config describes it:** `--list atmosphere/contents/01006A800016E000/romfs/skyline`
       (anything besides `plugins/`), `--path ultimate/skyline_exefs` (2 entries in the 05:0x listing), and any
       `nro_hook` config. Search the stage-1 listings for `nro_hook`; `libnro_hook.nro` is plugin `ca7aec03…`.
     - The **`exefs/subsdk9` sha256** above identifies the Skyline build and its logger: 2023 `972cdefc` = TcpLogger;
       2026 `2eb226fa` = DualLogger (TCP + kernel log). Neither writes an SD log; TCP 6969 is the log.
   - **Interpretation** (Skyline runs mains in directory order; ours runs before TR's):
     - r1's line present + no new TR line → TR's main stuck.
     - Neither → the hang was at or before r1.
2. **b1:** `card-jobs/b1-disable-switch-remote-nro` (`moves.txt`: our NRO → `ultimate/switch-remote/disabled/`).
   - Write `order.txt` (quote the primary's bisection authorization, 2026-11-16 ~05:20Z), then
     `own_install.py preflight` + `install --transport ftp --root ftp://<ftp-host>:<ftp-port>`.
   - Check: `ok=true`, moved readback True, other files unchanged.
   - Exit DBI (`route-exit-dbi.json`) → **one launch** (procedure §4a).
3. **b2 (only if b1 still hangs):** `card-jobs/b2-tr-out` (TR rc.15 → `disabled/`, **no replacement**) → one launch.
   - If that boots, TR is the culprit.
   - Optional afterwards: `card-jobs/b3-optional-tr-rc14-in` to split rc.14 from rc.15. **Edit it first** (its move
     line is already done by b2) and add `order.txt` + `go.txt` at run time.
4. **Then** any other plugin added since the last good boot (none known besides these two), one at a time.
5. **Stop at the first configuration that reaches the main menu.** Leave the card **bootable**: the culprit stays
   disabled, everything else as is, and Deluxe stays 0.10.7. **If TR is the culprit, leave it disabled; the operator
   decides.**
6. **g1** only after a clean smoke + an exact re-preflight (§2). Then **Smoke B** (the same procedure). Then
   read-only FTP (events.log + crash_reports). Then exit to HOME with SSBU selected.
7. **Post one summary:** attempts and stills; the findings; each bisection table; the culprit; the final card table
   (every plugin: sha, enabled/disabled); the exact rollback steps.

**4a. Launch procedure (one attempt)**
0. **Start the Skyline TCP log capture just BEFORE pressing A**, in the background (a harness background task, or
   `systemd-run --user --unit sr-skylog-<label> /usr/bin/python3 -I …`):
   `/usr/bin/python3 -I tools/host/skyline_tcplog.py --label <launch-label> --seconds 600`
   - It connects to `172.30.254.79:6969`, retries every 0.5 s, and reconnects after drops. It appends raw bytes with
     UTC timestamp headers to `artifacts/switch-remote-card/skyline-tcp-<launch-label>.log`.
   - Read-only. Stop it (SIGTERM) after the attempt, or let `--seconds` run out.
   - Use one label per launch, e.g. `b1-try1`, `b2-try1`, `smokeB-try1`.
   - In the post, quote each `[PluginManager] Loaded/Failed to load …` and `Running/Finished running main …` line,
     and the last line before the stop.
1. `pad_nav.py still pre` (gate ok).
2. `pad_nav.py press a launch --launch-ssbu --confirm-a "<…>" --settle-s 1`.
3. `pad_watch.py --t0 <press_wall>` until the user selector shows ("Who is using the software?") → `pad_nav.py press
   a user --confirm-a "pre-selected first profile thed0ctor1"`.
4. `pad_watch.py --t0 <press_wall> --seconds 120 --every 3` through boot, the title and the main menu. One A at the
   title screen is allowed, to reach the main menu (verify the title screen first). Go no further.
5. **Close:** HOME → still (HOME with SSBU suspended) → X → still (the close dialog) → `press a … --close-dialog`
   ("Close") → still.

**Hang rule** (operator): a hang = ~90 s with no progress past the splash. Retry ≤ 3 attempts per configuration
(HOME → X → A "Close" → relaunch). **If HOME is unresponsive → post and stop** (power cycle needed). Diagnose
read-only before any change. If anything crashes: still → HOME → FTP read-only crash report → post → stop.

## 5. Rules

- **Card writes:**
  - **move-only: never delete** a file (only moves into `ultimate/switch-remote/disabled/` and verified copies;
    own_install `moves.txt` allows exactly that and the reverse);
  - **f1 discipline:** a preflight table, verified local and on-card copies, readback, an after-table with every
    other file unchanged, receipts copied to the evidence root;
  - another project's file only with `authorized.json` + `go.txt`, after the primary's OK.
- **No pushes:** commits stay local; no tags.
- **The SSBU launch is allowed** (WAN blocked). No A on the SSBU tile except a `--launch-ssbu` launch. No System
  Settings changes.
- **Shell (verbatim, binding):** Every agent and spawned subagent on native Ubuntu must run local commands through
  `/usr/bin/pwsh -NoLogo -NoProfile -NonInteractive -Command ...`. Use the explicit executable path, including when
  the harness defaults to another shell. Do not execute local Bash command bodies unless explicitly authorized by the
  operator. The migration's Linux installer helpers are already authorized to use their native shell. For remote
  Windows hosts use the MSI PowerShell 7 executable `C:\Program Files\PowerShell\7\pwsh.exe` with the same flags;
  never WindowsApps, cmd.exe or Windows PowerShell 5.1. Remote Linux helpers may use their native shell. Repeat the
  applicable host's shell rules verbatim in every delegated prompt.
- **Builds:** never on the Surface for compiles; the Yoga/XPS go through the ledger (`tools/build_pad.ps1`
  `-LedgerHost yoga|xps`). The rc15 feature work has priority.
- **INBOX post format:** append a heading `## HH:MMZ (11-16, Get-Date) switch-remote → the primary: **headline**`
  (time from `Get-Date -AsUTC`). Start the body with one plain sentence. Use
  `[IO.File]::AppendAllText(<INBOX>, <post>)`.
- **Repo identity:** crashbandicode <crashbandicode@users.noreply.github.com>. Commit with
  `git -c user.name=intpa -c crashbandicode@users.noreply.github.com commit -F <tempfile>`.

## 6. Open defects and loose ends

- **r1 NRO, `e2e802a:src/runtime.rs:268-271`:** after a send failure the reporter loop reconnects **without
  sleeping** (a tight loop at priority 20 if a peer accepts then drops). Not the hang on this console (nothing
  listens; a failed connect sleeps 1 s).
  - Fix: sleep on that path; a fixed lower priority and explicit core via `CreateThread1`.
  - HEAD's NRO 0.2.0 (`3d6e1bb`, `scene_host`) has the same loop.
- **Sysmodule:** r3 (`7ac69bc4`) is on the card, no longer autostarting. r5 (`1cda5f7`) is parked, never installed.
- **Pad firmware:** a CLI `hold` with the usb backend releases after 1 s (watchdog) unless the process keeps pinging.
- **Older staged jobs (superseded; don't run):** d1/d2/e1/e2/r2–r5/fallback/cleanup in `card-jobs/`.
- **Local commits (53, none pushed); the most recent:**
  - `16dd9cc` R1 audit
  - `f1ac086` plugin moves + launch/close guards + pad_watch
  - `4569020` pad_route
  - `8a4b24c` authorized.json + go gate
  - `714ea70` FTP-PLAN-r1 executed (NLST fix)
  - `3968a7f` FTP plan + fetch
  - `cd750fe` first pad control
  - `b59a260` pad_wake
  - `1c05961` / `ef58bb6` / `433f357` build_pad fixes
  - `82abd45` FTP transport
  - `f9208f2` switch-pad 0.1.2
  - `6dac6c2` 0.1.1
  - `84f3acc` Pi option
  - `d51e232` USB route
  - `c5b6ee0` firmware
  - `9be991c` DESIGN rev 5
  - `ead5af1` option (d)
  - `3d6e1bb` NRO 0.2.0
  - `cb7ed55` / `1cda5f7` / earlier: see `git log`.
