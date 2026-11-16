# HANDOFF: switch-remote (restart-safe)

Written 2026-11-16 ~05:40Z for a fresh session; updated ~05:50Z (Skyline TCP log capture + the heap candidate).
- **Worker:** switch-remote (Claude Opus 5.5, xhigh), spawned by the rc6-integration primary.
- **History:** the older, longer log is `docs/HANDOFF-history-2026-11-16.md`.
- **Repo:** `/home/user/ssbu-switch-remote`, branch `main`. No remote; **53 local commits, none pushed**.
- **Work dir:** `/home/user/ssbu-switch-remote-work`.
- **Evidence root:** `/home/user/smash-training-lobby/artifacts/switch-remote-card/`.
- **INBOX (where every post goes):** `/home/user/smash-training-lobby/artifacts/phase23/INBOX-opus-rc6-integration.md`.

## 00. switch-remote-2 progress (2026-11-16, newest first; supersedes §0/§3/§4 where they differ)

- **~20:20Z HARDWARE TESTING STOPPED (the operator).** Console idle in Training (SSBU running); pad reset + stale claim
  released via `switch_hw.py down artifacts/switch-hw-feature-ledger/run-1`; OBS idle.
  - **STANDBY: the reship card install** when the primary forwards the NRO path + sha256 + go (from deluxe-gamespeed-sol,
    `rc15/hw-reship`): close SSBU → DBI FTP route → an own_install job like `card-jobs/l1-tr-legend` (authorized.json +
    order/go; preflight table; rollback on card + local; readback; re-hash the others; INSTALL.md) → route out →
    relaunch. NO other console input until then.
  - **On the card now:** TR legend ffd2f74a (80bb1203), Deluxe 0.10.8, tile-cursor aid 66f00442 (added 19:2xZ,
    `artifacts/switch-remote-card/install-t1-tile-cursor/`), + 17 others (20 NROs).
  - The handover doc for any HW driver: `artifacts/switch-hw-runbook/HANDOVER-TO-OPUS.md`.
- **~18:40Z REFOCUS (the operator: the HW run was "just clicking around").** The old strict plan is STOPPED at a row
  boundary (the session lane keeps the claim/capture; `switch-hw-session-2/out` keeps its receipts). A NEW plan of in-game
  FEATURE EFFECT rows (before → the input → the visible gameplay result), one per FEATURE-LEDGER feature + the rc.15
  features, ported from the Eden recipes. Menu-only rows → `$RB/features/EDEN-COVERED.md` (one menu smoke row kept).
  - The feature-plan lead (Opus, with Sonnet section authors) writes `$RB/rows-v3/`, `$RB/features/plan-<n>.json` (dry-run
    READY) + FEATURE-MAP.md, and messages the session lane per section; the lane runs each with the same `--out`.
  - The exact KNOWN-ISSUE signatures are (a)-(f): (a) P1+CPU in one visit, (b) the Player Swapping chord gate, (c) the BTT
    zoom, (d) the BTT banner, (e) projectiles vs targets, (f) no CPU-cycle busy notice. Anything else is FAIL/SETUP-SUSPECT.
  - Overlay relabels: FX-01-for-FS-01, FS-39, FS-54 → SETUP-SUSPECT; FS-57 → FAIL (possible product finding: the
    count stays 4 after a menu open/close); FS-04 → (f).
  - Clean-state fixes: BTT/Eggs Off read-back; Combo helper is a 2-value toggle (one press + read, cap 2); SC-01 waits
    per press; S-BOOT gets an expected line.
- **17:34Z CORE READY (INBOX 40979); WAITING FOR THE PRIMARY'S "SWITCH RELEASED" (the operator retrying P1 cycling by hand).**
  Launch = a fresh session-lane worker running, via pwsh:
  1. `/usr/bin/python3 -I /tmp/pb-clip/tools/eden-runner/switch_hw.py up /home/user/smash-training-lobby/artifacts/switch-hw-session-2` (refuses without a live Skyline line in 30 s);
  2. `/usr/bin/python3 -I /tmp/pb-clip/tools/eden-runner/runner.py run /home/user/smash-training-lobby/artifacts/switch-hw-runbook/relaunch/rows.json --instances …/switch-hw-session-2/instances.json --out …/switch-hw-session-2/out` (background);
  3. always `switch_hw.py down`.
  Post the start + first 15 + `P1-SWAP-EVIDENCE.md` clip paths + progress ~30 min + FAILING.md. Facts: Reset page = SAVED defaults (never use it for clean-state); Player Swapping defaults Off (rows set it On). Possible product finding: SC-01 4/6 cycles changed the CPU.
