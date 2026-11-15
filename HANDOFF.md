# HANDOFF: switch-remote

Updated 2026-11-15 22:20Z. Worker: switch-remote (Opus 5.5, high), session `81d86844-b65c-42b8-bf83-c7b6d7ba2c32`,
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

## State

- Branch `main`, no remote, nothing pushed or tagged; HEAD is the latest commit (`git log -1`).
- NRO gates: green on `e2e802a` (Yoga, `remote-gate.ps1`): test 21/0, clippy 0, ndf 0, release and diag
  36,864 B each. Release sha256 `1b5391a3b17318d01b6ef1fe32f5ec5683566447804727afa031e9ede3806ace`. The NRO
  source is unchanged since. Artifacts: `/home/user/ssbu-switch-remote-work/gates/e2e802a/`.
- Sysmodule: built on the Yoga at `39ebc14` (`tools/build_sysmodule.ps1`). exefs.nsp is 142,484 B, sha256
  `1ab635cf9398d2a19bdd3b90cf808d3ffa03332299eea6d886c7ad25bf73c93b`; the image is 1.38 MiB. Artifacts:
  `/home/user/ssbu-switch-remote-work/sysmodule/39ebc14/`. A later commit that only moved host files needs no
  rebuild (`sysmodule/` unchanged except the Makefile comment).
- Host: 40 tests OK (`/usr/bin/python3 -I -m unittest discover -s tools/host/tests`, after
  `make -C sysmodule out/loopback`). Core: 159 checks.
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
- Console discovery poller: `tools/host/discover.py` in the background (every 60 s, 2 h), log
  `/home/user/ssbu-switch-remote-work/discover.log`.
- Eden proof (non-blocking, primary ruling): queued as Yoga user unit `sr-nro-01-queue` (plan `eden/sr-nro-01/`,
  staging `/home/user/eden-runs-staging/sr-nro-01/`, log `queued.log` there, run `/home/user/eden-runs/sr-nro-01/`,
  summary `sr-summary.json`). Stop it with `systemctl --user stop sr-nro-01-queue` on the Yoga (before Up: nothing to undo).
- Hardware smoke runner: `tools/host/smoke.py --host IP --out DIR preflight|launch|play SEG..|step BTN|online-check|terminate|bye`.
- Eden proof ran 2026-11-15 23:00Z (primary GO), two short Yoga boots: install line, channel, title and main gate `allowed` PASS; online rows blocked by the profile (no LAN online, error 2306-0520). `eden/sr-nro-01/RESULTS.md`. The Yoga has a perf-sensitive calibration (dgs-cal-b2) and the XPS a
  timed-row Eden; the Surface is excluded. A ruling was requested (Eden slot first, or card install now).
- Watcher: `/tmp/switch-remote-watcher-81d86844.ps1`, re-armed on each resume (owner = the claude pid).

## Next commands

1. Eden proof of the NRO (Yoga; ledger claim). Boot with the diag NRO and `ultimate/switch-remote/config.txt`
   (`token`, `port`), and run the loopback build on the Yoga at that port, so the NRO's scene reports reach
   the real Service. Walk title → main → Training → Online menu with library segments, and check the
   install line `mode_reader=ok`, the mode words, and the gate (`allowed` offline, `online_mode` online).
2. `CARD INSTALL REQUEST` to the primary (NRO + sysmodule + config, with sha256), then END.
3. After the install: the hardware smoke (preflight; LAUNCH receipts; one capture; menus → Training and back;
   the HDLS timing measurement against capture frames; disconnect).

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
