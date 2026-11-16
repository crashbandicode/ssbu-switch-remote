# Provenance

switch-remote is MIT (`LICENSE`). Everything in this repository is written here. The projects below were read
(studied) or used as tools. **No code from them is copied into this repository.** Ideas taken from them are
re-implemented and attributed at the point of use.

Clean room: the upstream Training Modpack, UnclePunch and TM-CE sources were never opened (brief, 2026-11-15).
The game facts (mode word `main+0x53050f0`, guard `main+0x1360928`) come from this project's own reading of the
13.0.5 binary (DESIGN.md §2).

## Re-implemented ideas (attributed)

| Source | Studied at | License | What was taken (as an idea) | Where |
| --- | --- | --- | --- | --- |
| sys-autopilot, TooTallNate (github.com/TooTallNate/sys-autopilot) | `6ca6aac`, read-only clone `/home/user/ref/sys-autopilot` | No LICENSE file; `package.json` declares MIT; GitHub reports no license | (1) Rebuild the listeners whenever nifm's IP changes, none→IP included (`source/common/server.c:227-251`). (2) Take part in PSC sleep/wake: close sockets, release the HDLS work buffer, no fsp/bsd IPC until wake, then acknowledge (`source/common/power.c`, `server.c:187-219`). (3) Nintendo-default socket sizes (`source/main.c` socket config). | sysmodule r5 (`1cda5f7`): `sysmodule/source/main.cpp` (`init_power`, `power_poll`, `current_ip`, the serve loop, `init_sockets`), `sysmodule/core/core.hpp` (`IpWatch`, `kIpPollMs`) |
| ns2bench (github.com/phantomptr/ns2bench) | `a105ca7` | GPL-3.0-or-later (+ MIT parts) | Input goes neutral when the client leaves; a rolling clip before a failure (OBS replay buffer) | DESIGN.md §7 |
| sys-botbase | `ae13548c` | GPL-3.0 | How an HDLS virtual Pro Controller is attached (`source/commands.c:189-228`) | DESIGN.md §1 |
| ldn_mitm | `9a2639ef` | GPL-2.0 | A known-good bsd:u configuration (r4; r5 moved to sys-autopilot's sizes) | SMOKE-01-REVIEW.md |
| ovl-sysmodules | `9b114b3`, `/home/user/ref/ovl-sysmodules` | GPL-2.0 | How it starts and stops modules and handles `boot2.flag` (`source/gui_main.cpp:75-114`), so ours survives a hard kill and the switch-over procedure is right | DESIGN.md §3a, §11 |
| Atmosphere | line references in the docs | GPL-2.0 | Loader NPDM checks (`ldr_meta.cpp`), boot2 behaviour, creport | `tools/check_nsp.py` (own code), SMOKE-01-REVIEW.md |
| Eden | CLIControl docs, `src/yuzu/macro_file.cpp` | GPL-3.0 | The edenctl envelope, lease defaults and the macro button order (an interface we stay compatible with) | `tools/host/switch_remote.py` |

## Linked or used as tools (not copied)

| Item | Version | License | Use |
| --- | --- | --- | --- |
| libnx | 4.12.0 (studied at `b156b477`) | ISC | Linked into the sysmodule |
| devkitA64 | r30 | GPL (toolchain) | Builds the sysmodule and the fallback |
| skyline (crate) | =0.6.0 | MIT | The NRO's plugin runtime |
| GStreamer, obs-websocket v5 | system | LGPL / GPL (separate processes) | Capture, through their CLIs and protocols |

## The sys-autopilot binary (fallback driver)

Per the primary ruling of 2026-11-16, sys-autopilot is kept as a FALLBACK console driver (DESIGN.md §11):
- **Build.** `tools/build_autopilot.ps1` builds it unmodified from the pinned source `6ca6aac` on the Yoga. The
  output, recorded in `build.json`, is `sys-autopilot.nsp` 238,539 B, sha256
  `ea5dfc674f7eb528f331f14a6d6c423358aa15493d8b481f4c1732044e71ff68`.
- **Internal use only.** It is staged for the operator's own console and is **not redistributed**, and nothing
  of it is in this repository.
- **License.** Because it has no LICENSE file, no redistribution is planned. If one were ever needed, the
  license question goes to the operator first.
- **Our files beside it.** We supply only our own files: `toolbox.json` (`requires_reboot: false`) and a
  `config.ini` holding our token. The host backend `tools/host/autopilot.py` speaks its documented REST API
  (README "REST API"). Its test server `tools/host/tests/test_autopilot.py` is written from that README section.
