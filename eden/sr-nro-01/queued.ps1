#requires -Version 7
<#
switch-remote NRO Eden proof, queued on the Yoga (primary ruling 2026-11-15: one Yoga diag boot after deluxe-gamespeed's
perf-sensitive pair releases; evidence for the scene gate, not a hardware precondition).

Runs ON the Yoga from its staging folder (stage.ps1 copies it there):
  <stage>/rows/{rows.json,steps.py}   this plan
  <stage>/src/sysmodule/...           the sysmodule source (for the host loopback build)
  <stage>/nro/libssbu_switch_remote.nro  the diag NRO

Waits (60 s polls, up to -WaitHours) until the last ledger event naming dgs-cal is a RELEASED line and no Eden runs on this
host; builds sysmodule/out/loopback (a booked build); starts it on 127.0.0.1:47230/47231 with a test-only token; makes
the run with eden_test.py new (cloned from the rc15 final config: same Eden 2f315fc4 and game, profile g), pins only
this NRO + its config.txt; Up, run, Down (always), stops the loopback. Output: <run>/sr-summary.json.
#>
param(
    [string]$Stage = '/home/user/eden-runs-staging/sr-nro-01',
    [string]$Label = 'sr-nro-01',
    [string]$From = '/home/user/eden-runs/rc15-integration-35268287-final/config.json',
    [string]$GdbHelpers = '/home/user/eden-runs/rc15-integration-35268287-final/rows',
    [double]$WaitHours = 4,
    [int]$Port = 47230,
    [string]$Display = ':78'
)
$ErrorActionPreference = 'Stop'
$ledger = '/tmp/deluxe-surface-1007/OWNER-COORDINATION.md'
$pb = '/home/user/ssbu-mod-playbook'
$et = "$pb/tools/eden-test/eden_test.py"
$run = "/home/user/eden-runs/$Label"
$log = "$Stage/queued.log"
function Say($m) { "$(Get-Date -AsUTC -Format o) $m" | Tee-Object -FilePath $log -Append }

function Ready {
    $lines = Get-Content $ledger | Where-Object { $_ -match 'dgs-cal' -and $_ -match '\b(CLAIMED|RELEASED):' }
    $last = $lines | Select-Object -Last 1
    $released = (-not $last) -or ($last -match 'RELEASED:')
    $eden = @(Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -like '*Eden*' -or $_.ProcessName -match '^(eden|AppRun)' }).Count
    return @{ ok = ($released -and $eden -eq 0); last = $last; eden = $eden }
}

Say "queued: waiting for dgs-cal RELEASED and no Eden (up to $WaitHours h)"
$deadline = (Get-Date).AddHours($WaitHours)
while ($true) {
    $r = Ready
    if ($r.ok) { break }
    if ((Get-Date) -gt $deadline) { Say "gave up waiting: last dgs-cal line: $($r.last); eden=$($r.eden)"; exit 3 }
    Start-Sleep -Seconds 60
}
Say 'host free; building the loopback Service'
$bid = (& /usr/bin/python3 -I ~/build-hosts/bin/ledger.py claim --host yoga --owner switch-remote --note "loopback for $Label" | Select-Object -Last 1).Trim()
try {
    & systemd-run --user --scope -q -p MemoryMax=1G /usr/bin/timeout 300 nice -n 19 make -C "$Stage/src/sysmodule" out/loopback *>> $log
    if ($LASTEXITCODE -ne 0) { throw 'loopback build failed' }
} finally {
    & /usr/bin/python3 -I ~/build-hosts/bin/ledger.py release --host yoga --owner switch-remote --bid $bid --note 'loopback built' | Out-Null
}

# Test-only token (not the console's).
$bytes = [byte[]]::new(18); [Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
$tok = [Convert]::ToHexString($bytes).ToLower()
$cfgText = "token=$tok`nport=$Port`npeers=loopback`n"
$fix = "$Stage/fixtures"
New-Item -ItemType Directory -Force $fix | Out-Null
[IO.File]::WriteAllText("$fix/config.txt", $cfgText)
[IO.File]::WriteAllText("$fix/loopback-config.txt", $cfgText)

# Helpers for the no-hold GDB stub, copied from the rc15 run folder (operator's own code; not committed here).
foreach ($f in 'gdb_rc11.py', 'gdbdump.py', 'gdb-nohold.txt') { Copy-Item "$GdbHelpers/$f" "$Stage/rows/$f" }
'6591' | Set-Content "$Stage/rows/gdb-port.txt"

Say "eden_test new $Label"
& /usr/bin/python3 -I $et new --from $From --label $Label --rows "$Stage/rows" --owner "Claude 81d86844/switch-remote" *>> $log
if ($LASTEXITCODE -ne 0) { Say 'new failed'; exit 4 }
[IO.File]::WriteAllText("$run/sr-test-token", $tok)
$c = Get-Content "$run/config.json" -Raw | ConvertFrom-Json -AsHashtable
$c.Remove('plugin_swaps')
$c['perf_sensitive'] = $false
$c['runtime'] = 3600
$c['authorization'] = 'Primary ruling 2026-11-15 (switch-remote): ONE Yoga NRO diag boot after deluxe-gamespeed pair RELEASED; evidence for the scene gate. Muted, memory_layout_mode 0, GDB stub before game.load. No Surface, push, tag, release or card write.'
$inst = $c['instances'][0]
$c['instances'] = @($inst)
$inst['display'] = $Display
$nro = "$Stage/nro/libssbu_switch_remote.nro"
$inst['plugins'] = @(@{ path = $nro; sha256 = (Get-FileHash $nro -Algorithm SHA256).Hash.ToLower() })
$keep = @($inst['files'] | Where-Object { $_.to -eq 'user/config/qt-config.ini' })
$inst['files'] = $keep + @(@{ path = "$fix/config.txt"; sha256 = (Get-FileHash "$fix/config.txt" -Algorithm SHA256).Hash.ToLower(); to = 'user/sdmc/ultimate/switch-remote/config.txt' })
$inst['sources'] = @{ diag_log = 'user/sdmc/ultimate/switch-remote/events.log' }
$c | ConvertTo-Json -Depth 10 | Set-Content "$run/config.json"

$lb = Start-Process -FilePath "$Stage/src/sysmodule/out/loopback" -ArgumentList @('--port', $Port, '--root', "$run/loopback-sd", '--pad-log', "$run/loopback-pad.jsonl", '--config', "$fix/loopback-config.txt") -RedirectStandardOutput "$run/loopback.out" -RedirectStandardError "$run/loopback.err" -PassThru
New-Item -ItemType Directory -Force "$run/loopback-sd" | Out-Null
Start-Sleep -Seconds 1
$summary = [ordered]@{ label = $Label; started = (Get-Date -AsUTC -Format o); loopback_pid = $lb.Id }
try {
    Say 'up'
    & /usr/bin/python3 -I $et up $run *>> $log
    $summary.up = $LASTEXITCODE
    if ($LASTEXITCODE -eq 0) {
        Say 'run'
        & /usr/bin/python3 -I $et run $run *>> $log
        $summary.run = $LASTEXITCODE
    }
} finally {
    Say 'down'
    & /usr/bin/python3 -I $et down $run *>> $log
    $summary.down = $LASTEXITCODE
    Stop-Process -Id $lb.Id -ErrorAction SilentlyContinue
    $summary.ended = (Get-Date -AsUTC -Format o)
    $summary | ConvertTo-Json | Set-Content "$run/sr-summary.json"
    Remove-Item -Recurse -Force "$Stage/src" -ErrorAction SilentlyContinue
    Say "done: $($summary | ConvertTo-Json -Compress)"
}
