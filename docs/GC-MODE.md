# switch-pad GameCube-adapter mode (GC mode)

2026-11-17. The operator: "Just add a gc mode to esp32 you can switch to on a dime (like how modern 3rd party
controllers work) and test it that way". The purpose is catalog row **ON-F006** ("Both GameCube triggers belong to
Deluxe", `smash-training-lobby/artifacts/feature-catalog/FEATURE-CATALOG.md`). On a GameCube controller, holding both
triggers holds back TR's overlapping practice chords (D-pad save / record / speed). Releasing one trigger lets the
mapped single-trigger chord work again. CH-F004 (the GameCube button mapping) can share the same run.

## What changes

- **Two USB personalities, one firmware.**
  - **Pro mode** (the default at power-up): the HORI POKKEN CONTROLLER (0F0D:0092) as today. Its descriptors,
    report bytes, timing and protocol replies are unchanged, apart from two additions: `VERSION` reports 0.2.x, and
    `STATUS` has a trailing `mode=pro mounts=N`.
  - **GC mode**: the board presents itself as a **GameCube Controller Adapter** (Wii U / Switch style, 057E:0337).
    Port 1 is our pad; ports 2-4 are empty.
- **Runtime switch, no reflash.** The UART line `MODE gc` or `MODE pro` (or the v0 verb `mode gc`) does this:
  1. it releases all input and stops a macro;
  2. it soft-detaches USB (`tud_disconnect`, D+ pull-up off; the console sees an unplug);
  3. it waits 500 ms;
  4. it swaps the device, configuration, string and HID report descriptors, and the report builder;
  5. it re-attaches (`tud_connect`).

  The console then re-enumerates the board as the other device. No reports are sent until the new enumeration has
  completed (`tud_mount_cb`). `MODE` with no argument reports the current mode.
- **The mode is not stored.** A power cycle or reset always comes back in Pro mode. So a flash, a watchdog reset or a
  replug can never leave the console with a GameCube pad it cannot press HOME with.

## The adapter protocol (public sources)

