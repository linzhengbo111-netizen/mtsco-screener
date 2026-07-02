@echo off
setlocal
set SCRIPT_DIR=%~dp0
set CHROME_PROFILE=%SCRIPT_DIR%.tendata-chrome-profile

echo ========================================
echo   TenData Helper - Chrome Launcher
echo ========================================
echo.

rem Create profile directory if it does not exist
if not exist "%CHROME_PROFILE%" (
    echo Creating browser profile directory...
    mkdir "%CHROME_PROFILE%"
)

rem Find Chrome executable
set CHROME_PATH=
if exist "C:\Program Files\Google\Chrome\Application\chrome.exe" (
    set "CHROME_PATH=C:\Program Files\Google\Chrome\Application\chrome.exe"
) else if exist "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe" (
    set "CHROME_PATH=C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
) else if exist "%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe" (
    set "CHROME_PATH=%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"
)

if "%CHROME_PATH%"=="" (
    echo [ERROR] Google Chrome not found.
    echo Please install Google Chrome and try again.
    echo.
    pause
    exit /b 1
)

echo Launching TenData Helper window...
echo.

rem Launch Chrome with fixed debug port and our profile directory
start "" "%CHROME_PATH%" --remote-debugging-port=9222 --user-data-dir="%CHROME_PROFILE%" --no-first-run --no-default-browser-check https://bizr.tendata.cn/search#/index

echo [OK] TenData Helper window opened.
echo.
echo IMPORTANT:
echo   1. In the TenData Helper window, log in to your account.
echo   2. After login, keep the window open.
echo   3. Run the batch script: run_tendata_batch.bat
echo.
pause
endlocal
