#requires -Version 7
<#
Builds the switch-remote sysmodule for one commit on the Yoga (devkitA64 + libnx from the unpacked devkitPro there),
booked in the build ledger, at nice 19 under a memory and time cap, and brings back the SD layout with sha256.

  /usr/bin/pwsh -NoLogo -NoProfile -NonInteractive -File tools/build_sysmodule.ps1 -Rev <commit> -Out <dir>

Out: <dir>/sd/atmosphere/contents/4200000000005352/{exefs.nsp,toolbox.json,flags/boot2.flag}, switch-remote.elf/.nso/.npdm,
build.log, SHA256SUMS.txt, build.json. Run from the Surface (ubuntu-surface); never builds locally.
#>
param(
    [Parameter(Mandatory)][string]$Rev,
    [Parameter(Mandatory)][string]$Out,
    [string]$Repo = (Split-Path $PSScriptRoot -Parent),
    [string]$BuildHost = 'device-2',
    [string]$DevkitPro = '/home/user/smash-replay-export-campaign/grok-nro/private/devkitpro/root/opt/devkitpro',
    [string]$Lane = 'switch-remote-sysmodule'
)
$ErrorActionPreference = 'Stop'
$ssh = @('-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15')
function Say($m) { Write-Host "[build_sysmodule $(Get-Date -AsUTC -Format 'HH:mm:ss')Z] $m" }
$full = git -C $Repo rev-parse $Rev
$short = $full.Substring(0, 8)
New-Item -ItemType Directory -Force $Out | Out-Null
$tar = Join-Path ([IO.Path]::GetTempPath()) "sr-sysmodule-$short.tar"
git -C $Repo archive --format=tar -o $tar $full sysmodule
$claim = & ssh @ssh $BuildHost "/usr/bin/python3 -I ~/build-hosts/bin/ledger.py claim --host yoga --owner switch-remote --note 'sysmodule $short'" 2>&1
if ($LASTEXITCODE -ne 0) { throw "ledger claim refused: $claim" }
# ledger.py claim prints the booking id alone on its last line (bid=<id> in older output).
$last = (@($claim) | Where-Object { $_ -and $_.Trim() } | Select-Object -Last 1).Trim()
$bid = if ($last -match '^[0-9a-f]{6,}$') { $last } else { ([regex]::Match(($claim -join ' '), 'bid=(\S+)')).Groups[1].Value }
if (-not $bid) { throw "no booking id in the claim output: $claim" }
Say "claimed bid=$bid"
try {
    $remote = "build-hosts/lanes/$Lane"
    & ssh @ssh $BuildHost "Remove-Item -Recurse -Force ~/$remote -ErrorAction SilentlyContinue; New-Item -ItemType Directory -Force ~/$remote | Out-Null"
    & scp -q @ssh $tar "${BuildHost}:$remote/src.tar"
    $script = @"
Set-Location ~/$remote
tar -xmf src.tar
Set-Location sysmodule
`$env:DEVKITPRO = '$DevkitPro'
systemd-run --user --scope -q -p MemoryMax=2G /usr/bin/timeout 900 nice -n 19 ionice -c 3 make -j2 all *> ../build.log
`$code = `$LASTEXITCODE
"exit=`$code" | Add-Content ../build.log
exit `$code
"@
    $b64 = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($script))
    & ssh @ssh $BuildHost "/usr/bin/pwsh -NoLogo -NoProfile -NonInteractive -EncodedCommand $b64"
    $code = $LASTEXITCODE
    & scp -q @ssh "${BuildHost}:$remote/build.log" (Join-Path $Out 'build.log')
    if ($code -ne 0) { throw "make failed (exit $code); see $Out/build.log" }
    & scp -q -r @ssh "${BuildHost}:$remote/sysmodule/out/sd" (Join-Path $Out 'sd')
    foreach ($f in 'switch-remote.elf', 'switch-remote.nso', 'switch-remote.npdm', 'switch-remote.map') {
        & scp -q @ssh "${BuildHost}:$remote/sysmodule/out/$f" (Join-Path $Out $f)
    }
    $sums = Get-ChildItem $Out -Recurse -File | Where-Object Name -ne 'SHA256SUMS.txt' | ForEach-Object {
        "$((Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLower())  $([IO.Path]::GetRelativePath($Out, $_.FullName))"
    }
    $sums | Set-Content (Join-Path $Out 'SHA256SUMS.txt')
    $nsp = Get-Item (Join-Path $Out 'sd/atmosphere/contents/4200000000005352/exefs.nsp')
    [ordered]@{ rev = $full; host = $BuildHost; devkitpro = $DevkitPro; nsp_bytes = $nsp.Length
        nsp_sha256 = (Get-FileHash $nsp.FullName -Algorithm SHA256).Hash.ToLower(); time = (Get-Date -AsUTC -Format o) } |
        ConvertTo-Json | Set-Content (Join-Path $Out 'build.json')
    Say "ok: exefs.nsp $($nsp.Length) B"
} finally {
    & ssh @ssh $BuildHost "/usr/bin/python3 -I ~/build-hosts/bin/ledger.py release --host yoga --owner switch-remote --bid $bid --note 'sysmodule $short done'" | Out-Null
    & ssh @ssh $BuildHost "Remove-Item -Recurse -Force ~/build-hosts/lanes/$Lane -ErrorAction SilentlyContinue"
    Remove-Item $tar -ErrorAction SilentlyContinue
    Say "released bid=$bid, lane cleaned"
}
