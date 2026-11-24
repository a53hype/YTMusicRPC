@echo off
setlocal
cd /d "%~dp0"

REM Double-clicking this file starts a hidden worker, then closes this console.
if /i not "%~1"=="--worker" (
    wscript.exe //B //NoLogo "%~dp0launch_hidden.vbs"
    exit /b 0
)

REM Keep a diagnostic log even though the worker console is hidden.
if defined LOCALAPPDATA (
    set "DATA_DIR=%LOCALAPPDATA%\YTMDiscordRPC"
) else (
    set "DATA_DIR=%USERPROFILE%\.YTMDiscordRPC"
)
if not exist "%DATA_DIR%" mkdir "%DATA_DIR%" >nul 2>&1
set "LAUNCH_LOG=%DATA_DIR%\launcher.log"

>>"%LAUNCH_LOG%" echo ============================================================
>>"%LAUNCH_LOG%" echo [%DATE% %TIME%] Starting YouTube Music Discord RPC
>>"%LAUNCH_LOG%" echo Project folder: %CD%

REM Prefer the Python Launcher, but support installations that expose only python.exe.
where py >nul 2>nul
if not errorlevel 1 (
    set "PYTHON_CMD=py -3"
) else (
    where python >nul 2>nul
    if errorlevel 1 goto python_missing
    set "PYTHON_CMD=python"
)

%PYTHON_CMD% -c "import sys; print('Python:', sys.version); raise SystemExit(0 if sys.version_info >= (3, 9) else 1)" >>"%LAUNCH_LOG%" 2>&1
if errorlevel 1 goto python_version_error

>>"%LAUNCH_LOG%" echo [%DATE% %TIME%] Installing/checking dependencies
%PYTHON_CMD% -m pip install --disable-pip-version-check --user --quiet -r requirements.txt >>"%LAUNCH_LOG%" 2>&1
if errorlevel 1 goto dependency_error

>>"%LAUNCH_LOG%" echo [%DATE% %TIME%] Launching app.py
REM Run the GUI from this already-hidden worker instead of relying on pyw.exe.
REM This avoids pyw launcher/path issues and captures startup tracebacks in the log.
%PYTHON_CMD% -u app.py %~2 >>"%LAUNCH_LOG%" 2>&1
set "APP_EXIT=%ERRORLEVEL%"
if not "%APP_EXIT%"=="0" goto app_error
>>"%LAUNCH_LOG%" echo [%DATE% %TIME%] App closed normally.
exit /b 0

:python_missing
>>"%LAUNCH_LOG%" echo ERROR: Python 3.9 or newer was not found (neither py nor python is on PATH).
start "" notepad.exe "%LAUNCH_LOG%"
start "" "https://www.python.org/downloads/windows/"
exit /b 1

:python_version_error
>>"%LAUNCH_LOG%" echo ERROR: Python 3.9 or newer is required, or Python failed to start.
start "" notepad.exe "%LAUNCH_LOG%"
exit /b 1

:dependency_error
>>"%LAUNCH_LOG%" echo ERROR: Dependency installation failed.
start "" notepad.exe "%LAUNCH_LOG%"
exit /b 1

:app_error
>>"%LAUNCH_LOG%" echo ERROR: The app exited with code %APP_EXIT%.
start "" notepad.exe "%LAUNCH_LOG%"
exit /b %APP_EXIT%
