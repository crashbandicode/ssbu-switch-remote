# HANDOFF: switch-remote

Updated 2026-11-16 ~04:20Z (USB pad route built; END: waiting for the operator to plug the board in). Worker: switch-remote (Opus 5.5, high), session `81d86844-b65c-42b8-bf83-c7b6d7ba2c32`,
spawned by the rc6-integration primary. Playbook followed: `ssbu-mod-playbook` `3332c0d`.

## Objective and scope

The goal is to remote-control the physical Switch (SSBU 13.0.5) as agents drive Eden. Under option (d), adopted
2026-11-16, the parts are:
- sys-autopilot 1.5.0 (upstream) for input and files;
- our in-game NRO as the scene reporter (`scene_host` → this host);
- the host:
  - the scene receiver and fail-closed gate (`scened.py`);
  - the image-gated LAUNCH/TERMINATE (`homegate.py`);
  - the `autopilot://` driver;
  - capture through OBS (primary) or capd.

Our sysmodule (r5) is parked as the fallback. `DESIGN.md` (revision 5) is the design.

**Since ~03:50Z the PRIMARY INPUT ROUTE is the USB wired pad (§13).** It is the operator's Freenove ESP32-S3 board:
the OTG port goes to the dock, the UART port to the Surface. sys-autopilot is OPTIONAL (logs and files).

## Rulings received (2026-11-15)

- Sysmodule in v1; capture-card screenshots and GIFs; caps:sc and NVN readback dropped.
- A: LAUNCH/DISMISS run without a scene report only when no application exists, and LAUNCH only after the
  sysmodule confirms SSBU is the first tile; all other input fails closed.
- B: ns2bench was evaluated: GPL-3.0, Switch 2, needs external hardware. We keep our own and copy nothing.
  OBS is the primary capture backend when it runs.
- Later (2026-11-15/16): LAN only, and the gate allows local wireless while refusing NSO (§2, §6a). ovlSysmodules
  compatibility with no-reboot updates (§3a). Smoke 01 parked (operator away). **r5 + sys-autopilot fallback
  (primary ruling 2026-11-16 ~03:0xZ):**
  - Build r5 and host-test it, but don't install it.
  - Keep sys-autopilot as a hand-started fallback, staged without boot2, with a thin `autopilot://` backend.
    Only one may hold the virtual controller at a time.
  - Resume plan: r3 logs, then install r5 and the fallback, reboot, smoke r5, and switch to the fallback if r5
    doesn't listen within 2 min.
  - Post the CARD INSTALL REQUEST table first. No card actions now; the console is parked.
- **ADOPT option (d) (primary ruling 2026-11-16 ~03:1xZ; supersedes the r5 + fallback message, which crossed):**
  - sys-autopilot 1.5.0 drives; r5 is PARKED as the fallback.
  - LAUNCH re-ruling APPROVED: host-side HOME→A only with no fresh scene report AND a still with SSBU as the HOME
    first tile (reviewed image gate). TERMINATE is HOME→X→A with a still check.
  - Residual gate risk ACCEPTED: the sys-autopilot token bypasses the host gate; the token stays host-only and the
    console is internet-blocked.
  - First install WITHOUT sys-autopilot's boot2.flag: start it by hand in ovlSysmodules, and add the flag later in
    a receipted session once it has served `/status`.
  - The card session: read r3's logs; ADD sys-autopilot (no flag); REMOVE our `boot2.flag`; UPDATE the NRO and
    config (`scene_host`); reboot; the operator starts it once.
  - Post the final exact table when the host work is done, then END.

## State

- Branch `main`, no remote, nothing pushed or tagged; HEAD is the latest commit (`git log -1`).
- NRO 0.2.0 (`scene_host`): gates green on `3d6e1bb` (Yoga, `remote-gate.ps1`, 2026-11-16 03:3xZ):
  - test 22/0, clippy 0, ndf 0;
  - release 36,864 B, sha256 `d4581396f2133e630ee2b4c34bf8722020fcb357d4024efb756f813fe35e24ba`;
  - diag `c9d7ad10…`;
  - artifacts in `/home/user/ssbu-switch-remote-work/gates/3d6e1bb/`; the lane was cleaned.
  - On the card now: r1's NRO `1b5391a3…` (0.1.0, loopback only).
