# Starts capd on the card, measures warm screenshot latency and a 5 s recording, then stops it.
# Output: evidence/capture-probe/capd-probe.json. Run on the capture host (ubuntu-surface).
param([string]$Device = '/dev/video4', [string]$Out = "$PSScriptRoot/../evidence/capture-probe")
$ErrorActionPreference = 'Stop'
$sock = "/run/user/$(id -u)/switch-remote-capd-probe.sock"
$shots = "$PSScriptRoot/../shots"
New-Item -ItemType Directory -Force $Out, $shots | Out-Null
$d = Start-Process -FilePath /usr/bin/nice -ArgumentList @('-n', '10', '/usr/bin/python3', '-I', "$PSScriptRoot/capd.py", 'serve', '--device', $Device, '--socket', $sock) -RedirectStandardOutput "$Out/capd-serve.log" -RedirectStandardError "$Out/capd-serve.err" -PassThru
try {
    $deadline = (Get-Date).AddSeconds(15)
    while (-not (Test-Path $sock) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 100 }
    Start-Sleep -Seconds 1
    $cpu0 = (Get-Process -Id $d.Id).TotalProcessorTime.TotalSeconds
    $lat = @()
    foreach ($i in 1..10) {
        $sw = [Diagnostics.Stopwatch]::StartNew()
        $r = & /usr/bin/python3 -I "$PSScriptRoot/capd.py" call ui.screenshot "{`"path`":`"$shots/warm-$i.png`",`"width`":1280}" --socket $sock | ConvertFrom-Json
        $lat += $sw.ElapsedMilliseconds
        if (-not $r.ok) { throw "screenshot failed: $($r | ConvertTo-Json -Compress)" }
    }
    $rec = "$shots/rec-$(Get-Date -AsUTC -Format 'HHmmss').mkv"
    $start = & /usr/bin/python3 -I "$PSScriptRoot/capd.py" call recording.start "{`"path`":`"$rec`"}" --socket $sock | ConvertFrom-Json
    Start-Sleep -Seconds 5
    $stop = & /usr/bin/python3 -I "$PSScriptRoot/capd.py" call recording.stop --socket $sock | ConvertFrom-Json
    $status = & /usr/bin/python3 -I "$PSScriptRoot/capd.py" call status --socket $sock | ConvertFrom-Json
    $cpu1 = (Get-Process -Id $d.Id).TotalProcessorTime.TotalSeconds
    $report = [ordered]@{
        time = (Get-Date -AsUTC -Format o); device = $Device
        screenshot_call_ms = $lat; screenshot_note = 'includes a python process start per call (client), JPEG decode, 1280-wide PNG write'
        last_shot = $r.result; recording_start = $start; recording_stop = $stop.result; status = $status.result
        daemon_cpu_seconds = [math]::Round($cpu1 - $cpu0, 2)
    }
    $report | ConvertTo-Json -Depth 6 | Set-Content "$Out/capd-probe.json"
    $report | ConvertTo-Json -Depth 6
} finally {
    Stop-Process -Id $d.Id -ErrorAction SilentlyContinue
}
