@echo off
rem WowWalker starten (Doppelklick).
rem Weitere Optionen werden durchgereicht, z.B.:  start.bat --neue-adresse
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel% equ 0 (
    py -3 wowwalker.py %*
) else (
    python wowwalker.py %*
)
echo.
pause
