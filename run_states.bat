@echo off
rem Download E-Verify employer CSVs by state, logging to logs\.
rem
rem Usage (double-click, or from cmd / PowerShell):
rem   run_states.bat                    all states
rem   run_states.bat MN CA TX           just these
rem   run_states.bat --skip-existing    any everify_states.py options pass through

setlocal
cd /d "%~dp0"

rem Locale-independent timestamp for the log file name
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd_HH-mm-ss"') do set TS=%%i
set LOG=logs\everify_states_%TS%.log

python -u everify_states.py %* --log "%LOG%"
set STATUS=%ERRORLEVEL%

echo.
echo Exit code %STATUS%. Log saved to %LOG%
rem Keep the window open when double-clicked
echo %CMDCMDLINE% | find /i "/c" >nul && pause
exit /b %STATUS%
