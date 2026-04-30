# Interception driver uninstaller
# Runs the install-interception.exe /uninstall command

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$InstallerPath = Join-Path $ScriptDir "interception\command line installer\install-interception.exe"

Write-Host "Uninstalling Interception driver..." -ForegroundColor Yellow
Write-Host ""

if (-not (Test-Path $InstallerPath)) {
    Write-Host "Error: install-interception.exe not found" -ForegroundColor Red
    Write-Host "Expected: $InstallerPath" -ForegroundColor Red
    exit 1
}

Write-Host "Running: $InstallerPath /uninstall" -ForegroundColor Cyan
$proc = Start-Process -FilePath $InstallerPath -ArgumentList "/uninstall" -Wait -PassThru -NoNewWindow

if ($proc.ExitCode -eq 0) {
    Write-Host ""
    Write-Host "================================================================" -ForegroundColor Green
    Write-Host "  Uninstall complete" -ForegroundColor Green
    Write-Host "  REBOOT REQUIRED" -ForegroundColor Green
    Write-Host "  Restart Windows to finish removing the driver" -ForegroundColor Green
    Write-Host "================================================================" -ForegroundColor Green
} else {
    Write-Host ""
    Write-Host "Uninstall exited with code: $($proc.ExitCode)" -ForegroundColor Red
    Write-Host "Check the output above for details" -ForegroundColor Red
}

Write-Host ""
Write-Host "After rebooting, you can delete:"
Write-Host "  - $ScriptDir\interception\"
Write-Host "  - $(Split-Path -Parent $ScriptDir)\interception.dll"
