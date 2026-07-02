@echo off
setlocal
set "SD=%~dp0"
set "PD=%SD%"
set "CP=%SD%.tendata-chrome-profile"

echo ========================================
echo   TenData Stack - One Click Start
echo ========================================
echo.

rem === Find Chrome ===
set "CPATH="
if exist "C:\Program Files\Google\Chrome\Application\chrome.exe" set "CPATH=C:\Program Files\Google\Chrome\Application\chrome.exe"
if exist "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe" if "%CPATH%"=="" set "CPATH=C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
if exist "%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe" if "%CPATH%"=="" set "CPATH=%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"

if "%CPATH%"=="" (
    echo [ERROR] Chrome not found.
    pause
    exit /b 1
)
echo [OK] Chrome found

rem === Profile dir ===
if not exist "%CP%" mkdir "%CP%"

rem === Find Python (avoid Microsoft Store alias) ===
set "PYCMD="
py --version >nul 2>&1
if not errorlevel 1 (
    set "PYCMD=py"
    echo [OK] Python found (via py launcher)
) else (
    python --version >nul 2>&1
    if not errorlevel 1 (
        set "PYCMD=python"
        echo [OK] Python found
    ) else (
        echo [ERROR] No Python found. Install Python first.
        pause
        exit /b 1
    )
)

rem === Check ngrok ===
set "NGOK=0"
where ngrok >nul 2>&1
if not errorlevel 1 set "NGOK=1"

echo.
echo [1/4] Starting Chrome with debug port 9222...
start "" "%CPATH%" --remote-debugging-port=9222 --user-data-dir="%CP%" --no-first-run --no-default-browser-check https://bizr.tendata.cn/search#/index
timeout /t 2 /nobreak >nul
echo [OK] Chrome 9222 started

echo [2/4] Starting task_server...
start "TenData Task Server" /MIN %PYCMD% "%PD%scripts\task_server.py" --port 8080
timeout /t 2 /nobreak >nul
echo [OK] task_server started

echo [3/4] Starting queue_worker...
start "TenData Queue Worker" /MIN %PYCMD% "%PD%scripts\queue_worker.py"
timeout /t 2 /nobreak >nul
echo [OK] queue_worker started

echo [4/4] Starting ngrok...
if "%NGOK%"=="1" (
    start "ngrok" /MIN ngrok http 8080
    timeout /t 3 /nobreak >nul
    echo [OK] ngrok started
) else (
    echo [SKIP] ngrok not found, start it manually
)

echo.
echo ========================================
echo   All services started
echo ========================================
echo.
echo Chrome 9222    : running
echo task_server    : running (http://localhost:8080)
echo queue_worker   : running
echo ngrok          : check ngrok window for public URL
echo.
echo REMINDER:
echo   1. Confirm TenData is logged in in Chrome
echo   2. Note the ngrok public URL
echo.
pause
endlocal