- Sysmodule: built on the Yoga at `39ebc14` (`tools/build_sysmodule.ps1`). exefs.nsp is 142,484 B, sha256
  `1ab635cf9398d2a19bdd3b90cf808d3ffa03332299eea6d886c7ad25bf73c93b`; the image is 1.38 MiB. Artifacts:
  `/home/user/ssbu-switch-remote-work/sysmodule/39ebc14/`. A later commit that only moved host files needs no
  rebuild (`sysmodule/` unchanged except the Makefile comment).
- Host: 95 tests OK (`/usr/bin/python3 -I -m unittest discover -s tools/host/tests`, after
  `make -C sysmodule out/loopback`); card tool 16 OK (`-s tools/card/tests`). Core: 240 checks (2026-11-16 ~03:30Z).
- Capture card: `/dev/video4` works, but there is no signal yet: the console is in hekate UMS, mounted
  read-only for us at `/run/media/intpa/SWITCH SD`. OBS 32.2.0 (snap) is installed but not running.
- Ledger: no open claims of ours. The first sysmodule build left `bid=cb1efd13` open; it was released by hand.
  Lanes cleaned: `switch-remote-sysmodule` (by the script) and `switch-remote` on the Yoga and the XPS
  (`remote-gate.ps1 -Action Clean`, 22:2xZ).
- Install bundle: `/home/user/ssbu-switch-remote-work/install-r1/` (`sd/` tree + `MANIFEST.json`). The
  token lives only in `~/.config/switch-remote/token` (0600) and the bundle's `config.txt` (0600).
- First install DONE by the primary (2026-11-15 22:19:40Z, add-only, all 5 readbacks OK):
  `/home/user/smash-training-lobby/artifacts/switch-remote-card/install-r1/INSTALL-RECEIPT.json`. Card free
  space is 24 GB (my "about 90 GB" read the wrong drive). Later reinstalls of our own files only:
  `tools/card/own_install.py preflight|install <job> --transport ums|mtp --root ...` (receipts, rollback copy,
  readback, before/after table; order in `order.txt`).
- LAN only (operator, 2026-11-15 ~23:10Z): the console will be blocked from the internet. No hardening beyond the token, private peers and the gate; online-path testing goes over a LOCAL-WIRELESS lobby (Switch + one Eden LDN peer; operator correction ~23:14Z): `smoke.py lan-check` expects a 0x0201xxxx scene with gate allowed; NSO refusal is code+test only. DESIGN.md section 6a.
- Card r2 (2026-11-15 23:17Z, own_install, UMS): sysmodule exefs.nsp 1ab635cf… → f3fe636a… (c537d5f, local wireless allowed); readback OK, rollback copy ultimate/switch-remote/backups/pre-f3fe636a1cd7bd0f.exefs.nsp, 22 other files unchanged. Receipts: /home/user/smash-training-lobby/artifacts/switch-remote-card/install-r2/. NRO unchanged (1b5391a3…).
- Card r3 (2026-11-16 01:19Z, own_install, UMS): sysmodule 45062a9 exefs.nsp f3fe636a… → 7ac69bc4… (151,649 B, image 1.46 MB incl. 1 MiB heap), toolbox.json → requires_reboot false (1b4da43d…); readbacks OK, rollback copies in ultimate/switch-remote/backups/, 21 other files unchanged. Receipts: artifacts/switch-remote-card/install-r3/.
- Console discovery poller: stopped (time limit) at 03:1xZ, never found a listener (last round 03:10:59Z), log
  `/home/user/ssbu-switch-remote-work/discover.log`. Not restarted: `resume_smoke.ps1` runs its own discovery.
