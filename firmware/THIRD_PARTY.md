# Third-party material in firmware/

## GP2040-CE: USB descriptor bytes (MIT)

`firmware/main/main.cpp` (device, configuration and HID report descriptors, strings) and the HORI button masks in
`firmware/core/pad.cpp` reuse values from GP2040-CE `headers/drivers/switch/SwitchDescriptors.h`, at commit
`0195793273372c9dc05f2989dc27ae4891bbf82a` (https://github.com/OpenStickCommunity/GP2040-CE).

```
SPDX-License-Identifier: MIT
SPDX-FileCopyrightText: Copyright (c) 2021 Jason Skuby (mytechtoybox.com)

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
documentation files (the "Software"), to deal in the Software without restriction, including without limitation the
rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit
persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the
Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE
WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR
OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
```

The descriptor layout itself was reverse-engineered by progmem/Switch-Fightstick (MIT, `af49d546`), from HORI's
Pokken Tournament Pro Pad.

## GC mode: GameCube Controller Adapter interface facts (nothing copied)

The GC-mode descriptors and report in `firmware/main/main.cpp` and `firmware/core/pad.cpp` are written from public
descriptions of the adapter's interface, cited in `docs/GC-MODE.md`:
- SDL `src/joystick/hidapi/SDL_hidapi_gamecube.c` (zlib);
- the `gc-adapter` crate 0.1.2 (`lib.rs`, `parsing.rs`, `usb/libusb.rs`);
- Dolphin's guide to the official adapter (USB ID 057E 0337, "WUP-028");
- the gcadapter-oc-kmod / gcadapter_oc-dkms READMEs (stock bInterval 8, 125 Hz).

No code is copied. The HID report descriptor is our own, because the official one is not published.

## Studied only (nothing copied)

- ns2bench `firmware/pico/src/main.rs` (`a105ca7`, GPL-3.0): the HORI identity, UART host link and 1 s watchdog
  match ours independently.
- sorasen2020/SwitchControllerESP32 (MIT), celclow/SwitchControlLibrary (MIT): read for the approach.

## Linked at build time

- ESP-IDF v5.5.6 (Apache-2.0); `espressif/esp_tinyusb` 1.7.6~2 and TinyUSB (Apache-2.0 / MIT), fetched by the IDF
  component manager.
