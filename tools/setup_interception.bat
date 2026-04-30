@echo off
REM Interception driver setup launcher
REM Double-click this file to install the Interception kernel driver.
REM It will open an elevated PowerShell and run setup_interception.ps1.

setlocal enabledelayedexpansion
cd /d "%~dp0"

powershell -Command "Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile','-NoExit','-ExecutionPolicy','Bypass','-File','%~dp0setup_interception.ps1'"

pause