- Eden proof (non-blocking, primary ruling): queued as Yoga user unit `sr-nro-01-queue` (plan `eden/sr-nro-01/`,
  staging `/home/user/eden-runs-staging/sr-nro-01/`, log `queued.log` there, run `/home/user/eden-runs/sr-nro-01/`,
  summary `sr-summary.json`). Stop it with `systemctl --user stop sr-nro-01-queue` on the Yoga (before Up: nothing to undo).
- Hardware smoke runner: `tools/host/smoke.py --host IP --out DIR preflight|launch|play SEG..|step BTN|online-check|terminate|bye`.
- Eden proof ran 2026-11-15 23:00Z (primary GO), two short Yoga boots: install line, channel, title and main gate `allowed` PASS; online rows blocked by the profile (no LAN online, error 2306-0520). `eden/sr-nro-01/RESULTS.md`. The Yoga has a perf-sensitive calibration (dgs-cal-b2) and the XPS a
  timed-row Eden; the Surface is excluded. A ruling was requested (Eden slot first, or card install now).
- Watcher: `/tmp/claude-1000/sr-model-watch-81d86844.py` (expects claude-opus-5-5/high), re-armed on each
  resume (owner = the claude pid).

## Smoke 01 (2026-11-16 01:30Z): BLOCKED, parked

The sysmodule was not listening (console at HOME, IP 172.30.254.53). Static review: `docs/SMOKE-01-REVIEW.md`.
Probable root cause: the listener was bound before DHCP. r4 (`9d8a027`, self-diagnosing) is superseded by r5.

## USB wired pad (primary direction 2026-11-16 ~03:50Z): built, host-tested, NOT flashed

- **Firmware** `firmware/` (switch-pad 0.1.0), built on the Yoga with `tools/build_pad.ps1` at `433f357`
  (ESP-IDF v5.5.6 in `~/build-hosts/toolchains`, esp_tinyusb 1.7.6~2, TinyUSB 0.21.0~6, 0 warnings).
  - Output: `/home/user/ssbu-switch-remote-work/pad/433f357/`.
  - `switch_pad.bin` 234,928 B, sha256 `ab516cdb44329812efa6e95dc55a7406f91f77f37fe5e25ecfc2a447aeafe755`.
  - bootloader 13,632 B `219e7ee1…`; partition table 3,072 B `7f00b6c0…`.
  - The ledger claim was released and the lane cleaned.
- **USB identity:** a HORI POKKEN CONTROLLER 0F0D:0092. The descriptors are from GP2040-CE (MIT, notice in
  `firmware/THIRD_PARTY.md`).
- **Host:**
  - `tools/host/usbpad.py` (`usb://`): the same edenctl API, macros on the device clock, keep-alive PING every
    300 ms, no gate (the scene report is information only), logs via the optional sys-autopilot.
  - `tools/host/sequences.py`: launch/terminate/dismiss with still checks, shared with autopilot.
  - `tools/host/flash_pad.py`: esptool 4.13.0 venv `/home/user/ssbu-switch-remote-work/venv-esptool`.
  - `smoke.py --backend usb --device … pad-check|launch|…`, `switch_test.py --backend usb` (now the default), the
    runner patch, `resume_smoke.ps1 -Phase flash|pad`.
- **Tests:** core 84 checks (`make -C firmware test`); host 116 OK, including `usb://` against the pty simulator
  `firmware/out/padsim` (device-clock macro timing within ±3 ms, watchdog, sequences) and flashing with a fake
  esptool.
- **Blocker (needs the operator, sudo):** my user is not in `dialout`, so the CH343 tty won't be accessible. Either
  fix works:
  - a udev rule, e.g. `/etc/udev/rules.d/99-switch-pad.rules`:
    `SUBSYSTEM=="tty", ATTRS{idVendor}=="1a86", MODE="0660", GROUP="plugdev"`, then
    `udevadm control --reload && udevadm trigger`;
  - or `usermod -aG dialout intpa` plus a re-login.
- **Card session (revised):**
  - REQUIRED `card-jobs/e1-pad-route-nro-3d6e1bb`: remove our boot2.flag, update the NRO (`d4581396…`) and the
    config (scene_host). Dry run OK.
  - OPTIONAL `card-jobs/e2-optional-autopilot-1.5.0` (no order.txt).
  - d1 is superseded.

