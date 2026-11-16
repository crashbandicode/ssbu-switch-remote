#requires -Version 7
<#
Resumes the parked hardware smoke in one command (2026-11-16 ruling: read the old log first, install r4 through the
receipt tool, reboot, then the smoke).

  /usr/bin/pwsh -NoLogo -NoProfile -NonInteractive -File tools/host/resume_smoke.ps1 -Phase card    # console in UMS or DBI MTP
  /usr/bin/pwsh -NoLogo -NoProfile -NonInteractive -File tools/host/resume_smoke.ps1 -Phase smoke   # after the operator reboots

card:  finds the card (UMS mount, else DBI's MTP storage under gvfs), copies READ-ONLY sysmodule.log, status.txt,
       events.log and the crash_reports listing (plus reports newer than -Since) into <Out>/before-r4/, then runs
       own_install.py preflight + install for the r4 job (switch-remote's own files only; receipts in the job folder
       and copied to the primary's artifacts). Prints the reboot request.
smoke: waits (up to -WaitMin) for the sysmodule on the LAN, then smoke.py preflight and launch (first-tile check), and
       saves a still after each; Training navigation, the ovlSysmodules toggle and updates continue by hand with
       smoke.py (HANDOFF "Hardware smoke plan"). Never writes outside switch-remote's allowlist; never mounts or
       ejects anything.
#>
param(
    [Parameter(Mandatory)][ValidateSet('card', 'smoke')][string]$Phase,
    [string]$Job = '/home/user/ssbu-switch-remote-work/card-jobs/r4-sysmodule-9d8a027',
    [string]$Out = '/home/user/ssbu-switch-remote-work/smoke-02',
    [string]$Artifacts = '/home/user/smash-training-lobby/artifacts/switch-remote-card/install-r4',
    [string]$Since = '2026-11-16T00:00:00Z',
    [int]$WaitMin = 15
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$py = '/usr/bin/python3'
New-Item -ItemType Directory -Force $Out | Out-Null
function Say($m) { "$(Get-Date -AsUTC -Format 'HH:mm:ssZ') $m" | Tee-Object -FilePath "$Out/resume.log" -Append }

if ($Phase -eq 'card') {
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
    $before = "$Out/before-r4"
    New-Item -ItemType Directory -Force $before | Out-Null
    foreach ($rel in 'ultimate/switch-remote/sysmodule.log', 'ultimate/switch-remote/status.txt', 'ultimate/switch-remote/events.log') {
        & $read $rel (Join-Path $before (Split-Path $rel -Leaf))
        Say "read-only copy $rel -> $(if (Test-Path (Join-Path $before (Split-Path $rel -Leaf))) { 'ok' } else { 'absent' })"
    }
    & $list 'atmosphere/crash_reports' | Out-String | Set-Content "$before/crash_reports-listing.txt"
    & $list 'atmosphere/contents/4200000000005352/flags' | Out-String | Set-Content "$before/boot2-flags-listing.txt"
    Say "listings saved; boot2 flag: $((Get-Content "$before/boot2-flags-listing.txt" -Raw) -match 'boot2')"
    Push-Location $repo
    try {
        & $py -I tools/card/own_install.py preflight $Job --transport $transport --root $root | Out-Null
        & $py -I tools/card/own_install.py install $Job --transport $transport --root $root
        if ($LASTEXITCODE -ne 0) { throw "install failed: see $Job/install-receipt.json" }
    } finally { Pop-Location }
    New-Item -ItemType Directory -Force $Artifacts | Out-Null
    Copy-Item "$Job/preflight-receipt.json", "$Job/install-receipt.json", "$Job/INSTALL.md", "$Job/order.txt" $Artifacts
    Say 'r4 installed and read back. Ask the operator to leave UMS/DBI and REBOOT the console, then run -Phase smoke.'
    exit 0
}

# smoke
Say "waiting up to $WaitMin min for the sysmodule on the LAN"
& $py -I "$PSScriptRoot/discover.py" --every 30 --until-found ($WaitMin * 60) | Tee-Object -FilePath "$Out/discover.log" -Append
$found = (Get-Content "$Out/discover.log" | Select-String '"found": "([0-9.]+)"' | Select-Object -Last 1).Matches.Groups[1].Value
if (-not $found) { Say 'sysmodule not found: read status.txt/sysmodule.log via UMS/DBI (-Phase card copies them)'; exit 2 }
Say "sysmodule at $found"
& $py -I "$PSScriptRoot/smoke.py" --host $found --out $Out preflight
& $py -I "$PSScriptRoot/smoke.py" --host $found --out $Out launch
Say "automatic part done; review $Out/shots and continue with smoke.py --host $found --out $Out (play/step/lan-check/terminate/bye)"
