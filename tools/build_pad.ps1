#requires -Version 7
<#
Builds the switch-pad ESP32-S3 firmware (firmware/, DESIGN.md section 13) at a commit on the Yoga with ESP-IDF
v5.5.6 (installed under ~/build-hosts/toolchains). Booked in the build ledger; the lane is cleaned after.

  /usr/bin/pwsh -NoLogo -NoProfile -NonInteractive -File tools/build_pad.ps1 -Rev <commit> -Out <dir>

Out: bootloader.bin, partition-table.bin, switch_pad.bin, flasher_args.json, dependencies.lock, build.log,
build.json (rev, sha256 of each image, idf version).
#>
param(
    [Parameter(Mandatory)][string]$Rev,
    [Parameter(Mandatory)][string]$Out,
    [string]$Repo = (Split-Path $PSScriptRoot -Parent),
    [string]$BuildHost = 'device-2',           # ssh target that builds
    [ValidateSet('yoga', 'xps')][string]$LedgerHost = 'yoga',  # the host booked in the ledger (yoga | xps)
    [string]$LedgerSsh = 'device-2',           # where the ledger lives
    [string]$Idf = '/home/user/build-hosts/toolchains/esp-idf-v5.5.6',
    [string]$IdfTools = '/home/user/build-hosts/toolchains/espressif',
    [string]$Lane = 'switch-remote-pad',
    [int]$WaitMinutes = 120
)
$ErrorActionPreference = 'Stop'
$ssh = @('-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15')
$full = git -C $Repo rev-parse $Rev
New-Item -ItemType Directory -Force $Out | Out-Null
$tar = Join-Path ([IO.Path]::GetTempPath()) "sr-pad-$($full.Substring(0, 8)).tar"
git -C $Repo archive --format=tar -o $tar $full firmware sysmodule/core
$deadline = (Get-Date).AddMinutes($WaitMinutes)
while ($true) {
    $claim = & ssh @ssh $LedgerSsh "/usr/bin/python3 -I ~/build-hosts/bin/ledger.py claim --host $LedgerHost --owner switch-remote --note 'switch-pad firmware $($full.Substring(0, 8))'" 2>&1
    if ($LASTEXITCODE -eq 0) { break }
    if ((Get-Date) -gt $deadline) { throw "ledger claim refused for $WaitMinutes min: $claim" }
    "$(Get-Date -AsUTC -Format 'HH:mm:ssZ') $LedgerHost busy ($((@($claim) -join ' ').Trim().Substring(0, [Math]::Min(120, (@($claim) -join ' ').Trim().Length)))); waiting 60 s"
    Start-Sleep -Seconds 60
}
$bid = (@($claim) | Where-Object { $_ -and $_.Trim() } | Select-Object -Last 1).Trim()
try {
    $remote = "build-hosts/lanes/$Lane"
    & ssh @ssh $BuildHost "Remove-Item -Recurse -Force ~/$remote -ErrorAction SilentlyContinue; New-Item -ItemType Directory -Force ~/$remote/src | Out-Null"
    & scp -q @ssh $tar "${BuildHost}:$remote/src.tar"
    # ESP-IDF's export.sh is bash (remote Linux helpers may use their native shell).
    $sh = @"
set -e
cd ~/$remote/src && tar -xmf ../src.tar && cd firmware
export IDF_TOOLS_PATH=$IdfTools
. $Idf/export.sh > /dev/null
idf.py --version
systemd-run --user --scope -q -p MemoryMax=4G /usr/bin/timeout 1800 nice -n 19 ionice -c 3 idf.py set-target esp32s3
systemd-run --user --scope -q -p MemoryMax=4G /usr/bin/timeout 1800 nice -n 19 ionice -c 3 idf.py build
"@
    $b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($sh))
    & ssh @ssh $BuildHost "echo $b64 | base64 -d > ~/$remote/build.sh; /usr/bin/bash ~/$remote/build.sh *> ~/$remote/build.log; 'exit=' + `$LASTEXITCODE | Add-Content ~/$remote/build.log"  # the login shell is pwsh
    & scp -q @ssh "${BuildHost}:$remote/build.log" (Join-Path $Out 'build.log')
    $tail = Get-Content (Join-Path $Out 'build.log') -Tail 1
    if ($tail -ne 'exit=0') { throw "idf.py build failed ($tail); see $Out/build.log" }
    $b = "$remote/src/firmware"
    foreach ($f in "$b/build/bootloader/bootloader.bin", "$b/build/partition_table/partition-table.bin", "$b/build/switch_pad.bin",
                   "$b/build/flasher_args.json", "$b/dependencies.lock") {
        & scp -q @ssh "${BuildHost}:$f" $Out
    }
    $imgs = [ordered]@{}
    foreach ($n in 'bootloader.bin', 'partition-table.bin', 'switch_pad.bin') {
        $p = Join-Path $Out $n
        $imgs[$n] = [ordered]@{ bytes = (Get-Item $p).Length; sha256 = (Get-FileHash $p -Algorithm SHA256).Hash.ToLower() }
    }
    [ordered]@{ rev = $full; host = $BuildHost; idf = (Select-String -Path (Join-Path $Out 'build.log') -Pattern '^(ESP-IDF )?v5\.' | Select-Object -First 1).Line
        images = $imgs; time = (Get-Date -AsUTC -Format o) } | ConvertTo-Json -Depth 4 | Set-Content (Join-Path $Out 'build.json')
    "ok: switch_pad.bin $($imgs['switch_pad.bin'].bytes) B sha256 $($imgs['switch_pad.bin'].sha256)"
} finally {
    & ssh @ssh $LedgerSsh "/usr/bin/python3 -I ~/build-hosts/bin/ledger.py release --host $LedgerHost --owner switch-remote --bid $bid --note 'switch-pad build done'" | Out-Null
    & ssh @ssh $BuildHost "Remove-Item -Recurse -Force ~/build-hosts/lanes/$Lane -ErrorAction SilentlyContinue"
    Remove-Item $tar -ErrorAction SilentlyContinue
}
