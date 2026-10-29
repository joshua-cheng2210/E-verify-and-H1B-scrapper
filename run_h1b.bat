@echo off
rem Download H-1B employer CSVs by state into ...\Claude\Projects\CS\H1B, logging to logs\.
rem
rem Usage (double-click, or from cmd / PowerShell):
rem   run_h1b.bat                     all states, FY2009-latest
rem   run_h1b.bat MN CA               just these states
rem   run_h1b.bat --from-year 2020    any h1b_states.py options pass through

setlocal
cd /d "%~dp0"

rem Locale-independent timestamp for the log file name
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd_HH-mm-ss"') do set TS=%%i
set LOG=logs\h1b_states_%TS%.log

python -u h1b_states.py %* --log "%LOG%"
set STATUS=%ERRORLEVEL%

echo.
echo Exit code %STATUS%. Log saved to %LOG%
rem Keep the window open when double-clicked
echo %CMDCMDLINE% | find /i "/c" >nul && pause
exit /b %STATUS%