- **~17:30Z STRICT-CHECKS RELAUNCH PREP (SWITCH RELEASED by the primary; the console is on the Training CSS).**
  - **Why:** the BTT leak (FS-65/68 left it ON) contaminated PF-05..REC-21; only 12/212 rows had checked effect gates.
  - **New rules:** the clean-state gate + recovery, mode-off endings, the async gateway **Grok 4.6 high judge**
    (`$RB/judge/judge.py --model grok-gw`; calibrated 18/19; GLM dropped), final = gates + judge (UNSURE →
    RAN-UNVERIFIED), stop only after **3 consecutive failures**, **KNOWN-PRODUCT-ISSUE** rows (P1 swap; BTT zoom/banner;
    projectiles vs targets) don't count, the P1-swap rows run EARLY (evidence for the fix lane).
  - **Section rows done:** `$RB/rows-v2/` (Expected lines + labelled stills; 5 log gates).
  - **The Opus core is finishing:** `$RB/relaunch/rows.json`, run dir **artifacts/switch-hw-session-2/**, a fresh Skyline
    capture (it stopped at 15:25:58Z in session-1). When it reports CORE READY: launch via the /tmp/pb-clip runner, then post
    the start + first 15 + the P1-swap clip paths.
  - The 3 section authors and the judge worker are done.
- **~16:10Z SESSION PAUSED for the NVENC encoder change (INBOX 40490).** OBS stopped (finalized), the claim released, SSBU still in
  Training. Last completed row **SC-02**; next **PF-09** (interrupted mid-row, reruns). **RESUME on the primary's "encoder
  changed":**
  1. `/usr/bin/python3 -I /tmp/pb-clip/tools/eden-runner/switch_hw.py up /home/user/smash-training-lobby/artifacts/switch-hw-session-1 --owner switch-hw-session`;
  2. make sure OBS is recording (obsws status; if inactive, the runner's per-row guard or a start + wait outputActive);
  3. `/usr/bin/python3 -I /tmp/pb-clip/tools/eden-runner/runner.py run $RB/rows.json --instances <dir>/instances.json --out <dir>/out` (same --out; passed rows skip; `"per_row_files": true` in rows.json);
  4. `switch_hw.py down` always.
  - The runner = scratch clone `/tmp/pb-clip` (per-row-clips, ebf4d8e on d77d0b2; the patch in `switch-hw-session-1/playbook-patch/`). The per-row recording-active guard was requested from the clip worker (interrupted; check the clone's git log).
  - **Mop-up:** PF-06, PF-07 (the Recording TOP seek lands on Combos & Sequences: a systematic bug), FS-57/58/63/66-67/70 + later PENDING-SETUP. The session lane's notes are `switch-hw-session-1/RUN-NOTES.md`.
- **~15:50Z SESSION RESUME #2 GO (one continuous recording; offline cuts by rec_ms timestamps; per-row OBS split work
  STOPPED).** The session lane holds `artifacts/switch-hw-session-1/` (same --out, from PF-06; fixed `$RB/hooks/tr_page.py`
  keyboard-safe home + preload_clear d77d0b2). Then newest → oldest, then the mop-up (P1 Mario in its own menu visit).
  - **Candidate TR product issue:** P1 pick "Character choice expired" when the CPU is picked in the same menu visit
    (MENU_EPOCH bump), INBOX 40334.
  - **OBS:** the clip worker enabled manual file splitting (`split_file`) ~15:40Z; harmless; revert per
    `$RB/OBS-SETTINGS-CHANGE.md` when convenient.
  - **Stopped/abandoned workers:** the clip worker (stale claim released via `down`), the carry-over worker (rows-v2
    partial; not used).
- **~14:55Z LEGEND INSTALLED (INBOX 39952):** TR `de563644` → `80bb1203` (ffd2f74a release), ok/readback True, 20 others
  unchanged; rollback on card `ultimate/switch-remote/backups/pre-80bb12036ce0e0f0.libtraining_rooms.nro` + local
  `card-jobs/l1-tr-legend/before/…`; receipts `artifacts/switch-remote-card/install-l1-tr-legend/`. Console HOME/SSBU.
  **Card now = TR legend ffd2f74a + Deluxe 0.10.8 + 17 others (19).** Gap worker filling LEG-01..05 (+ SC-02 drill grading
  added; PF-01 footer = review shot; dry-run READY ~1.52 h). NEXT: when LEG rows are in → launch the ONE session (a session
  worker), post the start + first 15, showcase PASS/FAIL lines, progress ~30 min, results; cut with
  `tools/host/cut_session.py <out>`.
- **~14:20Z READY TO LAUNCH, gated on the legend.** LIVE RECORDING PROOF PASS (INBOX 39830; client 91512f5, playbook 16cc318,
  cutter ebde113 `tools/host/cut_session.py`). The legend install is fully staged: `card-jobs/l1-tr-legend/STEPS.md` (exact
  commands A-D + rollback + the INSTALL.md template). Watchers in this session: the INBOX `^## .*LEGEND RELEASE READY` (not
  ours), and a 30-min timer. **If the legend isn't ready by ~14:50Z, tell the primary** (it decides rc.15-first). After the
  install: launch the ONE runner session (`switch_hw.py up` → `runner.py run $RB/rows.json …` → `down`; then
  `cut_session.py <out>`), showcase rows first. Pending: the compression worker's newest-first reorder + LEG rows +
  dry-run. (The obsolete playbook-patch and its test step are removed; the runner's native factory is tested.)
- **~14:00Z SESSION ORDER (the operator): newest first.** First after boot: (a) quicker CPU cycling (Character Cycles,
  several cycles), (b) Eggs-ercise, (c) Break the Targets, (d) input drills (record/make/crop/grading appearing), then
  the LEG-* legend checks, then everything else newest → oldest by introducing release. During the session: post an INBOX
  line with PASS/FAIL as each of (a)-(d) finishes (`showcase:` row-end marks); cut their GIFs first (mid-recording if
  OBS's file is readable, else first after the session). Post the first 15 rows with the session start. The
  compression worker is re-ordering SESSION.md/rows.json.
- **~13:55Z CURRENT PLAN (the primary): ONE full session on TR rc.15 + LEGEND change; NO more mini-runs.**
  1. Wait for INBOX "LEGEND RELEASE READY" (rc15-crop-editor-sol: NRO + sha + size + check list). A background watcher is
     armed in this session.
  2. Install it over DBI FTP: job `ssbu-switch-remote-work/card-jobs/l1-tr-legend/` (order.txt staged; add the NRO under
     `sd/…/plugins/libtraining_rooms.nro`, `authorized.json` like g1b's, `go.txt`). Route in: `pad_route.py
     …/ftp-r2/route-to-ftp.json`; `own_install.py preflight` then `install --transport ftp --root
     ftp://<ftp-host>:<ftp-port>` (STOR .sr-part → RETR sha → rename → readback; on-card + local rc.15 rollback); route
     out `route-exit-dbi.json`. Post the install table (19 plugins) BEFORE the session; INSTALL.md + receipts go to
     `artifacts/switch-remote-card/install-l1-tr-legend/`.
  3. The runbook (compression worker): REMOVED-BY-LEGEND colour rows, LEG-01… rows from the check list, dry-run READY.
     Estimate (pre-legend): 219 executed rows, ~1.44 h (INBOX 39492).
  4. Before launch: harness-lib fixes (no per-row recording on hardware; the gif step → marks + an offline cut from the
     a80d514 session recording; it was asked who owns the cutter) + the client start guard (wait while OBS is
     finalizing) → a 1-min live check (start/mark/stop/cut one GIF) → the ONE runner invocation (`switch_hw.py up` →
     `runner.py run $RB/rows.json --instances … --out …` → `down`). Post the start time + row count, progress every
     ~30 min, and the results.
  - **Done since 13:11Z:** hw-run-2 / 2b (F05′ PASS; F07 PASS in 2b; F04 obs_500, F08 GifRefused = harness);
    Screen Burn-In Reduction **On → Off** (operator-approved; `artifacts/switch-settings-autosleep/SETTINGS-CHANGE.md`,
    revert = back to On); obsws file-finalize fix `40cb949`. The old mini-run workers are told to release and END.
- **13:11Z HW RUNBOOK READY posted (INBOX line 39177):** 467 catalog / 254 executed / 100 covered / 19 pilot / 33 PERF / 26
  ONLINE / 15 2P / 20 NOT-TESTABLE. ONE session ~5.4 h. `$RB/rows.json` dry-run READY (1,901 segments). Selector tests 16/16;
  commit `7a473a3`. Follow-up: 195 unmapped TR src files select everything, so they need mapping.
  - **hw-run-2** (a worker holds the console; run dir `artifacts/switch-hw-run-2/`): unattended F04/F05/F07/F08 via the
    runner. S-BOOT passed in 143 s. When it reports, post its verdicts and update `switch-hw-ledger-rc15/LEDGER.md`. Then the full
    session: `switch_hw.py up <dir>` → `runner.py run $RB/rows.json --instances <dir>/instances.json --out <dir>/out` →
    `switch_hw.py down <dir>` (always). harness-lib = session ssbu-mod-playbook-6e (backend at playbook f60448d).
- **12:48Z PILOT TRIAGE + PILOT FIXES READY posted (INBOX line 39022).** The console was handed to harness-lib (ssbu-mod-playbook-6e) ~12:47Z for its proof; switch-remote-2 holds no console.
  - Fixes doc: `artifacts/switch-hw-ledger-rc15/PLAN-FIX-F04-F07-F08.md` (Eden-exact setups: F04 P1 Olimar via Player Character + reset; F07/F08 Eden `drill-move` taps → 4-step drill; no hit rule).
  - Launch-1 = erpt **2009-0005** (ldr InvalidNso) at 12:16:15Z; it's not timing. Proposed (needs an OK): a read-only SD read check when the card is next on UMS. 21 other new erpts are WAN noise. Decoder: `/tmp/sr2-erpt.py`.
  - harness-lib was told: add an "Unable to start software" image gate → close → one relaunch in switch_boot (refs `switch-hw-ledger-rc15/preamble/P0-profile-after-load.png`).
  - **Operator rule:** PRODUCT FAIL only with the Eden row's exact setup. The integration worker was told to copy Eden setups.
- **(superseded) PILOT TRIAGE notes:**
  - **F04** = PLAN: Eden's CPU-Olimar row did a Training reset before looking (`final-35268287/runtime-remaining1/rows/article_hurt.py:140-148`, `reset-center`, then 6 native Pikmin); the pilot swapped the CPU without a reset. Fix: `press l+r+a 300` after the swap. Confirm on hardware in the next run.
  - **F07** = PLAN: Make combo makes an input drill only when the macro has inputs and lands no hit (TR `src/model/rr_host.rs:1515-1520`); otherwise EMPTY_COMBO "No combo yet". Macro 1 had swallowed inputs.
  - **F08** = PLAN: record the drill facing away from the CPU.
  - **Launch 1 "Unable to start software"** (12:14:50Z A; error by 12:16:03Z): 15 min after the last Close (11:59:54Z), 5 s after a B undim; launch 2 8 s after a Close booted fine, so "too soon after Close" isn't supported. Still to do: once the pilot is DONE, exit to HOME → DBI FTP read of the newest `atmosphere/erpt_reports` (583 at 11:5xZ) → error code → post `PILOT TRIAGE` → hand the console to harness-lib (session ssbu-mod-playbook-6e) for its 10-min proof.
  - Integration delegated to an Opus worker (dedupe, SESSION.md, rows.json + recipe_macro + dry-run, selector history tests, RUNBOOK.md). All 6 lanes done: CPU 97/57 exec, DC 93/58, FS 86/48, REC 79/53, SAV 51/37, X 51/16.
- **12:3x-12:45Z operator corrections:** ONE session (not 45-60 min splits); NO redundant tests: every catalog row has
  `- **Covered by:** self | <ID> | pilot F0x`, and only `self` HW rows execute; the rows must run in the playbook
  RUNNER format (`ssbu-mod-playbook/tools/eden-runner/runner.py` rows.json) as one unattended invocation.
  harness-lib-opus builds the hardware backend. Coordination post: INBOX line 38806, plus a message to session
  ssbu-mod-playbook-6e. The firmware macro contract already exists (MBEGIN/MEV/MPLAY; PING ≤0.5 s during play; HOME via
  BTN 18). switch-remote-2 owns the firmware: flash only between pilot rows (coordinate with switch-hw-ledger-sonnet).
  - `tools/runbook_rows_json.py` generates `$RUNBOOK/rows.json` from executed rows (+ pilot F0x from the ledger PLAN),
    in SESSION.md order. Tested on pilot F01/F06.
  - Still to do: the lanes' rows → cross-page dedupe → `SESSION.md` → `rows.json` → historical selector tests →
    `RUNBOOK.md` → post `HW RUNBOOK READY` with the counts catalog / executed / covered-by-rep / pilot-covered / PERF /
    ONLINE / 2P + the session length.
- **~12:25Z NEW JOB: FULL HW RUNBOOK** (operator: every TR feature, incl. clean-room ports; then only re-test changed
  code paths). Dir `/home/user/smash-training-lobby/artifacts/switch-hw-runbook/`: `BRIEF-extract.md` (row format),
  `rows/{cpu,display-and-controls,fighters-and-stages,recording,saves-and-speed,cross-cutting}.md` + `coverage-eden-rows.md`
  (6 Sonnet extraction lanes, launched ~12:20Z), `select-all.txt`, `ignore.txt`.
  - Selector `tools/runbook_select.py <range>` + `tools/tests/test_runbook_select.py` (8 rule tests pass; 3 historical
    activate when rows exist).
  - Remaining: when the lanes report, validate the rows (globs exist, parse), assign `- **Session:** S01…` (45-60 min each,
    by page/fixture), write `RUNBOOK.md`, run the historical tests, post `HW RUNBOOK READY` with the counts
    HW / PERF-EXCLUDED / NEEDS-ONLINE / NEEDS-2P + sessions. **Don't touch the console** (the pilot lane drives it).
- **12:13Z PLAN READY posted (INBOX line 38729).** Per the operator, a Sonnet lane executes `PLAN.md` (12 in scope, 8 N/A;
  tools section included); switch-remote-2 only answers stuck points. Console idle on HOME, SSBU selected.
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
