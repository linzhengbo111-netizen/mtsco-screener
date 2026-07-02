@echo off
setlocal
set SCRIPT_DIR=%~dp0
cd /d "%SCRIPT_DIR%"

echo ========================================
echo   TenData Customer Enricher - Services
echo ========================================
echo.

rem Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Please install Python 3.9+
    pause
    exit /b 1
)

rem Start task_server
echo [1/2] Starting task_server on port 8080...
start "TenData Task Server" /MIN python scripts\task_server.py --port 8080
timeout /t 2 /nobreak >nul

rem Start queue_worker
echo [2/2] Starting queue_worker...
start "TenData Queue Worker" /MIN python scripts\queue_worker.py
timeout /t 2 /nobreak >nul

rem Health check
echo.
echo Health check:
curl -s http://localhost:8080/api/health 2>nul
if errorlevel 1 (
    echo [WARN] Health check failed. Check if task_server started.
) else (
    echo [OK]
)

echo.
echo Services started. Check taskbar for two windows:
echo   - TenData Task Server
echo   - TenData Queue Worker
echo.
echo API Endpoints:
echo   POST http://localhost:8080/api/task/create  - Submit task
echo   GET  http://localhost:8080/api/task/status  - Query status
echo   GET  http://localhost:8080/api/task/result  - Get results
echo   GET  http://localhost:8080/api/health       - Health check
echo.
pause
