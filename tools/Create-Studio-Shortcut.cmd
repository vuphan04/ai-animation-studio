@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Create-Studio-Shortcut.ps1"
if errorlevel 1 pause
