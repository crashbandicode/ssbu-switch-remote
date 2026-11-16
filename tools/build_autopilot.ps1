#requires -Version 7
<#
Builds the sys-autopilot FALLBACK driver from its pinned source on the Yoga (internal tool use, not redistributed;
primary ruling 2026-11-16). The source is the read-only clone /home/user/ref/sys-autopilot at the pinned commit.

  /usr/bin/pwsh -NoLogo -NoProfile -NonInteractive -File tools/build_autopilot.ps1 -Out <dir>

Out: sys-autopilot.nsp, build.log, build.json (commit, sha256). Booked in the build ledger; lane cleaned after.
#>
param(
    [Parameter(Mandatory)][string]$Out,
    [string]$Source = '/home/user/ref/sys-autopilot',
    [string]$Commit = '6ca6aac',
    [string]$BuildHost = 'device-2',
    [string]$DevkitPro = '/home/user/smash-replay-export-campaign/grok-nro/private/devkitpro/root/opt/devkitpro',
    [string]$Lane = 'switch-remote-autopilot'
)
$ErrorActionPreference = 'Stop'
$ssh = @('-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15')
$full = git -C $Source rev-parse $Commit
New-Item -ItemType Directory -Force $Out | Out-Null
$tar = Join-Path ([IO.Path]::GetTempPath()) "sr-autopilot-$($full.Substring(0, 8)).tar"
git -C $Source archive --format=tar -o $tar $full
$claim = & ssh @ssh $BuildHost "/usr/bin/python3 -I ~/build-hosts/bin/ledger.py claim --host yoga --owner switch-remote --note 'sys-autopilot $($full.Substring(0, 8)) fallback build'" 2>&1
if ($LASTEXITCODE -ne 0) { throw "ledger claim refused: $claim" }
$bid = (@($claim) | Where-Object { $_ -and $_.Trim() } | Select-Object -Last 1).Trim()
try {
    $remote = "build-hosts/lanes/$Lane"
    & ssh @ssh $BuildHost "Remove-Item -Recurse -Force ~/$remote -ErrorAction SilentlyContinue; New-Item -ItemType Directory -Force ~/$remote/src | Out-Null"
    & scp -q @ssh $tar "${BuildHost}:$remote/src.tar"
    $script = @"
Set-Location ~/$remote/src
tar -xmf ../src.tar
`$env:DEVKITPRO = '$DevkitPro'
`$env:PATH = '$DevkitPro/tools/bin:$DevkitPro/devkitA64/bin:' + `$env:PATH
systemd-run --user --scope -q -p MemoryMax=2G /usr/bin/timeout 900 nice -n 19 ionice -c 3 make -j2 *> ../build.log
`$code = `$LASTEXITCODE
"exit=`$code" | Add-Content ../build.log
exit `$code
"@
    $b64 = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($script))
    & ssh @ssh $BuildHost "/usr/bin/pwsh -NoLogo -NoProfile -NonInteractive -EncodedCommand $b64"
    $code = $LASTEXITCODE
    & scp -q @ssh "${BuildHost}:$remote/build.log" (Join-Path $Out 'build.log')
    if ($code -ne 0) { throw "make failed (exit $code); see $Out/build.log" }
    & scp -q @ssh "${BuildHost}:$remote/src/sys-autopilot.nsp" (Join-Path $Out 'sys-autopilot.nsp')
    $nsp = Get-Item (Join-Path $Out 'sys-autopilot.nsp')
    [ordered]@{ commit = $full; host = $BuildHost; nsp_bytes = $nsp.Length
        nsp_sha256 = (Get-FileHash $nsp.FullName -Algorithm SHA256).Hash.ToLower(); time = (Get-Date -AsUTC -Format o)
        license = 'none: no LICENSE file; package.json says MIT; GitHub license null. Internal tool, not redistributed.' } |
        ConvertTo-Json | Set-Content (Join-Path $Out 'build.json')
    "ok: sys-autopilot.nsp $($nsp.Length) B"
} finally {
    & ssh @ssh $BuildHost "/usr/bin/python3 -I ~/build-hosts/bin/ledger.py release --host yoga --owner switch-remote --bid $bid --note 'sys-autopilot fallback build done'" | Out-Null
    & ssh @ssh $BuildHost "Remove-Item -Recurse -Force ~/build-hosts/lanes/$Lane -ErrorAction SilentlyContinue"
    Remove-Item $tar -ErrorAction SilentlyContinue
}