## Option (d): staged, NOT installed (ruled 2026-11-16; INPUT superseded by the USB pad, see above)

- **Card job** `card-jobs/d1-autopilot-1.5.0-nro-3d6e1bb` (one session; `order.txt` = the ruling). Dry run on a
  fake card mirroring today's state (r1 NRO/config, r3 sysmodule files and flag, another plugin): all readbacks OK,
  the other files unchanged.

  | Destination | Action | Bytes | sha256 |
  | --- | --- | --- | --- |
  | atmosphere/contents/4200000000004150/exefs.nsp | add (sys-autopilot 1.5.0 release) | 233,732 | b132156a53fd9c05e35b87a58502f5a7320376301dd7186c9bbc72ed33d14ea8 |
  | atmosphere/contents/4200000000004150/toolbox.json | add (ours: "sys-autopilot (switch-remote driver)", requires_reboot false) | 107 | a9b941165287295b5e7b6f06e22353eaac394566144d6bd261dde31d8b1f340f |
  | config/sys-autopilot/config.ini | add (release default + our token + `log = true`; 0600) | 1,081 | (secret) |
  | atmosphere/contents/4200000000005352/flags/boot2.flag | REMOVE (our sysmodule no longer autostarts) | 0 | e3b0c442… |
  | atmosphere/contents/01006A800016E000/romfs/skyline/plugins/libssbu_switch_remote.nro | replace 1b5391a3… (rollback copy on card) | 36,864 | d4581396f2133e630ee2b4c34bf8722020fcb357d4024efb756f813fe35e24ba |
  | ultimate/switch-remote/config.txt | replace (same keys + `scene_host=172.30.254.66`; rollback copy) | 200 | (secret) |
  | atmosphere/contents/4200000000004150/flags/boot2.flag | NOT written | | |

- **Later job** `card-jobs/d2-autopilot-boot2`: sys-autopilot's boot2.flag only, after it has served `/status`.
  It has no order.txt.
- **Superseded jobs** (marked `SUPERSEDED.txt`): r4, r5, fallback-autopilot-6ca6aac and cleanup-autopilot-boot2.
- **First start:** ovlSysmodules **A then Y once**. A also creates the boot2.flag (`gui_main.cpp:96-100`), and Y
  deletes it.
- **Host:**
  - `scened.py` (receiver + gate, TCP 47131, unix socket; user unit `sr-scened` started by the resume script).
  - `homegate.py` (refs `tools/host/refs/home-first-tile-ssbu/`, reviewed by me; the primary's countersignature
    is pending).
  - `autopilot.py`:
    - gated input with a watchdog, and host leases;
    - host-timed macros (`late_ms_max`);
    - launch/terminate/relaunch;
    - list/file/fetch.
  - `switch_test.py --backend autopilot` (the default) and `smoke.py --backend autopilot driver-check|lease-check`.
- **The host IP:** `172.30.254.66` is a static secondary address on the Surface's wired NIC. ufw is off.

## Hardware smoke plan (in order)

Option (d): every step runs with `--backend autopilot`; `resume_smoke.ps1 -Phase smoke` runs 0-2 and the lease check.
0. `driver-check`: sys-autopilot answers, and a tap on HOME is REFUSED (`no_scene_report`): the gate fails closed.
1. Discovery finds the console; `smoke.py preflight` (status, capture, crash listing).
2. `smoke.py launch` (HOME → image gate → A) → the NRO's reports arrive at the host, `gate=allowed`.
3. Library segments to the character select; single steps to Training and back (shot after each).
4. Local-wireless lobby with one Eden LDN peer (two-PC rules; the host accepts the guest): `smoke.py lan-check`.
5. ovlSysmodules: stop sys-autopilot with the controller attached (does the virtual pad vanish? check the
   controllers screen with a capture), then A + Y to start it again; the client reconnects with no reboot.
