# start_tendata_stack.ps1
# TenData Stack - One Click Start (PowerShell)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$ChromeProfile = Join-Path $ProjectDir ".tendata-chrome-profile"

Write-Host "========================================"
Write-Host "  TenData Stack - One Click Start" -ForegroundColor Cyan
Write-Host "========================================"
Write-Host ""

# ---- Find Chrome ----
$ChromePaths = @(
    "C:\Program Files\Google\Chrome\Application\chrome.exe",
    "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
)
$ChromePath = $null
foreach ($p in $ChromePaths) {
    if (Test-Path $p) { $ChromePath = $p; break }
}

if (-not $ChromePath) {
    Write-Host "[ERROR] Google Chrome not found." -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

# ---- Create profile dir if needed ----
if (-not (Test-Path $ChromeProfile)) {
    New-Item -ItemType Directory -Path $ChromeProfile | Out-Null
}

# ---- Check Python ----
$null = Get-Command python -ErrorAction SilentlyContinue
if (-not $?) {
    Write-Host "[ERROR] Python not found." -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

# ---- Check ngrok ----
$HasNgrok = $null -ne (Get-Command ngrok -ErrorAction SilentlyContinue)

# ---- 1/4 Chrome ----
Write-Host "[1/4] Starting Chrome with remote debugging port 9222..."
Start-Process $ChromePath -ArgumentList @(
    "--remote-debugging-port=9222",
    "--user-data-dir=`"$ChromeProfile`"",
    "--no-first-run",
    "--no-default-browser-check",
    "https://bizr.tendata.cn/search#/index"
)
Start-Sleep -Seconds 2
Write-Host "[OK] Chrome 9222 已启动" -ForegroundColor Green

# ---- 2/4 task_server ----
Write-Host "[2/4] Starting task_server on port 8080..."
Start-Process powershell -ArgumentList @(
    "-NoExit",
    "-Command",
    "cd '$ProjectDir'; python scripts\task_server.py --port 8080"
) -WindowStyle Minimized
Start-Sleep -Seconds 2
Write-Host "[OK] task_server 已启动" -ForegroundColor Green

# ---- 3/4 queue_worker ----
Write-Host "[3/4] Starting queue_worker..."
Start-Process powershell -ArgumentList @(
    "-NoExit",
    "-Command",
    "cd '$ProjectDir'; python scripts\queue_worker.py"
) -WindowStyle Minimized
Start-Sleep -Seconds 2
Write-Host "[OK] queue_worker 已启动" -ForegroundColor Green

# ---- 4/4 ngrok ----
Write-Host "[4/4] Starting ngrok..."
if ($HasNgrok) {
    Start-Process ngrok -ArgumentList @("http", "8080") -WindowStyle Minimized
    Start-Sleep -Seconds 3
    Write-Host "[OK] ngrok 已启动" -ForegroundColor Green
} else {
    Write-Host "[SKIP] ngrok 未找到，请手动启动" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  全部服务已启动" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Chrome 9222 : 已启动" -ForegroundColor Green
Write-Host "task_server  : 已启动 (http://localhost:8080)" -ForegroundColor Green
Write-Host "queue_worker : 已启动" -ForegroundColor Green
Write-Host "ngrok        : 已启动 (请确认 ngrok 窗口中的公网地址)" -ForegroundColor Green
Write-Host ""
Write-Host "提醒：请确认以下事项" -ForegroundColor Yellow
Write-Host "  1. Chrome 窗口中腾道已登录" -ForegroundColor Yellow
Write-Host "  2. 记录 ngrok 窗口中显示的公网地址" -ForegroundColor Yellow
Write-Host ""
