# Smoke 01 (2026-11-16 01:30Z): "sysmodule not listening", static review

## What was seen

- OBS still (`smoke-01/shots/000-now.png`): the HOME menu, SSBU as the first tile, a wired-LAN icon, no fatal
  screen.
- The Switch answers ARP at 172.30.254.53 (Nintendo OUI `98:e2:55`). TCP 47130/47131 time out with no RST, and
  discovery has never seen 47130 open since 22:20Z.
- The SD card was not readable: the console had left UMS.

## Checked statically

| Suspect | Result | Evidence |
| --- | --- | --- |
| NPDM rejected by the loader | **No**. Every check the loader makes passes on the exact r3 file. | `tools/check_nsp.py`: NPDM/ACID/ACI0 magic, ACID production flag, version fields, sub-region bounds, ACI program id 0x4200000000005352 inside the ACID range and equal to the folder, main priority 49 in [24, 63], core 3 in [3, 3] (AMS `ldr_meta.cpp:79-172`) |
| boot2 launch | **Silent on failure.** A launch error other than out-of-resource/memory/limit is dropped, so a failed launch leaves no process, no fatal and no log. | AMS `boot2_api.board.nintendo_nx.cpp:145-160` (abort only on those three), `:317-325` (SD flagged programs) |
| Crash | **Unlikely.** A crash in a non-application process becomes a fatal error, and none was on screen. | AMS creport (`creport_main.cpp` ~139-159; research report) |
| Log before services | **No.** r1-r3 slept 10 s, then needed sm and fs before the first line; a failure in `smInitialize` returned with no log at all. | r3 `source/main.cpp` `init_services` |
| Early exit | **Yes.** A failed `socketInitialize` logged one line, then idled forever without listening. | r3 `main()`: `init failed; idling` |
| bsd:u config | **Unusual.** UDP buffers 0/0. ldn_mitm, a shipping sysmodule, uses 0x2000/0x2000. Untested whether bsd rejects zeros; a rejection gives exactly this symptom. | r3 `init_services`; ldn_mitm `ldnmitm_main.cpp:51-63` (research report) |
| Blocked service init | **Possible, invisible in r3.** An sm `GetService` for a service not yet registered waits; nothing logged which call was running. | r3 logged results only after all calls |
| Stack | 16 KiB main stack. An overflow would crash (a fatal), so it is not this case; raised for margin. | npdm `main_thread_stack_size` |
| nifm | Not needed to bind 0.0.0.0. r3 never looked, so it couldn't tell whether the LAN was up. | |

## sys-autopilot (evaluated 2026-11-16 ~02:55Z, read only, `/home/user/ref/sys-autopilot` 6ca6aac)

Its hardware-tested server rebuilds the listener on every IP change, because "a network teardown can otherwise
leave the listener silently not accepting (poll() doesn't always report it)" (`source/common/server.c:227-251`,
CHANGELOG lines 94-95). r3 bound once at boot, before DHCP; that is the **probable root cause** of the r3 symptom
(ARP answers, TCP times out with no RST). It also takes part in PSC sleep/wake; per its CHANGELOG, a sleep with
sockets open crashes the console on wake (`server.c:187-219`). License: MIT is declared only in `package.json`,
and there is no LICENSE file, so it is study-only and nothing is copied. Ruled and built (2026-11-16): r5
(`1cda5f7`) is r4 plus an IP-change listener rebuild, PSC sleep handling and default socket sizes, attributed
(DESIGN.md §11, `docs/PROVENANCE.md`). sys-autopilot itself is staged as a hand-started fallback.

## What r4 (sysmodule 0.2.0, `9d8a027`, built, NOT installed) changes

- **SD first.** `sm` + `fs` + the SD mount come first, retried until they succeed. Then a breadcrumb:
  `boot breadcrumb: switch-remote 0.2.0 uptime_ms=… boot_window=…`.
- **Every stage and call is logged.** `stage <name>` lines, plus `init <service> ...` before and `rc=` after each
  call, so a call that never returns is the last line.
- **Network.** `nifm:u` waits up to 120 s for an IP, logs the result, and serving starts either way.
- **Retries instead of idling.** Socket init retries with a backoff (1, 2, 5, then every 10 s). Listen failures
  are rate-limited and recorded.
- **`status.txt`.** One status line, rewritten at every stage and every 10 s:
  `version stage ip listening port uptime socket/nifm attempts config last_error`. It can be read through
  UMS/DBI with no LAN.
- **Settings.**
  - bsd:u UDP buffers 0x2000/0x2000 (ldn_mitm's values).
  - Main stack 64 KiB, pad thread 32 KiB.
- **Tests.** Core: `self_diagnosis_helpers` (232 checks in total). Host: `test_status_file_readable_without_the_lan`.

## Next, when the operator can

1. Put the console in hekate UMS or DBI MTP (after the primary's order for the CARD INSTALL REQUEST).
2. Run `tools/host/resume_smoke.ps1 -Phase card`.
   - It copies the OLD `sysmodule.log`, `status.txt` and `events.log`, plus the crash-report and both flags
     listings, read-only.
   - Then it installs r5 (with boot2) and the sys-autopilot fallback (without boot2), with receipts.
3. The operator reboots the console.
4. Run `tools/host/resume_smoke.ps1 -Phase smoke`: discovery (2 min), then preflight and launch.
5. If r5 does not listen, the operator presses A then Y on sys-autopilot in ovlSysmodules, then
   `-Phase fallback` (DESIGN.md §11).

The old log decides between the two remaining explanations: not launched (no log at all, or no line since the last
boot), or launched and stuck or failing (the last line names the stage).
