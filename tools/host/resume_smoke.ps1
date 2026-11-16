#requires -Version 7
<#
Resumes the parked hardware smoke (primary ruling 2026-11-16, r5 + sys-autopilot fallback):
read r3's logs read-only, install r5 (WITH boot2.flag) and the sys-autopilot fallback (WITHOUT boot2.flag) through
the receipt tool, reboot, smoke r5; if r5 does not listen within 2 min, start sys-autopilot from ovlSysmodules and
smoke through the fallback backend.

  /usr/bin/pwsh -NoLogo -NoProfile -NonInteractive -File tools/host/resume_smoke.ps1 -Phase card      # console in UMS or DBI MTP
  /usr/bin/pwsh -NoLogo -NoProfile -NonInteractive -File tools/host/resume_smoke.ps1 -Phase smoke     # after the reboot
  /usr/bin/pwsh -NoLogo -NoProfile -NonInteractive -File tools/host/resume_smoke.ps1 -Phase fallback  # after the operator starts sys-autopilot

card:     finds the card (UMS mount, else DBI's MTP storage under gvfs), copies READ-ONLY sysmodule.log, status.txt,
          events.log, the crash_reports listing and both sysmodules' flags listings into <Out>/before-<time>/, then runs
          own_install.py preflight + install for each -Jobs folder in order (own files only; receipts in each job
          folder, copied to the primary's artifacts). Stops at the first failure. Prints the reboot request.
          The same phase runs the cleanup job later (-Jobs .../cleanup-autopilot-boot2, after its order.txt exists).
smoke:    waits -WaitMin (2) minutes for r5 on the LAN; found: smoke.py preflight + launch. Not found: prints the
          switch-over request (operator: ovlSysmodules -> start "sys-autopilot (switch-remote fallback)") and exits 2.
fallback: waits for sys-autopilot on port 4150, then smoke.py --backend autopilot fallback-check. The client refuses
          input while our port 47130 answers (one virtual controller at a time).
Never writes outside switch-remote's allowlist; never mounts or ejects anything.
#>
param(
    [Parameter(Mandatory)][ValidateSet('card', 'smoke', 'fallback')][string]$Phase,
    [string[]]$Jobs = @('/home/user/ssbu-switch-remote-work/card-jobs/r5-sysmodule-1cda5f7',
                        '/home/user/ssbu-switch-remote-work/card-jobs/fallback-autopilot-6ca6aac'),
    [string]$Out = '/home/user/ssbu-switch-remote-work/smoke-02',
    [string]$Artifacts = '/home/user/smash-training-lobby/artifacts/switch-remote-card',
    [int]$WaitMin = 2
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
    Say 'All jobs done. Ask the operator to leave UMS/DBI and REBOOT the console, then run -Phase smoke.'
    exit 0
}

if ($Phase -eq 'smoke') {
    Say "waiting up to $WaitMin min for switch-remote r5 on the LAN (port 47130)"
    & $py -I "$PSScriptRoot/discover.py" --every 20 --until-found ($WaitMin * 60) | Tee-Object -FilePath "$Out/discover.log" -Append
    $found = (Get-Content "$Out/discover.log" | Select-String '"found": "([0-9.]+)"' | Select-Object -Last 1).Matches.Groups[1].Value
    if (-not $found) {
        Say 'r5 NOT listening after the wait. Switch-over (DESIGN.md 11): ask the operator to open ovlSysmodules (Tesla), press A on "sys-autopilot (switch-remote fallback)" to START it, then press Y ONCE to delete the boot2 flag A just created (it must never autostart). Leave switch-remote as it is (not listening = no controller held; A on it would delete its own boot2 flag). Then run -Phase fallback. The next card session: read status.txt/sysmodule.log (r5 diagnosis); -Phase card lists both flags folders.'
        exit 2
    }
    Say "switch-remote at $found"
    & $py -I "$PSScriptRoot/smoke.py" --host $found --out $Out preflight
    & $py -I "$PSScriptRoot/smoke.py" --host $found --out $Out launch
    Say "automatic part done; review $Out/shots and continue with smoke.py --host $found --out $Out (play/step/lan-check/terminate/bye)"
    exit 0
}

# fallback
$fb = "$Out/fallback"
New-Item -ItemType Directory -Force $fb | Out-Null
Say "waiting up to $([Math]::Max($WaitMin, 5)) min for sys-autopilot on the LAN (port 4150)"
& $py -I "$PSScriptRoot/discover.py" --backend autopilot --every 20 --until-found ([Math]::Max($WaitMin, 5) * 60) | Tee-Object -FilePath "$fb/discover.log" -Append
$found = (Get-Content "$fb/discover.log" | Select-String '"found": "([0-9.]+)"' | Select-Object -Last 1).Matches.Groups[1].Value
if (-not $found) { Say 'sys-autopilot not found either: stop here and report (both drivers silent: a network-level cause is likely)'; exit 2 }
Say "sys-autopilot at $found"
& $py -I "$PSScriptRoot/smoke.py" --backend autopilot --host $found --out $fb fallback-check
Say "fallback check done; review $fb/shots. Continue by hand with smoke.py --backend autopilot --host $found --out $fb (step/stick/bye). Launching the game stays with the operator in this mode (no first-tile check). REMINDER: the flag A created must be gone (Y once); if not, the staged cleanup job removes it at the next card session. To go back: A on sys-autopilot (stops it), then start ours."
