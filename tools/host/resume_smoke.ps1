#requires -Version 7
<#
Resumes the parked hardware smoke. Input route (primary direction 2026-11-16 ~03:50Z, DESIGN.md 13): the ESP32-S3
wired pad (OTG port -> dock, UART port -> this host). sys-autopilot is an OPTIONAL extra (logs/files; option d,
DESIGN.md 12); r5 is parked.

  /usr/bin/pwsh -NoLogo -NoProfile -NonInteractive -File tools/host/resume_smoke.ps1 -Phase flash   # board's UART port plugged in here
  /usr/bin/pwsh -NoLogo -NoProfile -NonInteractive -File tools/host/resume_smoke.ps1 -Phase pad     # board in the dock, console on HOME

  /usr/bin/pwsh -NoLogo -NoProfile -NonInteractive -File tools/host/resume_smoke.ps1 -Phase card    # console in UMS or DBI MTP
  /usr/bin/pwsh -NoLogo -NoProfile -NonInteractive -File tools/host/resume_smoke.ps1 -Phase smoke   # after reboot + manual start

card:  finds the card (UMS mount, else DBI's MTP storage under gvfs), copies READ-ONLY sysmodule.log, status.txt,
       events.log, the crash_reports listing and both sysmodules' flags listings into <Out>/before-<time>/, then runs
       own_install.py preflight + install for each -Jobs folder (default: the one option-(d) job: ADD sys-autopilot
       without boot2.flag, REMOVE our sysmodule's boot2.flag, UPDATE NRO + config). Receipts in the job folder and
       the primary's artifacts. Prints the reboot + manual-start request.
smoke: starts the scene receiver (user unit sr-scened, scened.py serve) if it is not running, waits for
       sys-autopilot on port 4150, then smoke.py --backend autopilot: preflight, driver-check (the gate must REFUSE
       a tap on HOME), launch (HOME -> image-gated still -> A; then the NRO's reports must start and the gate open),
       lease-check. The rest (play/lan-check/terminate/bye) continues by hand (HANDOFF "Hardware smoke plan").
Never writes outside switch-remote's allowlist; never mounts or ejects anything.
#>
param(
    [Parameter(Mandatory)][ValidateSet('card', 'smoke', 'flash', 'pad')][string]$Phase,
    # Required job; add .../e2-optional-autopilot-1.5.0 only if the optional sys-autopilot install is ordered.
    [string[]]$Jobs = @('/home/user/ssbu-switch-remote-work/card-jobs/e1-pad-route-nro-3d6e1bb'),
    [string]$Build = '/home/user/ssbu-switch-remote-work/pad/433f357',
    [string]$Device = 'auto',
    [string]$Out = '/home/user/ssbu-switch-remote-work/smoke-02',
    [string]$Artifacts = '/home/user/smash-training-lobby/artifacts/switch-remote-card',
    [int]$WaitMin = 5
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$py = '/usr/bin/python3'
New-Item -ItemType Directory -Force $Out | Out-Null
function Say($m) { "$(Get-Date -AsUTC -Format 'HH:mm:ssZ') $m" | Tee-Object -FilePath "$Out/resume.log" -Append }

if ($Phase -eq 'card') {
    foreach ($job in $Jobs) {
        if (-not (Test-Path "$job/order.txt")) { throw "no order.txt in ${job}: the primary writes it when ordering this job" }
        if (Test-Path "$job/install-receipt.json") { throw "$job was already installed; use a new job folder" }
        if (Test-Path "$job/SUPERSEDED.txt") { throw "$job is superseded: $(Get-Content "$job/SUPERSEDED.txt" -Raw)" }
    }
    $ums = '/run/media/intpa/SWITCH SD'
    $mtp = Get-ChildItem "/run/user/$(id -u)/gvfs" -Directory -ErrorAction SilentlyContinue | Where-Object Name -like 'mtp:host=*DBI*' | Select-Object -First 1
    if (Test-Path "$ums/atmosphere") {
        $transport = 'ums'; $root = $ums
        $read = { param($rel, $dest) Copy-Item -LiteralPath (Join-Path $ums $rel) $dest -ErrorAction SilentlyContinue }
        $list = { param($rel) Get-ChildItem -LiteralPath (Join-Path $ums $rel) -File -ErrorAction SilentlyContinue | Select-Object Name, Length, LastWriteTimeUtc }
    } elseif ($mtp) {
        $transport = 'mtp'
        $host_ = $mtp.Name.Substring('mtp:host='.Length)
        $root = "mtp://$host_/1%3A%20SD%20Card"
        $read = { param($rel, $dest) & gio copy "$root/$([uri]::EscapeDataString($rel).Replace('%2F', '/'))" $dest 2>$null }
        $list = { param($rel) & gio list -l "$root/$rel" 2>$null }
    } else {
        throw 'card not visible: ask the operator for hekate UMS or DBI MTP (this script never mounts anything)'
    }
    Say "card via $transport ($root)"
    $before = "$Out/before-$(Get-Date -AsUTC -Format 'yyyyMMddTHHmmssZ')"
    New-Item -ItemType Directory -Force $before | Out-Null
    foreach ($rel in 'ultimate/switch-remote/sysmodule.log', 'ultimate/switch-remote/status.txt', 'ultimate/switch-remote/events.log') {
        & $read $rel (Join-Path $before (Split-Path $rel -Leaf))
        Say "read-only copy $rel -> $(if (Test-Path (Join-Path $before (Split-Path $rel -Leaf))) { 'ok' } else { 'absent' })"
    }
    & $list 'atmosphere/crash_reports' | Out-String | Set-Content "$before/crash_reports-listing.txt"
    & $list 'atmosphere/contents/4200000000005352/flags' | Out-String | Set-Content "$before/switch-remote-flags-listing.txt"
    & $list 'atmosphere/contents/4200000000004150/flags' | Out-String | Set-Content "$before/autopilot-flags-listing.txt"
    Say "listings saved; switch-remote boot2: $((Get-Content "$before/switch-remote-flags-listing.txt" -Raw) -match 'boot2'); sys-autopilot boot2: $((Get-Content "$before/autopilot-flags-listing.txt" -Raw) -match 'boot2')"
    Push-Location $repo
    try {
        foreach ($job in $Jobs) {
            $name = Split-Path $job -Leaf
            & $py -I tools/card/own_install.py preflight $job --transport $transport --root $root | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "preflight refused for $name" }
            & $py -I tools/card/own_install.py install $job --transport $transport --root $root
            $code = $LASTEXITCODE
            $dest = Join-Path $Artifacts "install-$name"
            New-Item -ItemType Directory -Force $dest | Out-Null
            Copy-Item "$job/preflight-receipt.json", "$job/order.txt" $dest
            Copy-Item "$job/install-receipt.json", "$job/INSTALL.md" $dest -ErrorAction SilentlyContinue
            if ($code -ne 0) { throw "install failed for ${name}: see $job/install-receipt.json (later jobs NOT run)" }
            Say "$name installed and read back (receipts: $dest)"
        }
    } finally { Pop-Location }
    $ap = $Jobs | Where-Object { $_ -like '*autopilot*' }
    $start = if ($ap) { ' If the optional sys-autopilot was installed: in ovlSysmodules press A on "sys-autopilot (switch-remote driver)", then Y ONCE (A also creates its boot2.flag).' } else { '' }
    Say "Done. Ask the operator to leave UMS/DBI and REBOOT, and leave the console on the HOME menu with SSBU as the first tile.$start Then -Phase flash (once) and -Phase pad."
    exit 0
}

function Find-Pad {
    if ($Device -ne 'auto') { return $Device }
    $found = @(Get-ChildItem /dev/serial/by-id -ErrorAction SilentlyContinue | Where-Object Name -like '*1a86*')
    if ($found.Count -ne 1) { throw "expected one CH343 (1a86) under /dev/serial/by-id, found $($found.Count): plug the board's UART port (beside BOOT/IO0) into this host" }
    return $found[0].FullName
}

if ($Phase -eq 'flash') {
    $dev = Find-Pad
    $rcpt = "$Out/flash-$(Get-Date -AsUTC -Format 'yyyyMMddTHHmmssZ')"
    Say "flashing $Build onto $dev"
    & $py -I "$PSScriptRoot/flash_pad.py" --build $Build --out $rcpt --port $dev
    $code = $LASTEXITCODE
    $dest = Join-Path $Artifacts "flash-$(Split-Path $Build -Leaf)"
    New-Item -ItemType Directory -Force $dest | Out-Null
    Copy-Item "$rcpt/*" $dest
    if ($code -ne 0) { Say "flash FAILED: see $rcpt/flash-receipt.json"; exit 2 }
    Say "flashed and answering (receipt $dest). Plug the board's OTG port (beside EN/RST) into a dock USB port, then -Phase pad."
    exit 0
}

if ($Phase -eq 'pad') {
    $dev = Find-Pad
    $unit = 'sr-scened'
    if ((& systemctl --user is-active $unit 2>$null) -ne 'active') {
        & systemd-run --user --unit $unit --collect -p MemoryMax=200M /usr/bin/python3 -I "$PSScriptRoot/scened.py" serve | Out-Null
    }
    $sm = @('-I', "$PSScriptRoot/smoke.py", '--backend', 'usb', '--device', $dev, '--out', $Out)
    & $py @sm pad-check
    & $py @sm preflight
    & $py @sm launch
    Say "automatic part done; review $Out/shots and receipts.jsonl, then by hand: smoke.py --backend usb --device $dev --out $Out play|step|stick|lan-check|terminate|dismiss|bye"
    exit 0
}

# smoke
$unit = 'sr-scened'
$active = (& systemctl --user is-active $unit 2>$null)
if ($active -ne 'active') {
    & systemd-run --user --unit $unit --collect -p MemoryMax=200M /usr/bin/python3 -I "$PSScriptRoot/scened.py" serve | Out-Null
    Start-Sleep -Seconds 1
}
Say "scene receiver: $(& systemctl --user is-active $unit) ($(& $py -I "$PSScriptRoot/scened.py" status))"
Say "waiting up to $WaitMin min for sys-autopilot on the LAN (port 4150)"
& $py -I "$PSScriptRoot/discover.py" --backend autopilot --every 20 --until-found ($WaitMin * 60) | Tee-Object -FilePath "$Out/discover.log" -Append
$found = (Get-Content "$Out/discover.log" | Select-String '"found": "([0-9.]+)"' | Select-Object -Last 1).Matches.Groups[1].Value
if (-not $found) {
    Say 'sys-autopilot not found. Check the ovlSysmodules list (is it running?), then read sd:/config/sys-autopilot logs via UMS. If it cannot run on fw 23.5.0, the parked r5 is the fallback (DESIGN.md 11-12; needs a new order).'
    exit 2
}
Say "sys-autopilot at $found"
$sm = @('-I', "$PSScriptRoot/smoke.py", '--backend', 'autopilot', '--host', $found, '--out', $Out)
& $py @sm preflight
& $py @sm driver-check
& $py @sm launch
& $py @sm lease-check
Say "automatic part done; review $Out/shots and receipts.jsonl, then continue by hand: smoke.py --backend autopilot --host $found --out $Out play|step|stick|lan-check|terminate|bye"
