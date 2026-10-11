param(
    [string]$OutputDirectory = (Get-Location).Path
)

$ErrorActionPreference = "Stop"
$RepoUrl = "https://github.com/africansecurityobserver/african-security-observer.git"
$Stamp = Get-Date -Format "yyyy-MM-dd_HH-mm-ss"
$MirrorPath = Join-Path $OutputDirectory "african-security-observer-$Stamp.git"
$ZipPath = Join-Path $OutputDirectory "african-security-observer-$Stamp-git-backup.zip"

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "Git is required. Install Git for Windows, then retry."
}

New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
git clone --mirror $RepoUrl $MirrorPath
if ($LASTEXITCODE -ne 0) {
    throw "Git mirror clone failed; no backup archive was created."
}

Compress-Archive -Path $MirrorPath -DestinationPath $ZipPath -Force
Write-Host "Backup completed: $ZipPath"
Write-Host "Copy this archive to a location outside GitHub and test it."
