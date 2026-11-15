# Eden NRO proof, 2026-11-15 (Yoga, Eden 2f315fc4, profile rc15 `g`, diag NRO cd94c3b9…, loopback Service at 9efc533)

| Run | Row | Result | Cause / evidence (`/home/user/ssbu-switch-remote-work/eden-<run>/out/`) |
| --- | --- | --- | --- |
| sr-nro-01 | r00-load | PASS | `plugin=switch-remote/0.1.0 mode_reader=ok config=ok port=47230 result=installed heap_allocations=0`; `scene_channel connected=true` |
| sr-nro-01 | r01-title-gate | PASS | loopback Service STATUS `gate=allowed` from live scene reports |
| sr-nro-01 | r02-main-allowed | FAILED | the game was in its attract loop; one `attract-cancel` replay did not leave the demo battle |
| sr-nro-02 | r00-load | PASS | same install line and channel |
| sr-nro-02 | r01-main-allowed | PASS | `to_main` tapped out of the attract loop; main menu `scene_mode=0x1000000 scene_age_ms=71 gate=allowed` |
| sr-nro-02 | r02-online-menu | FAILED (fixture) | "Unable to connect to the Internet", error 2306-0520: this profile has no LAN-online setup |
| both | r03/r04 | not run | the online rows need a LAN-online profile (the dd-popup profile and `lan_online`) |

Mode words seen (diag log): `0x0`, `0xff10000`, `0x1000000` (boot, title, main), all offline.
Down: both `down=0`, profile restored and verified, RELEASED 23:01:53Z and 23:05:05Z. Up 11 s, rows about 95 s, down about 50 s each.
The online-family refusal is proven on the host (`test_online_scene_refuses`, `test_macro_refused_mid_play_when_scene_goes_online`)
and is next checked in the hardware smoke (the Quickplay menu).
