@echo off
rem Nur Messwerte anzeigen, keine Tasten druecken (zum Einstellen der Schwelle).
cd /d "%~dp0"
call start.bat --kalibrieren %*
