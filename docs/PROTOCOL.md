# switch-remote protocol

2026-11-15. The contract between the host client (`tools/host/switch_remote.py`) and the sysmodule
(`sysmodule/core/service.cpp`), and between the in-game NRO and the sysmodule. Tested on the host
against the loopback build (`tools/host/tests/test_switch_remote.py`).

## switch-pad (the ESP32-S3 wired controller, DESIGN.md section 13)

The firmware speaks the host-channel lines below over its UART (921,600 baud, 8N1, LF) with these differences:
- `AUTH` is accepted with any token (`OK auth switch-pad`): the link is physical.
- `VERSION` → `OK version switch-pad <x.y.z> usb=hori-0f0d:0092`.
- `STATUS` → `OK status fw= usb= usb_mounted=0|1 uptime_ms= reports= watchdog_trips= macro=`.
- `BTN 18|19` (HOME, CAPTURE) are accepted, for the host's still-checked sequences. Macros still refuse bits 16+.
- `MPLAY` frames run at 60 Hz of the device clock.
- No gate.
- LAUNCH/TERMINATE/LIST/FILE/PUT/... answer `ERR unsupported`.
- If no line arrives for 1 s, everything goes neutral, including a playing macro (`error=watchdog`).
- Boot noise from the ROM may precede the first reply; the host skips lines until `OK auth`.

GC mode (0.2.0, docs/GC-MODE.md): the board can present a GameCube Controller Adapter (057E:0337) instead of the HORI
pad. Pro mode is the default at every power-up; the mode is never stored.

| Line | Reply | Notes |
| --- | --- | --- |
| `MODE` / `mode` | `OK mode pro\|gc` | |
| `MODE pro\|gc` / `mode pro\|gc` | `OK mode gc switching` / `OK mode gc unchanged` | releases input, stops a macro, USB detach 500 ms, swap, re-attach |
| `TRIG <l 0-255> <r 0-255> <ms>` / `trig <l 0..1> <r 0..1> <ms>` | `OK` | analog trigger lease; `ERR wrong_mode` in Pro mode |
| `MEV <frame> <buttons> <lx> <ly> <rx> <ry> <lt> <rt>` | as MEV | optional analog triggers 0-255 (ignored in Pro mode) |
| `cstick <x> <y> <ms>` | `OK` | v0 right stick (the C-stick) |

- `VERSION` → `… usb=gc-adapter-057e:0337` in GC mode.
- `STATUS` ends with ` mode=pro`, or ` mode=gc gc_init=<0x13 count> gc_rumble=<port-1 byte>`. `usb_mounted=0` from
  the detach until the console has configured the new device.
- GC mode refuses live input that names a button the GameCube pad lacks (`l`, `minus`, `lstick`, `rstick`, `home`,
  `capture`): `ERR no_gc_button <name>`. Macro samples drop those bits. Mapping (CH-F004): zl → L, zr → R, r → Z,
  plus → Start, right stick → C-stick. v0 names: `z`, `gc_l`, `gc_r`, `gc_start`.

## Transport

- TCP, ASCII lines ending in LF (a CR before it is ignored), at most 255 bytes per request.
- Host channel: `0.0.0.0:<port>` (default 47130). One client at a time; peers outside the
  configured ranges (default: private IPv4 and loopback) are closed before reading.
- Scene channel: `127.0.0.1:<port+1>`, loopback peers only, for the in-game NRO. No replies.
- The first request on either channel is `AUTH <token>` (the token in
  `sd:/ultimate/switch-remote/config.txt`). A wrong token closes the connection (`ERR auth` on the
  host channel).
- Replies: `OK …` or `ERR <code> [detail]`. `OK list` and `OK file` are followed by data (below).
  `MEV` is answered only when it fails.

## Host channel

| Request | Reply | Notes |
| --- | --- | --- |
| `AUTH <token>` | `OK auth switch-remote` | first request |
| `PING` | `OK pong <ms>` | sysmodule clock |
| `STATUS` | `OK status k=v …` | `app=running\|none pid program exits launches scene_mode scene_deluxe scene_age_ms gate hdls macro sequence` |
| `BTN <bit 0-15> press\|release\|tap <ms 1-10000>` | `OK` | one button's lease; a new press replaces the deadline |
| `STICK l\|r <x> <y> <ms>` | `OK` | console units ±32767 |
| `RESET` | `OK` | releases everything, stops a macro |
| `MBEGIN <samples ≤4096> <frames ≤216000>` | `OK` | then exactly `samples` `MEV` lines |
| `MEV <frame> <buttons> <lx> <ly> <rx> <ry>` | (none) / `ERR …` | Eden macro bits; frames ascending; bits 16+ refused |
| `MPLAY` | `OK` | plays at 60 Hz of the sysmodule clock; owns the pad |
| `MSTATUS` | `OK macro state=idle\|loading\|ready\|playing\|finished\|error frame= duration= loaded= error=` | |
| `MSTOP` | `OK` | |
| `LAUNCH` | `OK started` / `ERR refused application_running` | HOME, 1.5 s, A; only without an application |
| `DISMISS` | `OK started` / `ERR refused …` | A; only without an application (an error screen) |
| `TERMINATE` | `OK terminated` / `OK not_running` / `ERR terminate_failed\|terminate_timeout` | ns:dev, pgl fallback; waits up to 10 s |
| `RELAUNCH` | as `LAUNCH` | terminate, 1 s, launch |
| `DETACH` | `OK` | removes the virtual controller |
| `LIST <root> [dir]` | `OK list <n>` + n lines `<f\|d\|-> <size> <mtime> <name>` | |
| `FILE <root> <offset> <max ≤65536> <path>` | `OK file <size> <n>` + n bytes | |
| `BYE` | `OK bye`, then close | |

Input (`BTN`, `STICK`, `MPLAY`) is refused with `ERR inject_refused <reason>` unless the gate is open:
`no_scene_report`, `scene_report_stale` (older than 500 ms), `nro_guards_failed`, `online_mode`,
`online_deluxe`, `inject_off`, `sequence_busy`, `input_busy` (a macro owns the pad). When the gate
closes during play, held input is released and a macro stops with `error=<reason>`.

Roots: `crash_reports`, `fatal_reports`, `fatal_errors`, `erpt_reports` (under `/atmosphere`),
`ultimate`, `skyline` (`/atmosphere/contents/01006A800016E000/romfs/skyline`). Paths are relative,
printable ASCII, no `.`/`..` parts, no empty parts, no backslash, at most 160 bytes.

Error codes from parsing: `empty`, `too_long`, `unknown_method`, `bad_arguments`, `out_of_range`,
`bad_root`, `bad_path`.

## Scene channel (from the in-game NRO)

```text
AUTH <token>
SCENE <seq> 0x<mode> <deluxe -1|0|1> <guards 0|1>     every 100 ms
```

`mode` is the game's word at `main+0x53050f0`; `deluxe` is Online Deluxe's ONLINE bit (-1 when the
plugin is absent); `guards` is 1 when the NRO's version and site guards passed. A malformed line
closes the channel.
