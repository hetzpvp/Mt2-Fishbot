@echo off
REM Interception driver uninstaller
REM Double-click this file to uninstall the Interception kernel driver.

setlocal enabledelayedexpansion
cd /d "%~dp0"

echo Checking for install-interception.exe...
if not exist "interception\command line installer\install-interception.exe" (
    echo Error: install-interception.exe not found
    echo Expected location: %cd%\interception\command line installer\install-interception.exe
    pause
    exit /b 1
)

echo Running uninstaller (requires admin)...
powershell -Command "Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile','-NoExit','-ExecutionPolicy','Bypass','-File','%~dp0uninstall_interception.ps1'"

pause