6. `smoke.py terminate` (HOME → still → X → still → A; crash-report and log fetch), `smoke.py bye`.
7. Then the later d2 session (sys-autopilot's boot2.flag), on a new order.

## Next commands

0. **USB pad (new primary route):**
   1. The operator applies the serial-access fix (above).
   2. Plug the board's UART port (beside BOOT/IO0; antenna up: right) into the Surface. Then
      `/usr/bin/pwsh -NoLogo -NoProfile -NonInteractive -File tools/host/resume_smoke.ps1 -Phase flash` (receipt in
      the artifacts).
   3. Plug the OTG port (beside EN/RST; left) into a dock USB-A port.
   4. With the console on HOME and SSBU first, run `... -Phase pad` (pad-check: `usb_mounted=1`; preflight;
      launch).
   5. If the console ignores the pad: System Settings → Controllers and Sensors → Pro Controller Wired
      Communication ON.
   6. The card session (e1, optionally e2) is still needed for the NRO/config update and the boot2 removal. It is
      independent of the pad.
1. The CARD INSTALL REQUEST is posted to the INBOX (revised: e1 required, e2 optional); wait for the order (END).
2. On the order, with the console in UMS or DBI MTP:
   `/usr/bin/pwsh -NoLogo -NoProfile -NonInteractive -File tools/host/resume_smoke.ps1 -Phase card`.
3. The operator reboots, presses A then Y on "sys-autopilot (switch-remote driver)" in ovlSysmodules, and leaves
   the HOME menu with SSBU first. Then run `... resume_smoke.ps1 -Phase smoke`.
4. Then the hardware smoke plan above. The local-wireless step needs an Eden ldn_mitm peer (pending).

## Checklist (playbook `docs/CHECKLIST.md`)

| Item | Tag | Result |
| --- | --- | --- |
| cdylib only; release profile; skyline =0.6.0; toolchain 1.98.1 | M | pass (compliance) |
| model.rs host tests / runtime cfg(switch) | M | pass |
| Gates: test, clippy -D warnings, ndf, skyline release, diag | M | pass (`3d6e1bb`, NRO 0.2.0) |
| No heap in hooks / counting allocator | M | pass: no hooks; `heap_allocations` in the install line |
| Fixed buffers, thread on a static stack | M | pass (`src/runtime.rs`) |
| Root cause / workarounds approved | M | pass: none shipped |
| Refusals cite approved restrictions | M | pass (0 new Rust refusals); `SR-LAUNCH-01` now approved by ruling A |
| Design report trace table | M | see the compliance report |
| Version guard; 64-byte guard | M | pass (`main+0x1360928`) |
| Card scan before the site | M | N/A for hooks: the NRO hooks nothing. Scan of card-rc13 (2026-11-14): no other NRO names the hid functions the fallback would use, except Deluxe and the overlay (calls only) |
| Hook sharing/chaining | C | N/A: no hooks |
| Hook after prologue | M | N/A: no hooks |
| Hooks never block on a plugin lock | M | N/A: no hooks; the one plugin thread takes no lock shared with game code |
| No diag strings in the shipped NRO | M | pass (compliance) |
| Shipped NRO writes only its install line | M | pass by code (`diag` gates the other lines); to be confirmed in Eden |
| Online read-only rules / replay A/B / two consoles | C | N/A: the NRO only reads two words and never runs game code; input refused online |
| Eden rows (claim, profile copy, runner, gates, prune, console.log) | M | pending: Eden proof not yet run |
| README + GIF | M | fail: README written, no GIF before hardware |
| Evidence doc | M | DESIGN.md §10 + evidence/capture-probe |
| Handoff current | M | this file |
| Release / card | C | not authorized (no order) |
| User connects/disconnects the Switch; nothing mounted | M | pass: read-only listing of the user's UMS mount |
| Nothing pushed/tagged/written to the card | M | pass |
| Forgejo remote | M | not authorized (no order to create or push) |
| Model gate + watcher | M | pass (claude-opus-5-5 / high, re-checked at each resume) |
