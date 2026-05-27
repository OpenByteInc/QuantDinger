@echo off
setlocal EnableExtensions EnableDelayedExpansion

REM 2026/05/27 Steve Peng: Added a double-click Windows launcher.
REM Change reason: users need an executable launcher for Taiwan market reports.
REM Before: reports could only be generated from the command line.
REM After: this launcher generates read-only pre-market, post-market, or backtest JSON reports.
REM Safety: this file only calls the information-report CLI; it does not connect brokers, place orders, or run paper/live trading.

set "ROOT_DIR=%~dp0"
set "BACKEND_DIR=%ROOT_DIR%backend_api_python"
set "OUT_DIR=%ROOT_DIR%reports\taiwan-market"
set "SCRIPT=%BACKEND_DIR%\scripts\generate_taiwan_market_report.py"

echo.
echo ============================================================
echo  QuantDinger Taiwan Market Report Launcher
echo  Disclaimer: Not investment advice. Evaluate risks yourself.
echo ============================================================
echo.

if not exist "%SCRIPT%" (
  echo [ERROR] Report script not found:
  echo %SCRIPT%
  echo.
  pause
  exit /b 1
)

if not exist "%OUT_DIR%" (
  mkdir "%OUT_DIR%" >nul 2>nul
)

call :FindPython
if errorlevel 1 (
  echo [ERROR] Python 3 was not found. Install Python 3 and enable Add to PATH.
  echo.
  pause
  exit /b 1
)

call :EnsureRuntime
if errorlevel 1 (
  echo.
  echo [ERROR] Failed to install backend Python requirements.
  echo Check network access, Python, and pip.
  echo.
  pause
  exit /b 1
)

echo Select report type:
echo   1. Pre-market report
echo   2. Post-market report
echo   3. Candidate backtest summary
echo.
set /p "CHOICE=Enter 1, 2, or 3, then press Enter: "

REM Use PowerShell for a locale-independent ASCII timestamp.
REM This avoids Chinese weekday text from Windows %DATE% entering the output file name.
for /f %%I in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "STAMP=%%I"

pushd "%BACKEND_DIR%" >nul

if "%CHOICE%"=="1" (
  set "OUTPUT=%OUT_DIR%\taiwan_pre_market_%STAMP%.json"
  %PYTHON_CMD% scripts\generate_taiwan_market_report.py --session pre_market --provider mock --top 20 --output "!OUTPUT!"
) else if "%CHOICE%"=="2" (
  set "OUTPUT=%OUT_DIR%\taiwan_post_market_%STAMP%.json"
  %PYTHON_CMD% scripts\generate_taiwan_market_report.py --session post_market --provider mock --top 20 --output "!OUTPUT!"
) else if "%CHOICE%"=="3" (
  set "OUTPUT=%OUT_DIR%\taiwan_backtest_%STAMP%.json"
  %PYTHON_CMD% scripts\generate_taiwan_market_report.py --backtest --provider mock --days 60 --top 20 --output "!OUTPUT!"
) else (
  popd >nul
  echo.
  echo [ERROR] Invalid option. Run again and enter 1, 2, or 3.
  echo.
  pause
  exit /b 1
)

set "RUN_CODE=%ERRORLEVEL%"
popd >nul

if not "%RUN_CODE%"=="0" (
  echo.
  echo [ERROR] Report generation failed. Exit code: %RUN_CODE%
  echo.
  pause
  exit /b %RUN_CODE%
)

echo.
echo [OK] Report generated:
echo !OUTPUT!
echo.
echo Reminder: this report is information-only. Any real trading must be done manually in your broker system.
echo.
pause
exit /b 0

:FindPython
set "PYTHON_CMD="
where py >nul 2>nul
if "%ERRORLEVEL%"=="0" (
  set "PYTHON_CMD=py -3"
  exit /b 0
)
where python >nul 2>nul
if "%ERRORLEVEL%"=="0" (
  set "PYTHON_CMD=python"
  exit /b 0
)
exit /b 1

:EnsureRuntime
pushd "%BACKEND_DIR%" >nul
%PYTHON_CMD% -c "import flask" >nul 2>nul
if "%ERRORLEVEL%"=="0" (
  popd >nul
  exit /b 0
)
echo [INFO] Python dependencies are missing. Installing backend requirements...
%PYTHON_CMD% -m pip install -r requirements.txt
set "PIP_CODE=%ERRORLEVEL%"
popd >nul
exit /b %PIP_CODE%
