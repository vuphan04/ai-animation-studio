@echo off
setlocal
chcp 65001 >nul
title AI Animation Studio
rem File này nằm trong tools\, thư mục app là thư mục cha. Launcher tự tạo shortcut Start-Studio (icon capybara)
rem ở thư mục app để nhấp đúp từ lần sau.
for %%I in ("%~dp0..") do set "STUDIO_ROOT=%%~fI\"
set "STUDIO_PYTHON=%STUDIO_ROOT%.venv\Scripts\python.exe"
pushd "%STUDIO_ROOT%"
if errorlevel 1 goto :folder_error

if not exist "%STUDIO_PYTHON%" (
  echo [1/3] Đang chuẩn bị môi trường Python...
  call :make_venv
  if errorlevel 1 goto :python_missing
)

"%STUDIO_PYTHON%" -c "import fastapi, uvicorn, httpx, keyring, win32api, anthropic" >nul 2>&1
if errorlevel 1 (
  echo [2/3] Đang cài thư viện backend. Lần đầu cần kết nối Internet...
  "%STUDIO_PYTHON%" -m pip install -r "%STUDIO_ROOT%services\api\requirements.lock.txt"
  if errorlevel 1 goto :backend_error
)

"%STUDIO_PYTHON%" "%STUDIO_ROOT%tools\prepare_web.py"
if errorlevel 1 goto :frontend_error

if /I "%~1"=="--check" (
  echo Sẵn sàng khởi chạy AI Animation Studio.
  popd
  exit /b 0
)

rem Cửa sổ launcher (có log trực tiếp) chạy bằng pythonw nên cửa sổ console này tự đóng.
rem Thiếu tkinter hoặc gọi kèm --console thì chạy trong console như trước.
if /I "%~1"=="--console" goto :console
"%STUDIO_PYTHON%" -c "import tkinter" >nul 2>&1
if errorlevel 1 goto :console
if not exist "%STUDIO_ROOT%.venv\Scripts\pythonw.exe" goto :console
start "" "%STUDIO_ROOT%.venv\Scripts\pythonw.exe" "%STUDIO_ROOT%tools\launch_studio.py" --window %*
popd
exit /b 0

:console
"%STUDIO_PYTHON%" "%STUDIO_ROOT%tools\launch_studio.py" %*
set "STUDIO_EXIT=%ERRORLEVEL%"
popd
if not "%STUDIO_EXIT%"=="0" pause
exit /b %STUDIO_EXIT%

:make_venv
rem Python từ python.org có lệnh py; bản cài kiểu khác có thể chỉ có lệnh python.
where py >nul 2>&1
if not errorlevel 1 (
  py -3.14 -m venv "%STUDIO_ROOT%.venv" 2>nul
  if not errorlevel 1 exit /b 0
)
where python >nul 2>&1
if errorlevel 1 exit /b 1
python -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 14) else 1)" >nul 2>&1
if errorlevel 1 exit /b 1
python -m venv "%STUDIO_ROOT%.venv"
exit /b %ERRORLEVEL%

:offer_install
rem %1 = tên hiển thị, %2 = mã gói winget. Chỉ cài khi người dùng đồng ý.
where winget >nul 2>&1
if errorlevel 1 exit /b 1
choice /C CK /N /M "Cài %~1 bằng winget ngay bây giờ? Bấm C = Có, K = Không: "
if errorlevel 2 exit /b 1
winget install -e --id %2 --accept-source-agreements --accept-package-agreements
if errorlevel 1 (
  echo Winget không cài được %~1.
  exit /b 1
)
echo.
echo Đã cài %~1. Đóng cửa sổ này rồi mở lại Start-Studio để tiếp tục.
exit /b 0

:python_missing
echo Chưa có Python 3.14 trên máy này, hoặc chưa tạo được môi trường Python.
call :offer_install "Python 3.14" Python.Python.3.14
if errorlevel 1 echo Cài Python 3.14 từ https://www.python.org/downloads/ rồi mở lại Start-Studio.
goto :failed

:backend_error
echo Không cài được thư viện backend. Kiểm tra kết nối mạng và lỗi phía trên rồi mở lại file.
goto :failed

:frontend_error
where npm >nul 2>&1
if errorlevel 1 (
  echo Chưa có Node.js trên máy này.
  call :offer_install "Node.js 24 LTS" OpenJS.NodeJS.LTS
  if errorlevel 1 echo Cài Node.js 24 từ https://nodejs.org/ rồi mở lại Start-Studio.
  goto :failed
)
echo Không chuẩn bị được giao diện. Kiểm tra kết nối mạng, phiên bản Node.js và lỗi phía trên.
goto :failed

:failed
popd
pause
exit /b 1

:folder_error
echo Không truy cập được thư mục dự án. Hãy giải nén toàn bộ repository trước khi mở file.
pause
exit /b 1
