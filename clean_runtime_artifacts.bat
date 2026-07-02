@echo off
setlocal
set SCRIPT_DIR=%~dp0

echo Cleaning runtime artifacts...
echo.

python "%SCRIPT_DIR%scripts\clean_runtime_artifacts.py"

echo.
pause
endlocal
