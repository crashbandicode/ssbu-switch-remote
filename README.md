# switch-remote

Drive a physical Nintendo Switch running Super Smash Bros. Ultimate 13.0.5 the way test agents drive the
Eden emulator. An ESP32-S3 board (`firmware/`) plugged into the dock is a wired controller; the host drives it
over the board's serial port, and macros play on the board's own 60 Hz clock (`usb://`, DESIGN.md §13). The host
also launches and terminates the game through image-checked HOME-menu sequences. A small Skyline plugin reports the
game's scene to this host (information on the wired route). [sys-autopilot](https://github.com/TooTallNate/sys-autopilot)
1.5.0 is an optional extra for remote log reads (§12). The HDMI capture card on the host provides the screenshots and clips,
through OBS when it runs and GStreamer otherwise. The playbook's Eden runner can target the console with
`autopilot://HOST` (see `DESIGN.md` §12). This repository's own sysmodule (`sysmodule/`) is parked as the
fallback (`switch://HOST:PORT`, §11).

> **Disclaimer:** this project was developed with the assistance of AI
> (Claude, Codex and other coding agents) under the operator's direction and
> review. Nothing is claimed beyond the dated tests cited below.

**Status, 2026-11-16:** host-tested only. Nothing has run on a console yet, and there is no GIF until the
first hardware smoke records one (docs/media is empty).

## Install (SD card)

- `atmosphere/contents/01006A800016E000/romfs/skyline/plugins/libssbu_switch_remote.nro` (the scene reporter)
- `ultimate/switch-remote/config.txt`, with `token=<16-64 characters>` and `scene_host=<the host's LAN IPv4>`.
  Without it, the plugin does nothing.
- sys-autopilot 1.5.0: `atmosphere/contents/4200000000004150/exefs.nsp` (the upstream release) plus a
  `toolbox.json` with `"requires_reboot": false`, and `config/sys-autopilot/config.ini` with the same token. Start
  it from ovlSysmodules (A, then Y to keep it from autostarting until it is proven on your firmware).
- On the host: `tools/host/scened.py serve` (the scene receiver and gate, TCP 47131).

To remove sys-autopilot: delete `atmosphere/contents/4200000000004150/` (from hekate UMS if the console won't
boot).

## Build

```text
cargo test
cargo clippy --all-targets -- -D warnings
cargo build --no-default-features
cargo skyline build --release                      # the NRO
make -C sysmodule test                             # sysmodule core + host loopback server (system g++)
DEVKITPRO=/opt/devkitpro make -C sysmodule         # the sysmodule (devkitA64 + libnx)
make -C firmware test                              # switch-pad core + pty simulator
idf.py -C firmware set-target esp32s3 build        # the ESP32-S3 firmware (ESP-IDF v5.5.6; tools/build_pad.ps1)
/usr/bin/python3 -I -m unittest discover -s tools/host/tests
```

On this workspace's hosts, the NRO gates run through `remote-gate.ps1` and the sysmodule through
`tools/build_sysmodule.ps1` (the Yoga).

## Configure the device address

Create a local file named `.switch-remote.json` in the repository root:

```json
{
  "host": "switch.example.invalid",
  "network": "192.0.2.0/24"
}
```

Set `host` to your Switch's address. Set `network` to your LAN CIDR if you use
`discover.py`. For the wired backend, set `device` to the USB serial path or pass it on the command line. The file is ignored by Git. Set `SWITCH_REMOTE_CONFIG` to an absolute
path if you keep the file elsewhere. Explicit `--host`, `--device` or `--net`
options take precedence. The token is read separately from
`~/.config/switch-remote/token` or the path in `SWITCH_REMOTE_TOKEN_FILE`.

## Use

```text
tools/host/capd.py serve
tools/host/switch_remote.py status
tools/host/switch_remote.py shot now.png
tools/host/switch_test.py run /path/to/rows.json --backend autopilot
tools/host/switch_remote.py --backend usb call switch.version
tools/host/switch_remote.py --backend autopilot launch
tools/host/skyline_tcplog.py --label capture
```

Protocol: `docs/PROTOCOL.md`. Restrictions: `docs/RESTRICTIONS.md`.

## License

MIT (`LICENSE`). No code is copied from the projects that were studied (sys-autopilot, sys-botbase, ldn_mitm,
ovl-sysmodules, Atmosphere, ns2bench). libnx (ISC) is linked. Details and attributions: `docs/PROVENANCE.md`.

sys-autopilot (the console driver, DESIGN.md §12) is installed from its upstream 1.5.0 release for internal use; it is
not part of this repository and is not redistributed.
