# HANDOFF: switch-remote

Updated 2026-11-16 ~03:30Z. Worker: switch-remote (Opus 5.5, high), session `81d86844-b65c-42b8-bf83-c7b6d7ba2c32`,
spawned by the rc6-integration primary. Playbook followed: `ssbu-mod-playbook` `3332c0d`.

## Objective and scope

The goal is to remote-control the physical Switch (SSBU 13.0.5) as agents drive Eden. The work has three parts:
- a sysmodule: input, launch and terminate, logs, and the LAN server;
- an in-game NRO: the scene reporter for the fail-closed online gate;
- host capture through OBS (primary) or capd (V4L2 fallback), plus the `switch://` runner backend.

`DESIGN.md` (revision 3) is the design, with the rulings recorded in it.

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

## State

- Branch `main`, no remote, nothing pushed or tagged; HEAD is the latest commit (`git log -1`).
- NRO gates: green on `e2e802a` (Yoga, `remote-gate.ps1`): test 21/0, clippy 0, ndf 0, release and diag
  36,864 B each. Release sha256 `1b5391a3b17318d01b6ef1fe32f5ec5683566447804727afa031e9ede3806ace`. The NRO
  source is unchanged since. Artifacts: `/home/user/ssbu-switch-remote-work/gates/e2e802a/`.
- Sysmodule: built on the Yoga at `39ebc14` (`tools/build_sysmodule.ps1`). exefs.nsp is 142,484 B, sha256
  `1ab635cf9398d2a19bdd3b90cf808d3ffa03332299eea6d886c7ad25bf73c93b`; the image is 1.38 MiB. Artifacts:
  `/home/user/ssbu-switch-remote-work/sysmodule/39ebc14/`. A later commit that only moved host files needs no
  rebuild (`sysmodule/` unchanged except the Makefile comment).
- Host: 61 tests OK (`/usr/bin/python3 -I -m unittest discover -s tools/host/tests`, after
  `make -C sysmodule out/loopback`); card tool 16 OK (`-s tools/card/tests`). Core: 239 checks (2026-11-16 ~03:25Z).
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

## r5 + sys-autopilot fallback (ruled 2026-11-16; built, staged, NOT installed)

- **r5** = sysmodule 0.3.0, `1cda5f7`. exefs.nsp 158,967 B, sha256
  `b4f634a8cc94a3ab84b2d55021b9c3fd62af335fb4e4799b67341750102ccc13`, `check_nsp` ok. It adds an IP-change
  listener rebuild, PSC sleep/wake and default socket sizes (DESIGN.md §11, attribution in `docs/PROVENANCE.md`).
  - Job `card-jobs/r5-sysmodule-1cda5f7`: exefs replaced; toolbox and boot2.flag stay `unchanged` if present.
  - Dry run on a copy of r3's files: OK.
- **Fallback** = sys-autopilot from `6ca6aac`, built by `tools/build_autopilot.ps1` (Yoga).
  - `sys-autopilot.nsp` 238,539 B, sha256 `ea5dfc674f7eb528f331f14a6d6c423358aa15493d8b481f4c1732044e71ff68`.
  - Job `card-jobs/fallback-autopilot-6ca6aac`:
    - exefs.nsp;
    - our toolbox.json (118 B, `ba5797d8…`, requires_reboot false);
    - `config/sys-autopilot/config.ini` (1,081 B, our token, `log = true`, 0600);
    - NO boot2.flag.
  - Dry run OK.
- **Backstop job** `card-jobs/cleanup-autopilot-boot2`: a `remove.txt` for sys-autopilot's boot2.flag. It has
  no order.txt, so it is refused until the primary orders it.
- **Host:**
  - `tools/host/autopilot.py` (`autopilot://`, `--backend autopilot`): host-side leases, refuses `two_drivers`
    while port 47130 answers, HOME/CAPTURE refused, `macro.*`/`switch.*` unsupported.
  - `discover.py --backend autopilot`; `smoke.py --backend autopilot fallback-check`.
  - `own_install.py`: autopilot allowlist plus a receipted `remove.txt`.
  - The runner patch routes `autopilot://` too.
- **Switch-over** (DESIGN.md §11, from ovl-sysmodules `gui_main.cpp:75-114`):
  - A on a running module kills it and deletes its flag; A on a stopped one starts it and creates the flag; Y
    only toggles the flag.
  - So: stop ours with `switch_remote.py shutdown` (not A); start sys-autopilot with A, then **Y once**.
- **Resume** (`tools/host/resume_smoke.ps1`):
  1. `-Phase card` (UMS/DBI): r3 logs and both flags listings read-only, then the r5 job and the fallback job.
  2. The operator reboots.
  3. `-Phase smoke`: 2 min for r5.
  4. If r5 isn't there: the operator does A + Y on sys-autopilot, then `-Phase fallback`.

## Hardware smoke plan (in order)

1. Discovery finds the console; `smoke.py preflight` (status, capture, records, crash listing).
2. `smoke.py launch` (first-tile check) → SSBU running, scene report, `gate=allowed`.
3. Library segments to the character select; single steps to Training and back (shot after each).
4. Local-wireless lobby with one Eden LDN peer (two-PC rules; the host accepts the guest): `smoke.py lan-check`.
5. ovlSysmodules: toggle switch-remote OFF with the controller attached (does the virtual pad vanish? check the
   controllers screen with a capture), then ON; the client reconnects with no reboot.
6. Update without a reboot: `switch.update` a new `sysmodule.exefs` (`restart`), check STATUS from the new
   instance; `nro` update + `RELAUNCH`, check the install line in `ultimate/switch-remote/events.log`.
7. `smoke.py terminate` (crash-report and log fetch), `smoke.py bye`.

## Next commands

1. When the operator is back: post the CARD INSTALL REQUEST table (r5 job + fallback job, with sha256) to the
   primary's INBOX, then END and wait for the order.
2. On the order, with the console in UMS or DBI MTP:
   `/usr/bin/pwsh -NoLogo -NoProfile -NonInteractive -File tools/host/resume_smoke.ps1 -Phase card`.
3. The operator reboots. Then `... resume_smoke.ps1 -Phase smoke`. If it exits 2, the operator does A then Y on
   "sys-autopilot (switch-remote fallback)" in ovlSysmodules, then `... -Phase fallback`.
4. Then the hardware smoke plan above. The local-wireless step needs an Eden ldn_mitm peer (pending).

## Checklist (playbook `docs/CHECKLIST.md`)

| Item | Tag | Result |
| --- | --- | --- |
| cdylib only; release profile; skyline =0.6.0; toolchain 1.98.1 | M | pass (compliance) |
| model.rs host tests / runtime cfg(switch) | M | pass |
| Gates: test, clippy -D warnings, ndf, skyline release, diag | M | pass (`e2e802a`) |
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
