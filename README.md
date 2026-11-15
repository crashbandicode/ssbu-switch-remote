# switch-remote

Drive a physical Nintendo Switch running Super Smash Bros. Ultimate 13.0.5 the way test agents drive the
Eden emulator. An Atmosphere sysmodule presents a virtual Pro Controller and can launch, terminate and
recover the game and read its crash logs over the LAN. A small Skyline plugin tells the sysmodule whether
the game is offline, and remote input is refused unless it is. The HDMI capture card on the host provides
the screenshots and clips, through OBS when it runs and GStreamer otherwise. The playbook's Eden runner can
target the console with `switch://HOST:PORT` (see `DESIGN.md`).

> **Disclaimer:** this project was developed with the assistance of AI
> (Claude, Codex and other coding agents) under the operator's direction and
> review. Nothing is claimed beyond the dated tests cited below.

**Status, 2026-11-15:** host-tested only. Nothing has run on a console yet, and there is no GIF until the
first hardware smoke records one (docs/media is empty).

## Install (SD card)

- `atmosphere/contents/01006A800016E000/romfs/skyline/plugins/libssbu_switch_remote.nro` (the scene reporter)
- `atmosphere/contents/4200000000005352/{exefs.nsp,toolbox.json,flags/boot2.flag}` (the sysmodule)
- `ultimate/switch-remote/config.txt`, with `token=<16-64 characters>`. Without it, neither part listens or
  injects.

To remove the sysmodule: delete `atmosphere/contents/4200000000005352/` (from hekate UMS if the console won't boot).

## Build

```text
cargo test
cargo clippy --all-targets -- -D warnings
cargo build --no-default-features
cargo skyline build --release                      # the NRO
make -C sysmodule test                             # sysmodule core + host loopback server (system g++)
DEVKITPRO=/opt/devkitpro make -C sysmodule         # the sysmodule (devkitA64 + libnx)
/usr/bin/python3 -I -m unittest discover -s tools/host/tests
```

On this workspace's hosts, the NRO gates run through `remote-gate.ps1` and the sysmodule through
`tools/build_sysmodule.ps1` (the Yoga).

## Use

```text
tools/host/capd.py serve                             # capture fallback when OBS is not running
tools/host/switch_remote.py --host 172.30.254.92 status
tools/host/switch_remote.py --host 172.30.254.92 shot now.png
tools/host/switch_test.py run eden/rows.json --host 172.30.254.92 --mirror diag_log=ultimate:<plugin>/events.log
```

Protocol: `docs/PROTOCOL.md`. Restrictions: `docs/RESTRICTIONS.md`.

## License

MIT (`LICENSE`). No code is copied from GPL projects that were studied (sys-botbase, ldn_mitm, Atmosphere,
ns2bench); libnx (ISC) is linked.
