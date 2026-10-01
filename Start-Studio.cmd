@echo off
setlocal
chcp 65001 >nul
title AI Animation Studio
set "STUDIO_ROOT=%~dp0"
set "STUDIO_PYTHON=%STUDIO_ROOT%.venv\Scripts\python.exe"
pushd "%STUDIO_ROOT%"
if errorlevel 1 goto :folder_error

if not exist "%STUDIO_PYTHON%" (
  echo [1/3] Đang chuẩn bị môi trường Python...
  where py >nul 2>&1
  if errorlevel 1 goto :python_missing
  py -3.14 -m venv "%STUDIO_ROOT%.venv"
  if errorlevel 1 goto :python_missing
)

"%STUDIO_PYTHON%" -c "import fastapi, uvicorn, httpx, keyring, win32api" >nul 2>&1
if errorlevel 1 (
  echo [2/3] Đang cài thư viện backend. Lần đầu cần kết nối Internet...
  "%STUDIO_PYTHON%" -m pip install -r "%STUDIO_ROOT%services\api\requirements.lock.txt"
  if errorlevel 1 goto :backend_error
)

if not exist "%STUDIO_ROOT%apps\web\dist\index.html" (
  echo [3/3] Đang chuẩn bị giao diện. Lần đầu cần kết nối Internet...
  where npm.cmd >nul 2>&1
  if errorlevel 1 goto :node_missing
  call npm.cmd ci --prefix "%STUDIO_ROOT%apps\web"
  if errorlevel 1 goto :frontend_error
  call npm.cmd run build --prefix "%STUDIO_ROOT%apps\web"
  if errorlevel 1 goto :frontend_error
)

if /I "%~1"=="--check" (
  echo Sẵn sàng khởi chạy AI Animation Studio.
  popd
  exit /b 0
)

"%STUDIO_PYTHON%" "%STUDIO_ROOT%tools\launch_studio.py" %*
set "STUDIO_EXIT=%ERRORLEVEL%"
popd
if not "%STUDIO_EXIT%"=="0" pause
exit /b %STUDIO_EXIT%

:python_missing
echo Chưa có Python 3.14 hoặc chưa tạo được môi trường Python.
echo Cài Python 3.14 từ https://www.python.org/downloads/ rồi mở lại Start-Studio.cmd.
goto :failed

:node_missing
echo Chưa tìm thấy Node.js/npm. Cài Node.js 24 từ https://nodejs.org/ rồi mở lại Start-Studio.cmd.
goto :failed

:backend_error
echo Không cài được thư viện backend. Kiểm tra kết nối mạng và lỗi phía trên rồi mở lại file.
goto :failed

:frontend_error
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
