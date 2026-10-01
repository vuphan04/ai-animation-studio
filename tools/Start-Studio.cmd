@echo off
setlocal
for %%I in ("%~dp0..") do set "STUDIO_ROOT=%%~fI\"
set "STUDIO_PYTHON=%STUDIO_ROOT%.venv\Scripts\python.exe"
if not exist "%STUDIO_PYTHON%" (
  echo AI Animation Studio needs one-time setup. See README.md.
  pause
  exit /b 1
)
"%STUDIO_PYTHON%" "%STUDIO_ROOT%tools\launch_studio.py" %*
if errorlevel 1 pause