| Fact | Value | Source |
| --- | --- | --- |
| USB ID | 057E:0337, device "WUP-028" | Dolphin guide "How to use the Official GameCube Controller Adapter for Wii U" (select "WUP-028", "Ensure that its USB ID is 057E 0337"): https://dolphin-emu.org/docs/guides/how-use-official-gc-controller-adapter-wii-u/ ; SDL `SDL_hidapi_gamecube.c` error strings "Couldn't initialize WUP-028" |
| Interface | one HID interface (class 3/0/0), interrupt IN 0x81 of 37 bytes, interrupt OUT 0x02 of 5 bytes | `lsusb -v` of a compatible adapter, which presents the same interface (gist vmedea/4596c4b105a5265bdd9a065eb92c12d1: "wMaxPacketSize 0x0025 1x 37 bytes", "0x0005 1x 5 bytes"); `gc-adapter` crate `src/usb/libusb.rs` (`read_interrupt(0x81, …)`, `write_interrupt(2, …)`) |
| Poll interval | stock adapters: bInterval 8 (125 Hz); "overclocking" rewrites it to 1 | hannesmann/gcadapter-oc-kmod README; KyleGospo/gcadapter_oc-dkms README ("from 125 Hz to 1000 Hz") |
| Init | the host writes one byte `0x13` to OUT 0x02, after which the adapter reports | SDL `initMagic = 0x13` ("This is all that's needed to initialize the device. Really!"); `gc-adapter` `write_interrupt(&[0x13])`; Linux `hid-gamecube-adapter` patch (F.-X. Carton, linux-input 2020) |
| Rumble | the host writes `0x11 p1 p2 p3 p4` (one 0/1 byte per port) to OUT 0x02 | SDL `rumbleMagic = 0x11`; `gc-adapter` `set_rumble` payload; Linux patch v2 rumble support |
| Input report | 37 bytes: `0x21`, then four 9-byte port blocks `status, buttons1, buttons2, stickX, stickY, cX, cY, L, R` | SDL (`packet[0] != 0x21`, size 37, `curSlot += 9`); `gc-adapter` `parsing.rs` (`magic = 0x21`, status, buttons, left stick, right stick, triggers) |
| Status byte | `0x10` = wired controller present, `0x20` = WaveBird, `0x04` = rumble power (second cable) present, `0x00` = empty port | SDL comment "0x10 - Wired, 0x20 - Wireless" and `rumbleAllowed = (curSlot[0] & 0x04) != 0`; `gc-adapter` `ControllerStatus` (type Normal = 1 / Wavebird = 2 in bits 4-5, `has_rumble`) |
| buttons1 | A 0x01, B 0x02, X 0x04, Y 0x08, D-left 0x10, D-right 0x20, D-down 0x40, D-up 0x80 | `gc-adapter` `Buttons` bitfield order (a, b, x, y, dpad_left, dpad_right, dpad_down, dpad_up); SDL reads the same bits (it labels them by position: GC B, at the Xbox-X position, is 0x02) |
| buttons2 | Start 0x01, Z 0x02, R digital 0x04, L digital 0x08 | `gc-adapter` (start, z, right_trigger, left_trigger: "the click when you hold them all the way down"); SDL (START 0x01, RIGHTSHOULDER 0x02, TRIGGERRIGHT 0x04, TRIGGERLEFT 0x08) |
| Axes | 0-255, centre about 128 (`(v - 127.5) / 127.5`), Y grows **upwards** (GameCube convention, no inversion in SDL's Nintendo path); triggers 0-255 (`v / 255`) | `gc-adapter` `axis.rs`; SDL `READ_AXIS(4, LEFTY)` without inversion |

**Not found in a public source: the official 214-byte HID report descriptor.** We therefore write our own small HID
report descriptor. It is vendor-neutral and declares exactly the reports above: an output 0x11 of 4 bytes, an output
0x13 of 1 byte, and an input 0x21 of 36 bytes after its ID.

**ASSUMPTION (settled by the hardware test): the console does not parse the report descriptor.** The Switch drives
the adapter by its USB ID and the interrupt endpoints, as Dolphin and the gc-adapter crate do. If the console does
not show a GameCube controller, the fallbacks in order are:
1. bInterval 1;
2. report even before 0x13 (we already do: see below);
3. a status byte with `0x04`.

No proprietary code is used. The facts above are interface facts, taken from public descriptions. The descriptor
bytes in `main.cpp` are written from them.

## Descriptors in GC mode

- **Device:** bcdUSB 2.00, class 0, bMaxPacketSize0 64, idVendor 0x057E, idProduct 0x0337, bcdDevice 1.00,
  manufacturer "Nintendo", product "WUP-028", no serial string, one configuration.
- **Configuration** (41 bytes, the same total as Pro): bus powered (0x80), 500 mA; interface 0 HID 3/0/0 with 2
  endpoints; HID 1.10 with our report descriptor; EP 0x81 IN interrupt 37 B every 8 ms; EP 0x02 OUT interrupt 5 B every
  8 ms.

## The report in GC mode

The report task still builds a report every 1 ms from the same leases, macro and watchdog. It sends HID report 0x21
(TinyUSB prepends the ID) whenever the endpoint is free. Port 1 is:

| Byte | Content |
| --- | --- |
| status | 0x10 (a wired controller; no rumble power: the board has no motor) |
| buttons1 | A, B, X, Y and the D-pad from Eden bits a b x y and left/right/down/up (opposite directions cancel, as on the hat) |
| buttons2 | Start = `plus`; Z = `r`; R click = `zr`; L click = `zl` |
| stickX/Y | the left stick, 0-255 with 128 at rest and **up = 255** |
| cX/cY | the right stick (the C-stick), the same scale |
| L/R analog | 255 while `zl`/`zr` is held, else the analog lease from `TRIG` (default 0) |

Ports 2-4 are `00` × 9 (empty).

**Mapping rule.** It is the same one the Switch and TR use for a GameCube controller: CH-F004 says "A GameCube
controller uses its L/R/Z in place of Switch ZL/ZR/R". A macro therefore means the same thing in both modes: ZL+down
in Pro mode is L+down in GC mode.

**No GameCube button for** `l`, `minus`, `lstick`, `rstick`, `home`, `capture`, SL/SR:
- live input that names them (`BTN`, `press`) answers `ERR no_gc_button <name>` in GC mode;
- a macro sample's unmapped bits are dropped in GC mode.

HOME needs Pro mode, so the host's LAUNCH / TERMINATE / DISMISS sequences refuse in GC mode (`ERR no_gc_button home`).
Switch back first.

**Output reports.**
- `0x13` (init) is counted (`gc_init=` in STATUS: the evidence that the console drives us as an adapter).
- `0x11` (rumble) is recorded as `gc_rumble=<port-1 byte>` and otherwise ignored.
- Reports are sent from enumeration on and do not wait for 0x13.
- In Pro mode, output reports stay ignored, as today.

## Protocol additions (docs/PROTOCOL.md, switch-pad section)

| Line | Reply | Notes |
| --- | --- | --- |
| `MODE` | `OK mode pro\|gc` | the current mode |
| `MODE pro\|gc` (also `mode …`) | `OK mode gc switching` / `OK mode gc unchanged` | the switch: reset input, detach, 500 ms, swap, attach |
| `TRIG <l 0-255> <r 0-255> <ms 1-10000>` | `OK` | GC mode only (`ERR wrong_mode` in Pro); analog trigger lease; a held `zl`/`zr` still means 255 |
| `trig <l 0..1> <r 0..1> <ms>` | `OK` | the v0 form |
| `MEV <frame> <buttons> <lx> <ly> <rx> <ry> [<lt> <rt>]` | as MEV | optional analog triggers 0-255 per sample (ignored in Pro mode) |
| `VERSION` | `OK version switch-pad 0.2.1 usb=hori-0f0d:0092` or `usb=gc-adapter-057e:0337` | |
| `STATUS` | as before + ` mode=pro mounts=N` or ` mode=gc gc_init=N gc_rumble=N mounts=N` (`mounts`: one per console enumeration; 0.2.1) | `usb_mounted=0` from the detach until the new enumeration |

v0 button names also take `z` (= r), `gc_l`/`gc_r` (= zl/zr) and `start` (= plus, already).

## Host (tools/host)

- **`switch_remote.py`.**
  - New button aliases `z`→`r`, `gc_l`→`zl`, `gc_r`→`zr`, `gc_start`→`plus`, and the stick name `cstick`
    (= right).
  - In the macro format (`eden-input-macro`), an event may give `"buttons"` as a list of names (GC names included)
    instead of the bit mask.
  - An event may give `"triggers": [l, r]` (0..1, Eden's own field and convention). Only a document with a
    **non-zero** trigger value gets MEV lines with the two extra fields. Eden recordings carry `"triggers": [0, 0]`, so
    they and all existing macros produce byte-identical lines (the sysmodule accepts only the six-field MEV).
- **`usbpad.py`.**
  - `pad.mode` (`{"mode": "gc"|"pro"}`) sends `MODE …`, then waits up to 30 s for `usb_mounted=1` in the new mode (on hardware the
    console took more than 10 s to configure the adapter, 2026-11-17).
  - `input.trigger` (`{"l": 0..1, "r": 0..1, "lease_ms": N}`) sends `TRIG`.
  - CLI: `switch_remote.py --backend usb --host /dev/ttyACM0 mode gc|pro`.
  - `hwstep.py` passes `mode gc` / `mode pro` / `trig …` through unchanged.

## Tests

- **`firmware/core/tests` (g++, -Wall -Wextra -Werror).**
  - The GC report layout and every button bit.
  - Stick and trigger scaling, up = 255.
  - Unmapped buttons.
  - `MODE` replies and the pending switch the glue consumes.
  - Mode switching resets input and stops a macro.
  - `TRIG` leases and the watchdog.
  - Macros with triggers.
  - Output reports 0x13/0x11.
  - Pro mode is byte-identical to 0.1.2 for the existing tests.
- **`host_sim/padsim`.**
  - It serves both modes.
  - A switch is logged as `{"t_us", "mode"}`.
  - It simulates a 500 ms detach, then "the console" sending 0x13.
  - GC reports are logged as `"gc": [37 bytes]`.
- **`tools/host/tests/test_usbpad.py`.** Against padsim:
  - the switch, and the round trip back to Pro;
  - GC report bytes for a chord;
  - both-triggers-held plus a D-pad press (the ON-F006 input shape);
  - macros with triggers.

## Flash plan (SAFE)

1. **Window.** Only in a console-free window granted by the primary. `switch-hw-opus-2` pauses between rows. The pad
   is never touched while another lane drives it.
2. **Backup.**
   - `tools/host/pad_backup.py backup --out <dir>` reads the first 0x110000 bytes of flash (bootloader, partition
     table, NVS/phy, and the 1 MB factory app) over the UART with `esptool read_flash`.
   - It records the sha256 and checks that the bootloader and app regions match the `f9208f2` build images
     (`ssbu-switch-remote-work/pad/f9208f2/build.json`). It writes `backup-receipt.json`.
   - Reading resets the board (the console sees the pad unplugged for about 30 s).
3. **Flash.** `flash_pad.py --build <0.2.x build> --out <receipt> --expect-version 0.2.x` (esptool hash-verified
   regions; VERSION after reset).
4. **Prove Pro mode first.**
   - A still on HOME.
   - One menu move (`pad_nav.py press right …` and its still).
   - Back (`left`).
   - `STATUS` shows `mode=pro usb_mounted=1`.

   Only then does anything else run.
5. **Rollback.** `pad_backup.py restore --backup <dir>` writes the backup image back (esptool verifies), or
   `flash_pad.py --build ssbu-switch-remote-work/pad/f9208f2 --expect-version 0.1.2`.

## ON-F006 test (after Pro mode is proven)

In Training with TR and Online Deluxe loaded, 1P:
1. `mode gc` → `STATUS mode=gc usb_mounted=1 gc_init>=1`.
2. A still shows the game accepting the GameCube controller as P1. If the controller is not assigned, press A on the
   GameCube pad, or use the game's controller screen.
3. Hold both triggers (`press zl+zr 3000` keeps both clicks and analog at 255). During the hold, try:
   - the D-pad save chord;
   - record;
   - speed (the mapped L/R + D-pad chords per CH-F004 / the catalog variants).

   Take stills and a clip. **Expected:** nothing fires.
4. Release one trigger and retry the mapped single-trigger chord (e.g. L + D-down save). **Expected:** it works (the
   save notice).
5. `mode pro` → `STATUS mode=pro usb_mounted=1`, then a still and one menu move prove Pro mode again.

## Hardware log

- **2026-11-17 14:40Z: 0.2.0 (4c38f47) flashed.**
  - The backup holds f9208f2 exactly; esptool verified all three regions.
  - Pro smoke PASS: boot default Pro, mounted; HOME menu moves; one A opened System → Controllers, which lists the pad
    as a USB Pro Controller, player 1.
- **14:41:5xZ: `mode gc` from the Controllers screen.**
  - The host gave up at 10 s, but STATUS then showed `usb=gc-adapter-057e:0337 usb_mounted=1 gc_init=1`: the console
    accepted our descriptors and sent the adapter init 0x13, more than 10 s after the attach.
  - `mode pro` mounted in 4.2 s.
  - The operator was rebooting the console by hand at about 14:42Z (after the ldn_mitm install). HDMI showed No Signal,
    so the timing is confounded and GC mode is **not yet passed**.
- **Follow-ups (0.2.1):**
  - the host waits 30 s for a mode switch;
  - STATUS `mounts=` counts the console's enumerations (a reboot shows up);
  - the reply buffer is 256 bytes (the longest GC STATUS fits; tested).
- **Retest rule:** GC only from the HOME menu, with video confirmed in a still first (no applet open), in a window the
  primary gives. A failure runs `mode pro` at once.
