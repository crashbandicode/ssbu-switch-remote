# DBI FTP plan r1 (2026-11-16): read-only fetch, then one approved write. NOT EXECUTED.

Nothing here runs until the primary OKs it. The read-only fetch (§3) and the write (§4) are separate OKs. Inputs
come from the ESP32 pad (`usb://`, DESIGN.md 13). Every screen is checked with a capture-card still before the next
press. The permitted presses are D-pad, A (only on an item the still has verified), B, + and HOME. A is never pressed
while a game tile is selected. Any unexpected screen means: stop pressing, take a still, report.

## 1. Preconditions (checked, no input)

- The console is awake on HOME. Image gate `home_first_tile_ssbu` passes; the stills are in
  `ssbu-switch-remote-work/ftp-r1/shots/`.
- `pad STATUS`: `usb_mounted=1`. The OBS source "Switch" (/dev/video4) is active.
- DBI's FTP server takes one session at a time (memory note 2026-10-13: a second client got no banner). So no other
  FTP client may be connected. `gio mount -l` currently shows no FTP mount (checked 2026-11-16).
- **Credentials:** the "Switch SD (FTP)" bookmark has user `ftp-guest` and no password (ftplib sends the standard
  ftp-guest password). They are parsed in memory and never printed.

## 2. Pad route: HOME → Album (hbmenu) → DBI → FTP server

| Step | From (verified by still) | Press | Expected after (verified by still) |
| --- | --- | --- | --- |
| R1 | HOME, game row, SSBU selected | D-pad Down ×1 | the selection is in the bottom icon row, with a name tooltip shown |
| R2 | a bottom-row icon selected | D-pad Right/Left, one press per still | the selected icon is the 4th, the blue picture frame, labelled "Album" (row: Switch Online, News, eShop, **Album**, a teal card icon, Controllers, a tablet icon, System Settings, Sleep) |
| R3 | "Album" selected | A | Atmosphere's homebrew menu (hbmenu). **If the real photo Album opens instead: B (back to HOME) and stop** |
| R4 | hbmenu | D-pad, one press per still | the selected entry's name reads "DBI" |
| R5 | "DBI" selected | A | DBI's main menu (title "DBI", a list of actions) |
| R6 | DBI menu | D-pad Down, one press per still | the highlighted entry reads "Run FTP server" (exact wording read from the screen) |
| R7 | "Run FTP server" highlighted | A | DBI's FTP screen showing the server's address and port (expected 172.30.254.53:<ftp-port>; whatever is shown is used) |

The hbmenu position of DBI, DBI's menu order and its key hints are read from the stills, not assumed. Reference
crops of each verified screen are saved for later automated gates.

## 3. Read-only fetch (`tools/card/own_install.py fetch`; local `artifacts/switch-remote-card/ftp-fetch-r1/`)

How it connects:
- one fresh `ftplib.FTP` connection per operation;
- `TYPE I`;
- a listing does `cwd` then `nlst()`, because DBI's NLST ignores a path;
- **every file is RETR'd twice, and the two sha256s must match**, else it stops;
- no STOR/DELE/RNFR/RNTO/MKD is ever sent in this phase (the tests assert it);
- an existing manifest is never overwritten.

**Stage 1, listings only** (→ `ftp-fetch-r1/listing/fetch-manifest.json`, names only):
- `/ultimate` (to find our folder and TR's log folder);
- `/atmosphere/contents/4200000000005352`;
- `/atmosphere/contents/4200000000004150` (expected absent);
- `/atmosphere/crash_reports`.

**Stage 2, files** (→ `ftp-fetch-r1/files/`, manifest with bytes and sha256 per file):
- `/ultimate/switch-remote/`: `sysmodule.log` (r3's log: the Smoke 01 diagnosis), `events.log` (the NRO install
  line), and `status.txt` (r4+ only; expected absent). Excluded: `config.txt` (our token) and `backups/`.
- `/atmosphere/contents/4200000000005352/`: `exefs.nsp`, `toolbox.json`, `flags/boot2.flag` (expected r3:
  `7ac69bc4…`, `1b4da43d…`, `e3b0c442…`).
- TR's logs, if stage 1 shows them: only `*.log` / `*.txt` files in TR's own folder under `/ultimate/`, each ≤ 4 MiB.

Command shape (the host is the one DBI shows):
`own_install.py fetch <dir> --transport ftp --root ftp://<host>:<port> --list … | --path …`.

## 4. The card change: SEPARATE WRITE, not executed until OK

Job `card-jobs/f1-ftp-retire-sysmodule-boot2`. Its `remove.txt` lists only
`atmosphere/contents/4200000000005352/flags/boot2.flag`. There is no `order.txt` until the OK.

1. `own_install.py preflight <job> --transport ftp --root ftp://<host>:<port>`, read-only. It records the before
   table: sha256 of every file in the plugins folder (TR's NRO and the others, read only), our `4200000000005352`
   folder, the flag, and `4200000000004150` (absent). It keeps a local copy of the 0-byte flag in `<job>/before/`.
2. `own_install.py install <job> …`:
   - DELE the flag;
   - readback: the flag is absent;
   - re-hash every other watched file: the after table must be identical, else `ok=false`;
   - then `install-receipt.json` + `INSTALL.md`.

| destination | before | after | action |
| --- | --- | --- | --- |
| atmosphere/contents/4200000000005352/flags/boot2.flag | e3b0c442… (0 B) | absent | removed |
| every other watched file (plugins incl. TR's NRO, our exefs/toolbox) | sha256 | the same sha256 | unchanged |

- **Effect:** at the next boot our sysmodule no longer starts. The running r3 instance holds no controller and is
  not listening; it ends at that reboot.
- **Rollback:** a job that adds the same empty file back.
- Nothing else is written or deleted. There are no other files and no Atmosphere files.

## 5. Exit route back to HOME

| Step | From | Press | Expected |
| --- | --- | --- | --- |
| X1 | DBI's FTP screen | B (or the stop key DBI shows) | the server stops; DBI's menu |
| X2 | DBI's menu | B or + (as DBI's key hints say) | hbmenu |
| X3 | hbmenu | + (exit) | the HOME menu |
| X4 | HOME, bottom row | D-pad Up | the game row; SSBU selected (image gate passes) |

Fallback at any step: HOME → the HOME menu. Each step is verified by a still. The final state is HOME with SSBU
selected and no app running.
