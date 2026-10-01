@echo off
setlocal
for %%I in ("%~dp0..") do set "STUDIO_ROOT=%%~fI\"
call "%STUDIO_ROOT%Start-Studio.cmd" %*
exit /b %ERRORLEVEL%
