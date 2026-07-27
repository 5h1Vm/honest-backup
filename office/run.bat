@echo off
REM HonestBackup - office copy. Double-click, or run from Task Scheduler.
setlocal
cd /d "%~dp0"

python pull.py %*
set RESULT=%ERRORLEVEL%

echo.
if %RESULT% NEQ 0 (
    echo   Something needs attention - read the message above.
) else (
    echo   Finished.
)

REM Keep the window open when double-clicked, but not under Task Scheduler.
if "%1"=="" pause
exit /b %RESULT%
