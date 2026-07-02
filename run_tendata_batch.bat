@echo off
echo ========================================
echo   TenData - Batch Runner
echo ========================================
echo.

rem Check if TenData Helper is running
netstat -ano | findstr ":9222" | findstr "LISTENING" >nul 2>&1
if not %errorlevel%==0 (
    echo [ERROR] TenData Helper is not running
    echo.
    echo Please start TenData Helper first:
    echo   1. Double-click start_tendata_helper.bat
    echo   2. Log in to TenData in the Helper window
    echo   3. Then run this script again
    echo.
    pause
    exit /b 1
)

echo [OK] TenData Helper is running
echo.

rem Check if input file is provided
if "%~1"=="" (
    echo [ERROR] No input file specified
    echo.
    echo Usage: run_tendata_batch.bat ^<customer_list.xlsx^>
    echo.
    echo Example:
    echo   run_tendata_batch.bat sample_input.xlsx
    echo   run_tendata_batch.bat customer_list.xlsx --output result.xlsx
    echo   run_tendata_batch.bat customer_list.xlsx --headless
    echo.
    pause
    exit /b 1
)

echo Starting batch processing...
echo.

rem Run the Python batch script, passing all arguments through
python "%~dp0scripts\run_batch.py" --input "%~1" %2 %3 %4 %5 %6 %7 %8 %9

echo.
pause
