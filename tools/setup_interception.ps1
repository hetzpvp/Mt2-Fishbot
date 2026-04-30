# Interception driver installer for the fishing bot.
#
# What this does:
#   1. Downloads Interception 1.0.1 from the official GitHub release.
#   2. Verifies the SHA256 of the archive against the value baked in below.
#   3. Extracts to <repo>\tools\interception\.
#   4. Copies the matching interception.dll (x64 by default, x86 with -X86)
#      next to the executable so ctypes can find it without PATH changes.
#   5. Runs install-interception.exe /install (requires Administrator).
#   6. Reminds the user a reboot is required before the driver loads.
#
# What this does NOT do:
#   - Uninstall any previous version. If you have an old install, run
#     `install-interception.exe /uninstall` first then re-run this script.
#   - Modify your PATH. The DLL is copied to a location the bot already searches.
#
# Usage:
#   Right-click -> Run with PowerShell (as Administrator).
#   Or from an elevated PowerShell:  .\tools\setup_interception.ps1
#   For 32-bit Python:              .\tools\setup_interception.ps1 -X86

[CmdletBinding()]
param(
    [switch]$X86,
    [switch]$SkipInstall  # Download/extract only - useful for CI or unattended setups
)

$ErrorActionPreference = "Stop"

# --- Configuration -------------------------------------------------------

$ReleaseUrl = "https://github.com/oblitum/Interception/releases/download/v1.0.1/Interception.zip"

# SHA256 of Interception.zip (v1.0.1). Set this to the hash of the file at
# $ReleaseUrl the first time you verify it locally:
#     (Get-FileHash -Algorithm SHA256 .\Interception.zip).Hash
# Then commit the value. Empty = skip verification (NOT recommended; only
# use for the very first run on a trusted network).
$ExpectedSha256 = ""
$RepoRoot       = Split-Path -Parent $PSScriptRoot
$ToolsDir       = Join-Path $RepoRoot "tools\interception"
$ZipPath        = Join-Path $env:TEMP "Interception.zip"

if ($X86) { $Arch = "x86" } else { $Arch = "x64" }

# --- Helpers -------------------------------------------------------------

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    $msg" -ForegroundColor Green }
function Write-Warn2($msg){ Write-Host "    $msg" -ForegroundColor Yellow }
function Write-Err($msg)  { Write-Host "    $msg" -ForegroundColor Red }

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p  = New-Object Security.Principal.WindowsPrincipal($id)
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# --- 0. Admin check ------------------------------------------------------

if (-not $SkipInstall -and -not (Test-Admin)) {
    Write-Err "This script must run as Administrator to install the kernel driver."
    Write-Err "Right-click the script -> Run with PowerShell (Administrator)."
    exit 1
}

Write-Step "Target architecture: $Arch"
Write-Step "Repo root: $RepoRoot"

# --- 1. Download ---------------------------------------------------------

Write-Step "Downloading Interception 1.0.1..."
try {
    Invoke-WebRequest -Uri $ReleaseUrl -OutFile $ZipPath -UseBasicParsing
    Write-Ok "Saved to $ZipPath"
} catch {
    Write-Err "Download failed: $_"
    exit 1
}

# --- 2. Verify SHA256 ----------------------------------------------------

$actual = (Get-FileHash -Algorithm SHA256 -Path $ZipPath).Hash
if ([string]::IsNullOrWhiteSpace($ExpectedSha256)) {
    Write-Warn2 "SHA256 verification skipped (\$ExpectedSha256 is empty)."
    Write-Warn2 "  computed hash: $actual"
    Write-Warn2 "  pin this value in the script to enable verification next run."
} else {
    Write-Step "Verifying SHA256..."
    if ($actual -ne $ExpectedSha256) {
        Write-Err "SHA256 mismatch."
        Write-Err "  expected: $ExpectedSha256"
        Write-Err "  actual:   $actual"
        Write-Err "Refusing to install. Delete $ZipPath and re-run, or update the script."
        exit 1
    }
    Write-Ok "Hash matches."
}

# --- 3. Extract ----------------------------------------------------------

Write-Step "Extracting to $ToolsDir ..."
if (Test-Path $ToolsDir) {
    Remove-Item -Recurse -Force $ToolsDir
}
New-Item -ItemType Directory -Force -Path $ToolsDir | Out-Null
Expand-Archive -Path $ZipPath -DestinationPath $ToolsDir -Force
Write-Ok "Extraction complete."

# The release ships as Interception\... - flatten one level so paths are
# tools\interception\library\<arch>\interception.dll regardless of zip layout.
$nested = Join-Path $ToolsDir "Interception"
if (Test-Path $nested) {
    Get-ChildItem -Path $nested | ForEach-Object {
        Move-Item -Path $_.FullName -Destination $ToolsDir -Force
    }
    Remove-Item -Recurse -Force $nested
}

# --- 4. Copy DLL ---------------------------------------------------------

$dllSrc = Join-Path $ToolsDir "library\$Arch\interception.dll"
if (-not (Test-Path $dllSrc)) {
    Write-Err "Expected DLL not found at $dllSrc"
    Write-Err "The archive layout may have changed; please open an issue."
    exit 1
}

$dllDst = Join-Path $RepoRoot "interception.dll"
Copy-Item -Path $dllSrc -Destination $dllDst -Force
Write-Ok "Copied $Arch interception.dll -> $dllDst"

# --- 5. Install driver ---------------------------------------------------

if ($SkipInstall) {
    Write-Warn2 "-SkipInstall set; not running install-interception.exe."
    Write-Warn2 "Run it manually from an elevated prompt:"
    Write-Warn2 "  $ToolsDir\command line installer\install-interception.exe /install"
    exit 0
}

$installer = Join-Path $ToolsDir "command line installer\install-interception.exe"
if (-not (Test-Path $installer)) {
    Write-Err "install-interception.exe not found at $installer"
    exit 1
}

Write-Step "Installing kernel driver (this is the part that needs admin)..."
$proc = Start-Process -FilePath $installer -ArgumentList "/install" -Wait -PassThru -NoNewWindow
if ($proc.ExitCode -ne 0) {
    Write-Err "Installer exited with code $($proc.ExitCode)."
    Write-Err "Common causes: another version already installed, or admin rights missing."
    exit $proc.ExitCode
}
Write-Ok "Driver installed."

# --- 6. Reboot reminder --------------------------------------------------

Write-Host ""
Write-Host "================================================================"   -ForegroundColor Yellow
Write-Host "  REBOOT REQUIRED"                                                  -ForegroundColor Yellow
Write-Host "  The Interception driver loads at boot. Restart Windows now,"      -ForegroundColor Yellow
Write-Host "  then start the bot - the header should show INPUT: INTERCEPTION." -ForegroundColor Yellow
Write-Host "================================================================"   -ForegroundColor Yellow
