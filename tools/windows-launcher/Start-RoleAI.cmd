@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Launcher.ps1" %*
set "result=%errorlevel%"
echo.
if not "%result%"=="0" echo Role-AI startup failed. See the error above.
pause
exit /b %result%
