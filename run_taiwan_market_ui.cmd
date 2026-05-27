@echo off
setlocal EnableExtensions

REM 2026/05/27 Steve Peng: Added Taiwan market Gradio UI launcher.
REM Change reason: users need a double-click GUI that displays Taiwan market reports directly.
REM Before: users could only use CLI/API or a text menu launcher.
REM After: this launcher installs missing requirements and starts a read-only local Gradio UI in the browser.
REM Safety: this launcher only starts app.ui.taiwan_market_app; it does not connect brokers, place orders, or run paper/live trading.

set "ROOT_DIR=%~dp0"
set "BACKEND_DIR=%ROOT_DIR%backend_api_python"

echo.
echo ============================================================
echo  QuantDinger Taiwan Market GUI
echo  Local URL: http://127.0.0.1:7860
echo  Disclaimer: Not investment advice. Evaluate risks yourself.
echo ============================================================
echo.

if not exist "%BACKEND_DIR%\app\ui\taiwan_market_app.py" (
  echo [ERROR] UI app not found:
  echo %BACKEND_DIR%\app\ui\taiwan_market_app.py
  echo.
  pause
  exit /b 1
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

echo [INFO] Starting Taiwan market GUI. Close this window to stop the UI.
echo [INFO] Browser should open automatically. If not, open http://127.0.0.1:7860
echo.
pushd "%BACKEND_DIR%" >nul
%PYTHON_CMD% -m app.ui.taiwan_market_app
set "RUN_CODE=%ERRORLEVEL%"
popd >nul

echo.
echo [INFO] Taiwan market GUI stopped. Exit code: %RUN_CODE%
pause
exit /b %RUN_CODE%

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
%PYTHON_CMD% -c "import flask, gradio" >nul 2>nul
if "%ERRORLEVEL%"=="0" (
  popd >nul
  exit /b 0
)
echo [INFO] Python dependencies are missing. Installing backend requirements...
%PYTHON_CMD% -m pip install -r requirements.txt
set "PIP_CODE=%ERRORLEVEL%"
popd >nul
exit /b %PIP_CODE%
